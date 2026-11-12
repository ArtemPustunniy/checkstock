"""Чтение: экран остатков, «где товар», история, открытые документы, сверка учёта."""

from collections import defaultdict
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.domain import (
    ACTION_LABELS,
    IN_TRANSIT_STATUS,
    KIND_LABELS,
    OPEN_STATUSES,
    STATUS_LABELS,
    Bucket,
    DocKind,
    DocStatus,
)
from app.models import Document, Movement, Product, StockBalance, Warehouse, WarehouseEvent
from app.services.ledger import product_by_sku

# ---------- общие представления ----------


def warehouse_ref(warehouse: Warehouse | None) -> dict[str, Any] | None:
    if warehouse is None:
        return None
    return {"id": warehouse.id, "name": warehouse.name, "deleted": warehouse.is_deleted}


def route_label(doc: Document, warehouses: dict[int, Warehouse]) -> str:
    src = warehouses[doc.source_warehouse_id].label if doc.source_warehouse_id else None
    dst = warehouses[doc.dest_warehouse_id].label if doc.dest_warehouse_id else None
    match doc.kind:
        case DocKind.RECEIPT:
            return f"Поставщик → {dst}"
        case DocKind.TRANSFER:
            return f"{src} → {dst}"
        case DocKind.FBO:
            return f"{src} → склад МП «{doc.destination}»"
        case DocKind.FBS:
            return f"{src} → клиент, заказ {doc.reference}"


def document_view(doc: Document, products: dict[int, Product], warehouses: dict[int, Warehouse]) -> dict[str, Any]:
    product = products[doc.product_id]
    return {
        "id": doc.id,
        "kind": doc.kind.value,
        "kind_label": KIND_LABELS[doc.kind],
        "status": doc.status.value,
        "status_label": STATUS_LABELS[doc.status],
        "sku": product.sku,
        "product_name": product.name,
        "qty": doc.qty,
        "source": warehouse_ref(warehouses.get(doc.source_warehouse_id)),
        "dest": warehouse_ref(warehouses.get(doc.dest_warehouse_id)),
        "reference": doc.reference,
        "destination": doc.destination,
        "route": route_label(doc, warehouses),
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
    }


def lookup(session: Session) -> tuple[dict[int, Product], dict[int, Warehouse]]:
    products = {p.id: p for p in session.scalars(select(Product))}
    warehouses = {w.id: w for w in session.scalars(select(Warehouse))}
    return products, warehouses


def documents_view(session: Session, docs: list[Document]) -> list[dict[str, Any]]:
    products, warehouses = lookup(session)
    return [document_view(doc, products, warehouses) for doc in docs]


def _in_transit_filter():
    return or_(*((Document.kind == kind) & (Document.status == status) for kind, status in IN_TRANSIT_STATUS.items()))


# ---------- экран остатков ----------

TRANSIT_KEY = {DocKind.TRANSFER: "transit_ff", DocKind.FBO: "transit_fbo", DocKind.FBS: "transit_fbs"}


def stock_overview(session: Session) -> dict[str, Any]:
    products = session.scalars(select(Product).order_by(Product.sku)).all()
    warehouses = session.scalars(select(Warehouse).order_by(Warehouse.id)).all()
    cells = {(b.product_id, b.warehouse_id): b for b in session.scalars(select(StockBalance))}

    # удалённый ФФ показываем, пока на нём что-то числится, — иначе товар «потеряется» с экрана
    holding = {wh_id for (_, wh_id), b in cells.items() if b.on_hand > 0}
    columns = [w for w in warehouses if not w.is_deleted or w.id in holding]

    transit: dict[tuple[int, str], int] = defaultdict(int)
    for doc in session.scalars(select(Document).where(_in_transit_filter())):
        transit[(doc.product_id, TRANSIT_KEY[doc.kind])] += doc.qty

    rows = []
    for product in products:
        row_cells = []
        for wh in columns:
            b = cells.get((product.id, wh.id))
            available, reserved = (b.available, b.reserved) if b else (0, 0)
            row_cells.append({"on_hand": available + reserved, "available": available, "reserved": reserved})
        row = {
            "sku": product.sku,
            "name": product.name,
            "cells": row_cells,
            "on_hand": sum(c["on_hand"] for c in row_cells),
            "available": sum(c["available"] for c in row_cells),
            "reserved": sum(c["reserved"] for c in row_cells),
            **{key: transit[(product.id, key)] for key in TRANSIT_KEY.values()},
        }
        row["tracked"] = row["on_hand"] + row["transit_ff"] + row["transit_fbo"] + row["transit_fbs"]
        rows.append(row)

    totals = {
        "cells": [
            {key: sum(r["cells"][i][key] for r in rows) for key in ("on_hand", "available", "reserved")}
            for i in range(len(columns))
        ],
        **{
            key: sum(r[key] for r in rows)
            for key in ("on_hand", "available", "reserved", "transit_ff", "transit_fbo", "transit_fbs", "tracked")
        },
    }
    return {"warehouses": [warehouse_ref(w) for w in columns], "rows": rows, "totals": totals}


def where_is(session: Session, sku: str) -> dict[str, Any]:
    """Где числится товар: на каких ФФ, в каких резервах и в пути по каким документам."""
    product = product_by_sku(session, sku)
    _, warehouses = lookup(session)
    places: list[dict[str, Any]] = []

    for b in session.scalars(
        select(StockBalance).where(StockBalance.product_id == product.id).order_by(StockBalance.warehouse_id)
    ):
        if b.on_hand == 0:
            continue
        wh = warehouses[b.warehouse_id]
        places.append(
            {
                "kind": "ff",
                "label": f"ФФ «{wh.label}»",
                "qty": b.on_hand,
                "available": b.available,
                "reserved": b.reserved,
                "warehouse": warehouse_ref(wh),
                "document_id": None,
            }
        )

    open_docs = session.scalars(
        select(Document)
        .where(Document.product_id == product.id, Document.status.in_(OPEN_STATUSES))
        .order_by(Document.id)
    )
    labels = {
        (
            DocKind.FBS,
            DocStatus.RESERVED,
        ): lambda d: f"Резерв FBS на «{warehouses[d.source_warehouse_id].label}», заказ {d.reference}",
        (
            DocKind.TRANSFER,
            DocStatus.IN_TRANSIT,
        ): lambda d: f"В пути «{warehouses[d.source_warehouse_id].label}» → «{warehouses[d.dest_warehouse_id].label}»",
        (DocKind.FBO, DocStatus.IN_TRANSIT): lambda d: f"В пути на склад МП «{d.destination}»",
        (DocKind.FBS, DocStatus.SHIPPED): lambda d: f"В пути к клиенту, заказ {d.reference}",
    }
    kinds = {
        (DocKind.FBS, DocStatus.RESERVED): "reserve",
        (DocKind.TRANSFER, DocStatus.IN_TRANSIT): "transit_ff",
        (DocKind.FBO, DocStatus.IN_TRANSIT): "transit_fbo",
        (DocKind.FBS, DocStatus.SHIPPED): "transit_fbs",
    }
    for doc in open_docs:
        key = (doc.kind, doc.status)
        places.append(
            {
                "kind": kinds[key],
                "label": labels[key](doc),
                "qty": doc.qty,
                "available": None,
                "reserved": None,
                "warehouse": warehouse_ref(warehouses.get(doc.source_warehouse_id)),
                "document_id": doc.id,
            }
        )

    gone = session.execute(
        text("""
            SELECT
                COALESCE(SUM(qty) FILTER (WHERE kind = 'fbo' AND status = 'accepted'), 0) AS marketplace,
                COALESCE(SUM(qty) FILTER (WHERE kind = 'fbs' AND status = 'delivered'), 0) AS client
            FROM documents WHERE product_id = :product_id
        """),
        {"product_id": product.id},
    ).one()
    on_hand = sum(p["qty"] for p in places if p["kind"] == "ff")
    in_transit = sum(p["qty"] for p in places if p["kind"].startswith("transit"))
    return {
        "sku": product.sku,
        "name": product.name,
        "places": places,
        "on_hand": on_hand,
        "in_transit": in_transit,
        "accepted_by_marketplace": gone.marketplace,
        "delivered_to_clients": gone.client,
    }


# ---------- история и документы ----------


def _place_label(bucket: Bucket, warehouse_id: int | None, doc: Document, warehouses: dict[int, Warehouse]) -> str:
    match bucket:
        case Bucket.SUPPLIER:
            return "Поставщик"
        case Bucket.AVAILABLE:
            return f"{warehouses[warehouse_id].label} · свободно"
        case Bucket.RESERVED:
            return f"{warehouses[warehouse_id].label} · резерв FBS"
        case Bucket.TRANSIT_FF:
            return f"В пути {warehouses[doc.source_warehouse_id].label} → {warehouses[doc.dest_warehouse_id].label}"
        case Bucket.TRANSIT_FBO:
            return f"В пути на МП «{doc.destination}»"
        case Bucket.TRANSIT_FBS:
            return f"В пути к клиенту, заказ {doc.reference}"
        case Bucket.MARKETPLACE:
            return f"Склад МП «{doc.destination}»"
        case Bucket.CLIENT:
            return f"Клиент, заказ {doc.reference}"


def history(
    session: Session,
    *,
    sku: str | None = None,
    warehouse_id: int | None = None,
    kind: DocKind | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    query = (
        select(Movement, Document)
        .join(Document, Document.id == Movement.document_id)
        .order_by(Movement.id.desc())
        .limit(limit)
    )
    if sku:
        query = query.where(Movement.product_id == product_by_sku(session, sku).id)
    if warehouse_id is not None:
        query = query.where(
            or_(
                Movement.from_warehouse_id == warehouse_id,
                Movement.to_warehouse_id == warehouse_id,
                Document.source_warehouse_id == warehouse_id,
                Document.dest_warehouse_id == warehouse_id,
            )
        )
    if kind is not None:
        query = query.where(Document.kind == kind)

    products, warehouses = lookup(session)
    items = []
    for movement, doc in session.execute(query):
        product = products[movement.product_id]
        items.append(
            {
                "id": movement.id,
                "at": movement.created_at,
                "action": movement.action.value,
                "action_label": ACTION_LABELS[movement.action],
                "document_id": doc.id,
                "kind_label": KIND_LABELS[doc.kind],
                "sku": product.sku,
                "product_name": product.name,
                "qty": movement.qty,
                "from_label": _place_label(movement.from_bucket, movement.from_warehouse_id, doc, warehouses),
                "to_label": _place_label(movement.to_bucket, movement.to_warehouse_id, doc, warehouses),
                "reference": doc.reference,
            }
        )
    return items


def open_documents(session: Session) -> list[dict[str, Any]]:
    docs = session.scalars(select(Document).where(Document.status.in_(OPEN_STATUSES)).order_by(Document.id)).all()
    return documents_view(session, docs)


def warehouses_view(session: Session) -> list[dict[str, Any]]:
    warehouses = session.scalars(select(Warehouse).order_by(Warehouse.deleted_at.is_not(None), Warehouse.id)).all()
    products = {p.id: p for p in session.scalars(select(Product))}
    stock: dict[int, list[StockBalance]] = defaultdict(list)
    for b in session.scalars(select(StockBalance).order_by(StockBalance.product_id)):
        if b.on_hand:
            stock[b.warehouse_id].append(b)
    inbound: dict[int, int] = defaultdict(int)
    for doc in session.scalars(
        select(Document).where(Document.kind == DocKind.TRANSFER, Document.status == DocStatus.IN_TRANSIT)
    ):
        inbound[doc.dest_warehouse_id] += doc.qty
    events: dict[int, list[WarehouseEvent]] = defaultdict(list)
    for event in session.scalars(select(WarehouseEvent).order_by(WarehouseEvent.id)):
        events[event.warehouse_id].append(event)

    return [
        {
            "id": wh.id,
            "name": wh.name,
            "deleted": wh.is_deleted,
            "created_at": wh.created_at,
            "deleted_at": wh.deleted_at,
            "on_hand": sum(b.on_hand for b in stock[wh.id]),
            "available": sum(b.available for b in stock[wh.id]),
            "reserved": sum(b.reserved for b in stock[wh.id]),
            "inbound_transit": inbound[wh.id],
            "skus": [
                {"sku": products[b.product_id].sku, "on_hand": b.on_hand, "reserved": b.reserved} for b in stock[wh.id]
            ],
            "events": [
                {"event": e.event, "old_name": e.old_name, "new_name": e.new_name, "at": e.created_at}
                for e in events[wh.id]
            ],
        }
        for wh in warehouses
    ]


# ---------- сверка учёта ----------

_FLOWS = """
    WITH flows AS (
        SELECT product_id, to_bucket AS bucket, to_warehouse_id AS warehouse_id, document_id, qty FROM movements
        UNION ALL
        SELECT product_id, from_bucket, from_warehouse_id, document_id, -qty FROM movements
    )
"""

# 1. Остатки ФФ в stock_balances равны сумме проводок по журналу.
_BALANCES_VS_LEDGER = (
    _FLOWS
    + """
    , ledger AS (
        SELECT product_id, warehouse_id,
               SUM(qty) FILTER (WHERE bucket = 'available') AS available,
               SUM(qty) FILTER (WHERE bucket = 'reserved') AS reserved
        FROM flows WHERE bucket IN ('available', 'reserved')
        GROUP BY product_id, warehouse_id
    )
    SELECT COALESCE(l.product_id, b.product_id) AS product_id,
           COALESCE(l.warehouse_id, b.warehouse_id) AS warehouse_id,
           COALESCE(l.available, 0) AS ledger_available, COALESCE(b.available, 0) AS balance_available,
           COALESCE(l.reserved, 0) AS ledger_reserved, COALESCE(b.reserved, 0) AS balance_reserved
    FROM ledger l
    FULL JOIN stock_balances b ON b.product_id = l.product_id AND b.warehouse_id = l.warehouse_id
    WHERE COALESCE(l.available, 0) <> COALESCE(b.available, 0)
       OR COALESCE(l.reserved, 0) <> COALESCE(b.reserved, 0)
"""
)

# 2. В пути по журналу ровно столько, сколько по открытым документам «в пути».
_TRANSIT_VS_DOCUMENTS = (
    _FLOWS
    + """
    , ledger AS (
        SELECT document_id, SUM(qty) AS qty FROM flows
        WHERE bucket IN ('transit_ff', 'transit_fbo', 'transit_fbs')
        GROUP BY document_id
    )
    SELECT d.id, d.kind, d.status, d.qty, COALESCE(l.qty, 0) AS ledger_qty
    FROM documents d LEFT JOIN ledger l ON l.document_id = d.id
    WHERE COALESCE(l.qty, 0) <> CASE
        WHEN (d.kind IN ('transfer', 'fbo') AND d.status = 'in_transit') OR (d.kind = 'fbs' AND d.status = 'shipped')
        THEN d.qty ELSE 0 END
"""
)

# 3. Резерв на ФФ равен сумме открытых резервов FBS.
_RESERVED_VS_ORDERS = """
    WITH orders AS (
        SELECT product_id, source_warehouse_id AS warehouse_id, SUM(qty) AS qty
        FROM documents WHERE kind = 'fbs' AND status = 'reserved'
        GROUP BY product_id, source_warehouse_id
    )
    SELECT COALESCE(b.product_id, o.product_id) AS product_id,
           COALESCE(b.warehouse_id, o.warehouse_id) AS warehouse_id,
           COALESCE(b.reserved, 0) AS reserved, COALESCE(o.qty, 0) AS orders_qty
    FROM stock_balances b
    FULL JOIN orders o ON o.product_id = b.product_id AND o.warehouse_id = b.warehouse_id
    WHERE COALESCE(b.reserved, 0) <> COALESCE(o.qty, 0)
"""

# 4. Сохранение количества: всё принятое либо на ФФ, либо в пути, либо выбыло к МП/клиенту.
_TOTALS = """
    SELECT
        (SELECT COALESCE(SUM(qty), 0) FROM documents WHERE kind = 'receipt') AS received,
        (SELECT COALESCE(SUM(available + reserved), 0) FROM stock_balances) AS on_ff,
        (SELECT COALESCE(SUM(qty), 0) FROM documents
            WHERE (kind IN ('transfer', 'fbo') AND status = 'in_transit')
               OR (kind = 'fbs' AND status = 'shipped')) AS in_transit,
        (SELECT COALESCE(SUM(qty), 0) FROM documents
            WHERE (kind = 'fbo' AND status = 'accepted') OR (kind = 'fbs' AND status = 'delivered')) AS left_ff
"""


def reconcile(session: Session) -> dict[str, Any]:
    """Независимая проверка учёта по четырём правилам. Должна проходить после любого действия."""
    mismatches = []
    for r in session.execute(text(_BALANCES_VS_LEDGER)):
        mismatches.append(
            f"Остаток товара #{r.product_id} на ФФ #{r.warehouse_id} (свободно/резерв): "
            f"в таблице {r.balance_available}/{r.balance_reserved}, по журналу {r.ledger_available}/{r.ledger_reserved}"
        )
    for r in session.execute(text(_TRANSIT_VS_DOCUMENTS)):
        mismatches.append(
            f"Документ №{r.id} ({r.kind}, {r.status}): в пути по журналу {r.ledger_qty}, ожидалось {r.qty}"
        )
    for r in session.execute(text(_RESERVED_VS_ORDERS)):
        mismatches.append(
            f"Резерв товара #{r.product_id} на ФФ #{r.warehouse_id}: {r.reserved}, "
            f"по открытым заказам FBS {r.orders_qty}"
        )
    t = session.execute(text(_TOTALS)).one()
    if t.received != t.on_ff + t.in_transit + t.left_ff:
        mismatches.append(f"Принято {t.received} ≠ на ФФ {t.on_ff} + в пути {t.in_transit} + выбыло {t.left_ff}")
    return {
        "ok": not mismatches,
        "received": t.received,
        "on_ff": t.on_ff,
        "in_transit": t.in_transit,
        "left": t.left_ff,
        "mismatches": mismatches,
    }
