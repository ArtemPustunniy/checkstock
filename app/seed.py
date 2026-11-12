"""Тестовые данные из задания: один магазин, два ФФ и количества для тестового прихода.

python -m app.seed                  # сбросить к началу: товары и ФФ без остатков
python -m app.seed --with-receipt   # сбросить и сразу провести тестовый приход
python -m app.seed --if-empty       # заполнить, только если база пустая (для старта контейнера)
"""

import argparse

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Product, Warehouse, WarehouseEvent
from app.services import operations

STORE = "Демо-магазин"
PRODUCTS = [("SKU-101", "Органайзер"), ("SKU-102", "Контейнер"), ("SKU-103", "Корзина")]
WAREHOUSES = ["Север", "Юг"]
# Количества для тестового прихода: артикул → {ФФ: шт.}
TEST_RECEIPT = {
    "SKU-101": {"Север": 10, "Юг": 6},
    "SKU-102": {"Север": 5, "Юг": 0},
    "SKU-103": {"Север": 0, "Юг": 4},
}
TABLES = "movements, idempotency_keys, documents, stock_balances, warehouse_events, warehouses, products"


def demo_receipt_lines(session: Session) -> list[operations.ReceiptLine]:
    ids = {w.name: w.id for w in session.scalars(select(Warehouse).where(Warehouse.deleted_at.is_(None)))}
    return [
        operations.ReceiptLine(sku=sku, warehouse_id=ids[name], qty=qty)
        for sku, by_wh in TEST_RECEIPT.items()
        for name, qty in by_wh.items()
        if qty > 0
    ]


def reset(session: Session, *, with_receipt: bool = False) -> None:
    """Очистить все данные и загрузить тестовые. Вызывать внутри транзакции."""
    session.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    session.add_all(Product(sku=sku, name=name) for sku, name in PRODUCTS)
    warehouses = [Warehouse(name=name) for name in WAREHOUSES]
    session.add_all(warehouses)
    session.flush()
    session.add_all(WarehouseEvent(warehouse_id=w.id, event="created", new_name=w.name) for w in warehouses)
    session.flush()
    if with_receipt:
        operations.receive(session, demo_receipt_lines(session), reference="Тестовый приход из задания")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--with-receipt", action="store_true", help="провести тестовый приход")
    parser.add_argument("--if-empty", action="store_true", help="ничего не делать, если данные уже есть")
    args = parser.parse_args()

    with SessionLocal() as session, session.begin():
        if args.if_empty and session.scalar(select(Product.id).limit(1)) is not None:
            print("Данные уже есть — пропускаю заполнение.")
            return
        reset(session, with_receipt=args.with_receipt)
    print("Тестовые данные загружены" + (" вместе с приходом." if args.with_receipt else "."))


if __name__ == "__main__":
    main()
