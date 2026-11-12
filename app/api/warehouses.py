from fastapi import APIRouter

from app.api.deps import SessionDep
from app.schemas import WarehouseChangeOut, WarehouseIn, WarehouseOut
from app.services import queries, warehouses

router = APIRouter(prefix="/api/warehouses", tags=["Склады (ФФ)"])


def _view(session, warehouse_id: int) -> dict:
    return next(w for w in queries.warehouses_view(session) if w["id"] == warehouse_id)


@router.get("", response_model=list[WarehouseOut], summary="ФФ, включая удалённые, с остатками")
def list_warehouses(session: SessionDep):
    return queries.warehouses_view(session)


@router.post("", response_model=WarehouseChangeOut, status_code=201, summary="Добавить ФФ")
def create(body: WarehouseIn, session: SessionDep):
    with session.begin():
        warehouse = warehouses.create(session, body.name)
    return {"warehouse": _view(session, warehouse.id), "changed": True, "message": f"ФФ «{warehouse.name}» добавлен."}


@router.patch("/{warehouse_id}", response_model=WarehouseChangeOut, summary="Переименовать ФФ")
def rename(warehouse_id: int, body: WarehouseIn, session: SessionDep):
    with session.begin():
        warehouse, changed = warehouses.rename(session, warehouse_id, body.name)
    message = (
        f"ФФ переименован в «{warehouse.name}». История и остатки сохранены." if changed else "Название не изменилось."
    )
    return {"warehouse": _view(session, warehouse.id), "changed": changed, "message": message}


@router.delete(
    "/{warehouse_id}", response_model=WarehouseChangeOut, summary="Удалить ФФ (остатки и история сохраняются)"
)
def delete(warehouse_id: int, session: SessionDep):
    with session.begin():
        warehouse, changed = warehouses.delete(session, warehouse_id)
    view = _view(session, warehouse.id)
    if not changed:
        message = f"ФФ «{warehouse.name}» уже удалён."
    elif view["on_hand"]:
        message = (
            f"ФФ «{warehouse.name}» удалён. На нём числится {view['on_hand']} шт. — остатки и история сохранены, "
            "новые поступления на склад закрыты. Вывезти товар можно перемещением."
        )
    else:
        message = f"ФФ «{warehouse.name}» удалён. Остатков на нём не было, история сохранена."
    return {"warehouse": view, "changed": changed, "message": message}


@router.post("/{warehouse_id}/restore", response_model=WarehouseChangeOut, summary="Восстановить удалённый ФФ")
def restore(warehouse_id: int, session: SessionDep):
    with session.begin():
        warehouse, changed = warehouses.restore(session, warehouse_id)
    message = f"ФФ «{warehouse.name}» восстановлен." if changed else f"ФФ «{warehouse.name}» не был удалён."
    return {"warehouse": _view(session, warehouse.id), "changed": changed, "message": message}
