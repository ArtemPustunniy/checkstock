"""Справочник ФФ: создание, переименование, мягкое удаление и восстановление.

Удаление не стирает склад: он получает `deleted_at`, его остатки и история остаются
и видны с пометкой «удалён». Правило одно — удалённый ФФ ничего не принимает
(приход, перемещение на него), но товар с него можно отгрузить или вывезти.
Удалить нельзя, только пока на склад едут перемещения: им некуда будет приехать.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import DocKind, DocStatus
from app.errors import Conflict, InvalidRequest
from app.models import Document, Warehouse, WarehouseEvent
from app.services.ledger import lock_warehouse


def normalize_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise InvalidRequest("Название ФФ не может быть пустым")
    return cleaned


def create(session: Session, name: str) -> Warehouse:
    name = normalize_name(name)
    _ensure_name_free(session, name)
    warehouse = Warehouse(name=name)
    session.add(warehouse)
    session.flush()
    session.add(WarehouseEvent(warehouse_id=warehouse.id, event="created", new_name=name))
    return warehouse


def rename(session: Session, warehouse_id: int, name: str) -> tuple[Warehouse, bool]:
    name = normalize_name(name)
    warehouse = lock_warehouse(session, warehouse_id, for_update=True)
    if warehouse.is_deleted:
        raise Conflict("Удалённый ФФ нельзя переименовать — сначала восстановите его", code="warehouse_deleted")
    if warehouse.name == name:
        return warehouse, False
    _ensure_name_free(session, name, exclude_id=warehouse.id)
    session.add(WarehouseEvent(warehouse_id=warehouse.id, event="renamed", old_name=warehouse.name, new_name=name))
    warehouse.name = name
    return warehouse, True


def delete(session: Session, warehouse_id: int) -> tuple[Warehouse, bool]:
    warehouse = lock_warehouse(session, warehouse_id, for_update=True)
    if warehouse.is_deleted:
        return warehouse, False
    inbound = session.scalars(
        select(Document.id).where(
            Document.kind == DocKind.TRANSFER,
            Document.status == DocStatus.IN_TRANSIT,
            Document.dest_warehouse_id == warehouse.id,
        )
    ).all()
    if inbound:
        numbers = ", ".join(f"№{doc_id}" for doc_id in inbound)
        raise Conflict(
            f"На ФФ «{warehouse.name}» едут перемещения {numbers}. Примите их, прежде чем удалять склад.",
            code="inbound_transit",
            documents=list(inbound),
        )
    warehouse.deleted_at = datetime.now(UTC)
    session.add(WarehouseEvent(warehouse_id=warehouse.id, event="deleted", old_name=warehouse.name))
    return warehouse, True


def restore(session: Session, warehouse_id: int) -> tuple[Warehouse, bool]:
    warehouse = lock_warehouse(session, warehouse_id, for_update=True)
    if not warehouse.is_deleted:
        return warehouse, False
    _ensure_name_free(session, warehouse.name, exclude_id=warehouse.id)
    warehouse.deleted_at = None
    session.add(WarehouseEvent(warehouse_id=warehouse.id, event="restored", new_name=warehouse.name))
    return warehouse, True


def _ensure_name_free(session: Session, name: str, exclude_id: int | None = None) -> None:
    query = select(Warehouse.id).where(func.lower(Warehouse.name) == name.lower(), Warehouse.deleted_at.is_(None))
    if exclude_id is not None:
        query = query.where(Warehouse.id != exclude_id)
    if session.scalar(query) is not None:
        raise Conflict(f"Действующий ФФ «{name}» уже есть", code="name_taken")
