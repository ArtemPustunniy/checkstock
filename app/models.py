"""Таблицы CheckStock.

- `movements` — журнал проводок, источник истины и история операций.
- `stock_balances` — материализованные остатки ФФ (свободно / резерв). Обновляются в той же
  транзакции, что и проводка; CHECK-ограничения не дают уйти в минус даже при ошибке в коде.
- `documents` — операции (приход, перемещение, FBO, FBS) со статусами; CHECK `kind_shape`
  запрещает несовместимые сочетания типа, статуса и складов.
- `warehouses` удаляются мягко (`deleted_at`): остатки и история остаются на месте.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain import Action, Bucket, DocKind, DocStatus

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Enum как VARCHAR + CHECK: проще мигрировать, чем нативный тип Postgres."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


Timestamp = DateTime(timezone=True)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))


class Warehouse(Base):
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(Timestamp)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def label(self) -> str:
        return f"{self.name} (удалён)" if self.is_deleted else self.name


# Имя уникально только среди действующих ФФ: удалённый «Север» не мешает завести новый «Север».
Index(
    "uq_warehouses_active_name",
    func.lower(Warehouse.name),
    unique=True,
    postgresql_where=Warehouse.deleted_at.is_(None),
)


class WarehouseEvent(Base):
    """Журнал действий со складами: создан, переименован, удалён, восстановлен."""

    __tablename__ = "warehouse_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    event: Mapped[str] = mapped_column(String(16))
    old_name: Mapped[str | None] = mapped_column(String(100))
    new_name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class StockBalance(Base):
    __tablename__ = "stock_balances"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), primary_key=True)
    available: Mapped[int] = mapped_column(Integer, server_default="0")
    reserved: Mapped[int] = mapped_column(Integer, server_default="0")

    __table_args__ = (
        CheckConstraint("available >= 0", name="available_non_negative"),
        CheckConstraint("reserved >= 0", name="reserved_non_negative"),
    )

    @property
    def on_hand(self) -> int:
        return self.available + self.reserved


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[DocKind] = mapped_column(str_enum(DocKind, "doc_kind"))
    status: Mapped[DocStatus] = mapped_column(str_enum(DocStatus, "doc_status"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty: Mapped[int] = mapped_column(Integer)
    source_warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"))
    dest_warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"))
    reference: Mapped[str | None] = mapped_column(String(100))  # номер заказа / накладной
    destination: Mapped[str | None] = mapped_column(String(100))  # склад маркетплейса для FBO
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint(
            """
            (kind = 'receipt' AND status = 'done'
                AND source_warehouse_id IS NULL AND dest_warehouse_id IS NOT NULL)
            OR (kind = 'transfer' AND status IN ('in_transit', 'received')
                AND source_warehouse_id IS NOT NULL AND dest_warehouse_id IS NOT NULL
                AND source_warehouse_id <> dest_warehouse_id)
            OR (kind = 'fbo' AND status IN ('in_transit', 'accepted')
                AND source_warehouse_id IS NOT NULL AND dest_warehouse_id IS NULL)
            OR (kind = 'fbs' AND status IN ('reserved', 'shipped', 'delivered', 'cancelled')
                AND source_warehouse_id IS NOT NULL AND dest_warehouse_id IS NULL)
            """,
            name="kind_shape",
        ),
        Index("ix_documents_kind_status", "kind", "status"),
    )


class Movement(Base):
    """Проводка: `qty` единиц товара из места `from_*` в место `to_*` по документу."""

    __tablename__ = "movements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    action: Mapped[Action] = mapped_column(str_enum(Action, "movement_action"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    qty: Mapped[int] = mapped_column(Integer)
    from_bucket: Mapped[Bucket] = mapped_column(str_enum(Bucket, "from_bucket"))
    from_warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"))
    to_bucket: Mapped[Bucket] = mapped_column(str_enum(Bucket, "to_bucket"))
    to_warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"))
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())

    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        # склад указан ровно у мест, привязанных к ФФ
        CheckConstraint(
            "(from_bucket IN ('available', 'reserved')) = (from_warehouse_id IS NOT NULL)",
            name="from_warehouse_matches_bucket",
        ),
        CheckConstraint(
            "(to_bucket IN ('available', 'reserved')) = (to_warehouse_id IS NOT NULL)",
            name="to_warehouse_matches_bucket",
        ),
    )


class IdempotencyKey(Base):
    """Ответ на уже выполненный запрос: повтор с тем же ключом вернёт его, ничего не проводя."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(50))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer, server_default="0")
    response: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
