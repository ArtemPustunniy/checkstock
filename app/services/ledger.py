"""Проводки и блокировки — единственное место, где меняются остатки.

Порядок блокировок везде одинаковый, поэтому взаимных блокировок нет:
документ (FOR UPDATE) → товар (FOR UPDATE) → склад (FOR SHARE).
Блокировка строки товара сериализует все операции с одним артикулом: пока одна
транзакция проверяет и списывает остаток, вторая ждёт и потом видит уже новые цифры.
"""

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain import FF_BUCKETS, Action, Bucket, DocKind
from app.errors import NotFound
from app.models import Document, Movement, Product, StockBalance, Warehouse


@dataclass(frozen=True)
class Place:
    bucket: Bucket
    warehouse_id: int | None = None  # только для мест на ФФ (свободно / резерв)


def product_by_sku(session: Session, sku: str) -> Product:
    product = session.scalar(select(Product).where(Product.sku == sku))
    if product is None:
        raise NotFound(f"Артикул «{sku}» не найден")
    return product


def lock_product(session: Session, product_id: int) -> Product:
    product = session.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise NotFound(f"Товар #{product_id} не найден")
    return product


def lock_warehouse(session: Session, warehouse_id: int, *, for_update: bool = False) -> Warehouse:
    """FOR SHARE — для операций, которым нужен действующий ФФ-получатель;
    FOR UPDATE — для удаления/переименования. Так удаление не проскочит между проверкой
    «склад не удалён» и записью прихода на него."""
    query = select(Warehouse).where(Warehouse.id == warehouse_id)
    query = query.with_for_update() if for_update else query.with_for_update(read=True)
    warehouse = session.scalar(query)
    if warehouse is None:
        raise NotFound(f"ФФ #{warehouse_id} не найден")
    return warehouse


def balances(session: Session, product_id: int) -> dict[int, StockBalance]:
    # populate_existing: остатки меняются UPDATE-ом в обход ORM, поэтому всегда перечитываем из БД
    query = select(StockBalance).where(StockBalance.product_id == product_id).execution_options(populate_existing=True)
    return {row.warehouse_id: row for row in session.scalars(query)}


def ff_place(bucket: Bucket, doc: Document, *, incoming: bool) -> Place:
    """Место для проводки документа. Входящий товар прихода и перемещения попадает на ФФ
    назначения, всё остальное происходит на ФФ отправления."""
    if bucket not in FF_BUCKETS:
        return Place(bucket)
    if incoming and doc.kind in (DocKind.RECEIPT, DocKind.TRANSFER):
        return Place(bucket, doc.dest_warehouse_id)
    return Place(bucket, doc.source_warehouse_id)


def post(session: Session, doc: Document, action: Action, src: Place, dst: Place) -> Movement:
    """Перенести весь `doc.qty` из `src` в `dst`: обновить остатки ФФ и записать проводку.
    Вызывающий код уже проверил остаток под блокировкой товара; CHECK в БД — страховка."""
    for place, delta in ((src, -doc.qty), (dst, doc.qty)):
        if place.bucket in FF_BUCKETS:
            _shift(session, doc.product_id, place, delta)
    movement = Movement(
        document_id=doc.id,
        action=action,
        product_id=doc.product_id,
        qty=doc.qty,
        from_bucket=src.bucket,
        from_warehouse_id=src.warehouse_id,
        to_bucket=dst.bucket,
        to_warehouse_id=dst.warehouse_id,
    )
    session.add(movement)
    return movement


def _shift(session: Session, product_id: int, place: Place, delta: int) -> None:
    column = StockBalance.available if place.bucket is Bucket.AVAILABLE else StockBalance.reserved
    session.execute(
        insert(StockBalance).values(product_id=product_id, warehouse_id=place.warehouse_id).on_conflict_do_nothing()
    )
    session.execute(
        update(StockBalance)
        .where(StockBalance.product_id == product_id, StockBalance.warehouse_id == place.warehouse_id)
        .values({column.key: column + delta})
    )
