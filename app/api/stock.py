from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import SessionDep
from app.config import settings
from app.domain import DocKind
from app.models import Product
from app.schemas import HistoryItem, ProductOut, ReconcileOut, StockOut, WhereOut
from app.seed import STORE
from app.services import queries

router = APIRouter(prefix="/api", tags=["Остатки и история"])


@router.get("/stock", response_model=StockOut, summary="Остатки по артикулам и ФФ, товар в пути")
def stock(session: SessionDep):
    return queries.stock_overview(session)


@router.get("/stock/{sku}", response_model=WhereOut, summary="Где числится товар")
def where_is(sku: str, session: SessionDep):
    return queries.where_is(session, sku)


@router.get("/history", response_model=list[HistoryItem], summary="История операций: что, сколько, откуда и куда")
def history(
    session: SessionDep,
    sku: str | None = None,
    warehouse_id: int | None = None,
    kind: DocKind | None = None,
    limit: int = Query(200, ge=1, le=1000),
):
    return queries.history(session, sku=sku, warehouse_id=warehouse_id, kind=kind, limit=limit)


@router.get("/reconcile", response_model=ReconcileOut, summary="Сверка: остатки сходятся с журналом проводок")
def reconcile(session: SessionDep):
    return queries.reconcile(session)


@router.get("/products", response_model=list[ProductOut], summary="Товары")
def products(session: SessionDep):
    return [{"id": p.id, "sku": p.sku, "name": p.name} for p in session.scalars(select(Product).order_by(Product.sku))]


@router.get("/meta", summary="Магазин и режим работы", include_in_schema=False)
def meta():
    return {"store": STORE, "demo_mode": settings.demo_mode}
