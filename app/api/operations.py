"""Операции с товаром. Создание — через идемпотентную обёртку, шаги документов — через advance()."""

from typing import Any

from fastapi import APIRouter

from app.api.deps import IdempotencyKeyHeader, SessionDep, plural
from app.domain import Action, DocKind
from app.models import Document
from app.schemas import DispatchIn, DocumentOut, FboIn, FbsIn, OperationOut, ReceiptIn, StepOut, TransferIn
from app.services import idempotency, operations, queries

router = APIRouter(prefix="/api", tags=["Операции"])


def _takes(body: DispatchIn) -> list[operations.Take]:
    return [operations.Take(warehouse_id, qty) for warehouse_id, qty in body.takes()]


def _spread(views: list[dict[str, Any]]) -> str:
    return ", ".join(f"{v['qty']} шт. с «{v['source']['name']}»" for v in views)


def _result(session, docs: list[Document], message) -> dict[str, Any]:
    views = queries.documents_view(session, docs)
    return {"documents": views, "message": message(views)}


@router.post("/receipts", response_model=OperationOut, status_code=201, summary="Приход на ФФ")
def create_receipt(body: ReceiptIn, session: SessionDep, idempotency_key: IdempotencyKeyHeader = None):
    def run():
        lines = [operations.ReceiptLine(line.sku, line.warehouse_id, line.qty) for line in body.lines]
        docs = operations.receive(session, lines, body.reference)
        total = sum(doc.qty for doc in docs)
        return _result(
            session,
            docs,
            lambda v: f"Приход проведён: {len(v)} {plural(len(v), ('строка', 'строки', 'строк'))}, {total} шт.",
        )

    return idempotency.run(session, idempotency_key, "receipts", body, run)


@router.post("/transfers", response_model=OperationOut, status_code=201, summary="Отправить перемещение между ФФ")
def create_transfer(body: TransferIn, session: SessionDep, idempotency_key: IdempotencyKeyHeader = None):
    def run():
        docs = operations.dispatch(
            session, DocKind.TRANSFER, body.sku, _takes(body), dest_warehouse_id=body.dest_warehouse_id
        )
        return _result(
            session,
            docs,
            lambda v: f"{body.sku}: {_spread(v)} отправлено на «{v[0]['dest']['name']}» — в пути до приёмки.",
        )

    return idempotency.run(session, idempotency_key, "transfers", body, run)


@router.post("/fbo", response_model=OperationOut, status_code=201, summary="Отгрузить на склад маркетплейса (FBO)")
def create_fbo(body: FboIn, session: SessionDep, idempotency_key: IdempotencyKeyHeader = None):
    def run():
        docs = operations.dispatch(session, DocKind.FBO, body.sku, _takes(body), destination=body.destination)
        return _result(
            session,
            docs,
            lambda v: f"{body.sku}: {_spread(v)} отгружено на склад МП «{body.destination}» — в пути до приёмки.",
        )

    return idempotency.run(session, idempotency_key, "fbo", body, run)


@router.post("/fbs", response_model=OperationOut, status_code=201, summary="Зарезервировать заказ FBS")
def create_fbs(body: FbsIn, session: SessionDep, idempotency_key: IdempotencyKeyHeader = None):
    def run():
        docs = operations.dispatch(session, DocKind.FBS, body.sku, _takes(body), reference=body.order_no)
        return _result(
            session,
            docs,
            lambda v: f"Заказ {body.order_no}: {body.sku} зарезервирован — {_spread(v)}.",
        )

    return idempotency.run(session, idempotency_key, "fbs", body, run)


# ---------- шаги документов ----------

STEP_MESSAGES = {
    Action.TRANSFER_RECEIVE: (
        "Перемещение №{id} принято на «{dest}»: +{qty} шт. {sku}.",
        "Перемещение №{id} уже принято — повторная приёмка ничего не изменила.",
    ),
    Action.FBO_ACCEPT: (
        "Отгрузка FBO №{id} принята складом МП «{destination}».",
        "Отгрузка FBO №{id} уже принята — повторное подтверждение ничего не изменило.",
    ),
    Action.FBS_SHIP: (
        "Заказ {reference} отгружен: {qty} шт. {sku} в пути к клиенту.",
        "Заказ {reference} уже отгружен — повторная отгрузка ничего не списала.",
    ),
    Action.FBS_CANCEL: (
        "Резерв по заказу {reference} отменён: {qty} шт. {sku} снова доступны на «{source}».",
        "Резерв по заказу {reference} уже отменён.",
    ),
    Action.FBS_DELIVER: (
        "Заказ {reference} доставлен клиенту.",
        "Заказ {reference} уже отмечен доставленным.",
    ),
}


def _step(session, action: Action, doc_id: int) -> dict[str, Any]:
    with session.begin():
        doc, changed = operations.advance(session, action, doc_id)
        view = queries.documents_view(session, [doc])[0]
    done, repeated = STEP_MESSAGES[action]
    message = (done if changed else repeated).format(
        id=view["id"],
        qty=view["qty"],
        sku=view["sku"],
        reference=view["reference"],
        destination=view["destination"],
        source=view["source"]["name"] if view["source"] else "",
        dest=view["dest"]["name"] if view["dest"] else "",
    )
    return {"document": view, "changed": changed, "message": message}


@router.post("/transfers/{doc_id}/receive", response_model=StepOut, summary="Принять перемещение на ФФ назначения")
def receive_transfer(doc_id: int, session: SessionDep):
    return _step(session, Action.TRANSFER_RECEIVE, doc_id)


@router.post("/fbo/{doc_id}/accept", response_model=StepOut, summary="Отметить приёмку FBO на складе МП")
def accept_fbo(doc_id: int, session: SessionDep):
    return _step(session, Action.FBO_ACCEPT, doc_id)


@router.post("/fbs/{doc_id}/ship", response_model=StepOut, summary="Отгрузить заказ FBS")
def ship_fbs(doc_id: int, session: SessionDep):
    return _step(session, Action.FBS_SHIP, doc_id)


@router.post("/fbs/{doc_id}/cancel", response_model=StepOut, summary="Отменить резерв FBS")
def cancel_fbs(doc_id: int, session: SessionDep):
    return _step(session, Action.FBS_CANCEL, doc_id)


@router.post("/fbs/{doc_id}/deliver", response_model=StepOut, summary="Отметить доставку заказа FBS")
def deliver_fbs(doc_id: int, session: SessionDep):
    return _step(session, Action.FBS_DELIVER, doc_id)


@router.get("/documents", response_model=list[DocumentOut], summary="Открытые документы: в пути и в резерве")
def open_documents(session: SessionDep):
    return queries.open_documents(session)
