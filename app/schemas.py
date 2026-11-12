"""Pydantic-схемы запросов и ответов API (они же — документация в /docs)."""

from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Qty = Annotated[int, Field(gt=0, le=1_000_000, description="Количество, шт.")]
Sku = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- запросы ----------


class WarehouseIn(Strict):
    name: Text


class ReceiptLineIn(Strict):
    sku: Sku
    warehouse_id: int
    qty: Qty


class ReceiptIn(Strict):
    lines: list[ReceiptLineIn] = Field(min_length=1, max_length=200)
    reference: Text | None = Field(None, description="Номер накладной поставщика")


class AllocationIn(Strict):
    warehouse_id: int
    qty: Qty


class DispatchIn(Strict):
    """Откуда взять товар: один ФФ (`warehouse_id`) или явное распределение (`allocations`) —
    его UI присылает, когда менеджер выбрал вариант после нехватки."""

    sku: Sku
    qty: Qty
    warehouse_id: int | None = None
    allocations: list[AllocationIn] | None = Field(None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def one_source(self) -> Self:
        if (self.warehouse_id is None) == (self.allocations is None):
            raise ValueError("Укажите либо warehouse_id, либо allocations")
        if self.allocations is not None:
            if sum(a.qty for a in self.allocations) != self.qty:
                raise ValueError("Сумма allocations должна быть равна qty")
            if len({a.warehouse_id for a in self.allocations}) != len(self.allocations):
                raise ValueError("ФФ в allocations не должны повторяться")
        return self

    def takes(self) -> list[tuple[int, int]]:
        if self.allocations is not None:
            return [(a.warehouse_id, a.qty) for a in self.allocations]
        return [(self.warehouse_id, self.qty)]


class TransferIn(DispatchIn):
    dest_warehouse_id: int


class FboIn(DispatchIn):
    destination: Text = Field(description="Склад маркетплейса, например «WB Коледино»")


class FbsIn(DispatchIn):
    order_no: Text = Field(description="Номер заказа маркетплейса")


class DemoResetIn(Strict):
    with_receipt: bool = Field(False, description="Сразу провести тестовый приход из задания")


# ---------- ответы ----------


class WarehouseRef(BaseModel):
    id: int
    name: str
    deleted: bool


class DocumentOut(BaseModel):
    id: int
    kind: str
    kind_label: str
    status: str
    status_label: str
    sku: str
    product_name: str
    qty: int
    source: WarehouseRef | None
    dest: WarehouseRef | None
    reference: str | None
    destination: str | None
    route: str
    created_at: datetime
    updated_at: datetime


class OperationOut(BaseModel):
    documents: list[DocumentOut]
    message: str


class StepOut(BaseModel):
    document: DocumentOut
    changed: bool = Field(description="false — шаг уже был выполнен, повтор ничего не провёл")
    message: str


class Cell(BaseModel):
    on_hand: int = Field(description="Физически на ФФ")
    available: int = Field(description="Доступно")
    reserved: int = Field(description="В резерве FBS")


class StockRow(BaseModel):
    sku: str
    name: str
    cells: list[Cell] = Field(description="По ФФ, в порядке списка warehouses")
    on_hand: int
    available: int
    reserved: int
    transit_ff: int = Field(description="В пути между ФФ")
    transit_fbo: int = Field(description="В пути на склад МП (FBO)")
    transit_fbs: int = Field(description="В пути к клиенту (FBS)")
    tracked: int = Field(description="Всего на учёте: на ФФ + в пути")


class StockTotals(BaseModel):
    cells: list[Cell]
    on_hand: int
    available: int
    reserved: int
    transit_ff: int
    transit_fbo: int
    transit_fbs: int
    tracked: int


class StockOut(BaseModel):
    warehouses: list[WarehouseRef]
    rows: list[StockRow]
    totals: StockTotals


class PlaceOut(BaseModel):
    kind: str = Field(description="ff | reserve | transit_ff | transit_fbo | transit_fbs")
    label: str
    qty: int
    available: int | None
    reserved: int | None
    warehouse: WarehouseRef | None
    document_id: int | None


class WhereOut(BaseModel):
    sku: str
    name: str
    places: list[PlaceOut]
    on_hand: int
    in_transit: int
    accepted_by_marketplace: int
    delivered_to_clients: int


class HistoryItem(BaseModel):
    id: int
    at: datetime
    action: str
    action_label: str
    document_id: int
    kind_label: str
    sku: str
    product_name: str
    qty: int
    from_label: str
    to_label: str
    reference: str | None


class SkuStock(BaseModel):
    sku: str
    on_hand: int
    reserved: int


class WarehouseEventOut(BaseModel):
    event: str
    old_name: str | None
    new_name: str | None
    at: datetime


class WarehouseOut(BaseModel):
    id: int
    name: str
    deleted: bool
    created_at: datetime
    deleted_at: datetime | None
    on_hand: int
    available: int
    reserved: int
    inbound_transit: int
    skus: list[SkuStock]
    events: list[WarehouseEventOut]


class WarehouseChangeOut(BaseModel):
    warehouse: WarehouseOut
    changed: bool
    message: str


class ProductOut(BaseModel):
    id: int
    sku: str
    name: str


class ReconcileOut(BaseModel):
    ok: bool
    received: int
    on_ff: int
    in_transit: int
    left: int = Field(description="Выбыло из учёта ФФ: принято МП + доставлено клиентам")
    mismatches: list[str]
