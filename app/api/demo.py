from fastapi import APIRouter

from app.api.deps import SessionDep
from app.config import settings
from app.errors import Conflict
from app.schemas import DemoResetIn
from app.seed import reset

router = APIRouter(prefix="/api/demo", tags=["Демо"])


@router.post("/reset", summary="Сбросить к тестовым данным из задания")
def reset_demo(body: DemoResetIn, session: SessionDep):
    if not settings.demo_mode:
        raise Conflict("Сброс данных выключен (DEMO_MODE=false)", code="demo_disabled")
    with session.begin():
        reset(session, with_receipt=body.with_receipt)
    suffix = " и проведён тестовый приход" if body.with_receipt else ", остатков нет"
    return {"message": f"Данные сброшены: товары и ФФ из задания{suffix}."}
