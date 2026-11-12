"""Складские операции: приход, отправки (перемещение / FBO / резерв FBS) и шаги документов.

Каждая функция выполняется внутри транзакции, которую открывает вызывающий код:
либо операция проводится целиком, либо не меняется ничего.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import CREATION, FF_BUCKETS, KIND_LABELS, STATUS_LABELS, STEPS, Action, Bucket, DocKind
from app.errors import Conflict, InvalidRequest, NotFound, Shortage
from app.models import Document, Product, StockBalance, Warehouse
from app.services import allocation
from app.services.ledger import Place, balances, ff_place, lock_product, lock_warehouse, post, product_by_sku


@dataclass(frozen=True)
class ReceiptLine:
    sku: str
    warehouse_id: int
    qty: int


@dataclass(frozen=True)
class Take:
    """Сколько взять с какого ФФ. Одна отправка может забрать товар с нескольких ФФ."""

    warehouse_id: int
    qty: int


def receive(session: Session, lines: list[ReceiptLine], reference: str | None = None) -> list[Document]:
    """Приход от поставщика: каждая строка — отдельный документ на свой ФФ."""
    products = {line.sku: product_by_sku(session, line.sku) for line in lines}
    for product in sorted(products.values(), key=lambda p: p.id):  # единый порядок блокировок
        lock_product(session, product.id)
    targets = {wh_id: lock_warehouse(session, wh_id) for wh_id in sorted({line.warehouse_id for line in lines})}
    for warehouse in targets.values():
        if warehouse.is_deleted:
            raise Conflict(f"ФФ «{warehouse.name}» удалён — оформить приход на него нельзя", code="warehouse_deleted")

    creation = CREATION[DocKind.RECEIPT]
    docs = []
    for line in lines:
        doc = Document(
            kind=DocKind.RECEIPT,
            status=creation.status,
            product_id=products[line.sku].id,
            qty=line.qty,
            dest_warehouse_id=line.warehouse_id,
            reference=reference,
        )
        session.add(doc)
        session.flush()
        post(session, doc, creation.action, Place(creation.src), Place(creation.dst, line.warehouse_id))
        docs.append(doc)
    return docs


def dispatch(
    session: Session,
    kind: DocKind,
    sku: str,
    takes: list[Take],
    *,
    dest_warehouse_id: int | None = None,
    reference: str | None = None,
    destination: str | None = None,
) -> list[Document]:
    """Забрать товар со свободного остатка ФФ: в перемещение, в отгрузку FBO или в резерв FBS.

    Если на каком-то ФФ доступного остатка меньше, чем нужно, ничего не проводится:
    поднимается Shortage с раскладом по складам и вариантами, откуда можно взять.
    """
    if kind not in (DocKind.TRANSFER, DocKind.FBO, DocKind.FBS):
        raise ValueError(f"dispatch не поддерживает {kind}")

    product = product_by_sku(session, sku)
    lock_product(session, product.id)
    warehouses = {w.id: w for w in session.scalars(select(Warehouse))}
    for take in takes:
        if take.warehouse_id not in warehouses:
            raise NotFound(f"ФФ #{take.warehouse_id} не найден")

    excluded: set[int] = set()
    if kind is DocKind.TRANSFER:
        if dest_warehouse_id is None:
            raise InvalidRequest("Укажите ФФ назначения")
        dest = lock_warehouse(session, dest_warehouse_id)
        if dest.is_deleted:
            raise Conflict(f"ФФ «{dest.name}» удалён — отправить на него товар нельзя", code="warehouse_deleted")
        if any(take.warehouse_id == dest.id for take in takes):
            raise InvalidRequest("ФФ отправления и назначения должны различаться")
        excluded = {dest.id}

    stock = balances(session, product.id)
    if any(_available(stock, take.warehouse_id) < take.qty for take in takes):
        raise _shortage(product, takes, stock, warehouses, excluded)

    creation = CREATION[kind]
    docs = []
    for take in takes:
        doc = Document(
            kind=kind,
            status=creation.status,
            product_id=product.id,
            qty=take.qty,
            source_warehouse_id=take.warehouse_id,
            dest_warehouse_id=dest_warehouse_id if kind is DocKind.TRANSFER else None,
            reference=reference,
            destination=destination,
        )
        session.add(doc)
        session.flush()
        dst_wh = take.warehouse_id if creation.dst in FF_BUCKETS else None
        post(session, doc, creation.action, Place(Bucket.AVAILABLE, take.warehouse_id), Place(creation.dst, dst_wh))
        docs.append(doc)
    return docs


def advance(session: Session, action: Action, doc_id: int) -> tuple[Document, bool]:
    """Следующий шаг документа: приёмка, отгрузка, отмена, доставка.

    Возвращает (документ, changed). Повторное подтверждение уже сделанного шага — не ошибка,
    а no-op с changed=False: строка документа заблокирована FOR UPDATE, поэтому два
    одновременных «отгрузить» выполнятся по очереди, и второй увидит статус «отгружен».
    """
    step = STEPS[action]
    doc = session.scalar(
        select(Document).where(Document.id == doc_id).with_for_update().execution_options(populate_existing=True)
    )
    if doc is None or doc.kind != step.kind:
        raise NotFound(f"{KIND_LABELS[step.kind]} №{doc_id} не найден")
    if doc.status in step.done:
        return doc, False
    if doc.status != step.from_status:
        raise Conflict(
            f"Нельзя {step.verb}: {KIND_LABELS[doc.kind].lower()} №{doc.id} — «{STATUS_LABELS[doc.status]}»",
            code="invalid_status",
            status=doc.status.value,
        )

    lock_product(session, doc.product_id)
    if action is Action.TRANSFER_RECEIVE:
        # удаление ФФ запрещено, пока на него едут перемещения, — проверяем на всякий случай
        dest = lock_warehouse(session, doc.dest_warehouse_id)
        if dest.is_deleted:
            raise Conflict(f"ФФ «{dest.name}» удалён — принять на него товар нельзя", code="warehouse_deleted")

    post(session, doc, action, ff_place(step.src, doc, incoming=False), ff_place(step.dst, doc, incoming=True))
    doc.status = step.to_status
    session.flush()
    return doc, True


def _available(stock: dict[int, StockBalance], warehouse_id: int) -> int:
    row = stock.get(warehouse_id)
    return row.available if row else 0


def _shortage(
    product: Product,
    takes: list[Take],
    stock: dict[int, StockBalance],
    warehouses: dict[int, Warehouse],
    excluded: set[int],
) -> Shortage:
    requested = sum(take.qty for take in takes)
    available = {wh: row.available for wh, row in stock.items() if wh not in excluded}
    total = sum(available.values())
    chosen = takes[0].warehouse_id if len(takes) == 1 else None

    if chosen is not None:
        row = stock.get(chosen)
        have, held = (row.available, row.reserved) if row else (0, 0)
        message = f"На ФФ «{warehouses[chosen].label}» доступно {have} шт. {product.sku}, нужно {requested}."
        if held:
            message += f" Ещё {held} шт. там в резерве FBS — их взять нельзя."
    else:
        message = f"Остатки {product.sku} изменились: выбранное распределение больше не помещается."

    options = allocation.suggest(requested, available, chosen)
    if options:
        message += f" На других ФФ хватает: всего доступно {total} шт. Выберите, откуда взять."
        code = "shortage"
    else:
        message += f" Всего на ФФ доступно {total} шт. — уменьшите количество или оформите приход."
        code = "shortage_total"

    return Shortage(
        message,
        code=code,
        sku=product.sku,
        requested=requested,
        total_available=total,
        warehouses=[
            {
                "id": wh.id,
                "name": wh.name,
                "deleted": wh.is_deleted,
                "available": stock[wh.id].available if wh.id in stock else 0,
                "reserved": stock[wh.id].reserved if wh.id in stock else 0,
            }
            for wh in sorted(warehouses.values(), key=lambda w: w.id)
            if wh.id not in excluded and (not wh.is_deleted or wh.id in stock)
        ],
        options=[
            {
                "label": " + ".join(f"{qty} шт. с «{warehouses[wh].label}»" for wh, qty in option),
                "allocations": [{"warehouse_id": wh, "qty": qty} for wh, qty in option],
            }
            for option in options
        ],
    )
