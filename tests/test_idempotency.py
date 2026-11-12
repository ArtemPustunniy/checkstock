"""Повторное подтверждение не списывает товар дважды — ни последовательно, ни параллельно."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import operations
from tests.helpers import Cell, new_key


@pytest.mark.parametrize(
    ("create", "path", "state_after"),
    [
        (lambda api: api.reserve("SKU-101", 3, "Север"), "/fbs/{}/ship", Cell(7, 7, 0)),
        (lambda api: api.reserve("SKU-101", 3, "Север"), "/fbs/{}/cancel", Cell(10, 10, 0)),
        (lambda api: api.transfer("SKU-101", 3, "Север", "Юг"), "/transfers/{}/receive", Cell(7, 7, 0)),
        (lambda api: api.fbo("SKU-101", 3, "Север"), "/fbo/{}/accept", Cell(7, 7, 0)),
    ],
)
def test_repeated_step_is_noop(stocked, create, path, state_after):
    api = stocked
    doc = api.doc_id(create(api))

    first = api.ok(api.step(path.format(doc)), 200)
    second = api.ok(api.step(path.format(doc)), 200)

    assert (first["changed"], second["changed"]) == (True, False)
    assert "ничего" in second["message"] or "уже" in second["message"]
    assert api.cell("SKU-101", "Север") == state_after


def test_ship_after_delivery_is_still_noop(stocked):
    api = stocked
    doc = api.doc_id(api.reserve("SKU-101", 3, "Север"))
    api.ok(api.step(f"/fbs/{doc}/ship"), 200)
    api.ok(api.step(f"/fbs/{doc}/deliver"), 200)

    assert api.ok(api.step(f"/fbs/{doc}/ship"), 200)["changed"] is False


@pytest.mark.parametrize(("first", "then"), [("ship", "cancel"), ("cancel", "ship"), ("cancel", "deliver")])
def test_incompatible_step_is_rejected(stocked, first, then):
    api = stocked
    doc = api.doc_id(api.reserve("SKU-101", 3, "Север"))
    api.ok(api.step(f"/fbs/{doc}/{first}"), 200)
    before = api.stock()

    response = api.step(f"/fbs/{doc}/{then}")

    assert (response.status_code, response.json()["error"]) == (409, "invalid_status")
    assert api.stock() == before


def test_wrong_document_kind_is_not_found(stocked):
    api = stocked
    transfer = api.doc_id(api.transfer("SKU-101", 1, "Север", "Юг"))
    assert api.step(f"/fbs/{transfer}/ship").status_code == 404


def test_idempotency_key_replays_receipt(api):
    key = new_key()
    first = api.receipt([("SKU-101", "Север", 10)], key=key)
    second = api.receipt([("SKU-101", "Север", 10)], key=key)

    assert first.status_code == second.status_code == 201
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert first.json() == second.json()
    assert api.cell("SKU-101", "Север") == Cell(10, 10, 0)  # не 20


def test_idempotency_key_with_other_payload_is_rejected(api):
    key = new_key()
    api.ok(api.receipt([("SKU-101", "Север", 10)], key=key))

    response = api.receipt([("SKU-101", "Север", 99)], key=key)
    assert (response.status_code, response.json()["error"]) == (422, "idempotency_key_reused")


def test_failed_request_does_not_burn_the_key(api):
    key = new_key()
    assert api.reserve("SKU-101", 3, "Север", key=key).status_code == 409  # остатков ещё нет
    api.load_test_receipt()

    assert api.reserve("SKU-101", 3, "Север", key=key).status_code == 201
    assert api.cell("SKU-101", "Север") == Cell(10, 7, 3)


# ---------- параллельные запросы: блокировки в Postgres ----------


@pytest.fixture
def slow_transactions(monkeypatch):
    """Расширяем окно гонки: захватив блокировку товара, транзакция «думает» 50 мс.
    Без этого параллельные запросы почти не пересекаются, и тест прошёл бы даже без блокировок."""
    original = operations.lock_product

    def slow(session, product_id):
        product = original(session, product_id)
        time.sleep(0.05)
        return product

    monkeypatch.setattr(operations, "lock_product", slow)


def _parallel(calls: int, request) -> list:
    """Запустить `calls` запросов одновременно, каждый своим клиентом и своим соединением с БД."""
    barrier = Barrier(calls)

    def run(i):
        client = TestClient(app)
        barrier.wait()
        return request(client, i)

    with ThreadPoolExecutor(calls) as pool:
        return list(pool.map(run, range(calls)))


def test_parallel_ship_moves_goods_once(stocked, slow_transactions):
    api = stocked
    doc = api.doc_id(api.reserve("SKU-101", 3, "Север"))

    responses = _parallel(8, lambda client, _: client.post(f"/api/fbs/{doc}/ship"))

    assert [r.status_code for r in responses] == [200] * 8
    assert sum(r.json()["changed"] for r in responses) == 1
    assert api.cell("SKU-101", "Север") == Cell(7, 7, 0)
    api.assert_reconciled()


def test_parallel_reserves_never_oversell(stocked, slow_transactions):
    api = stocked
    north = api.wh("Север")

    responses = _parallel(
        8,
        lambda client, i: client.post(
            "/api/fbs", json={"sku": "SKU-101", "qty": 3, "warehouse_id": north, "order_no": f"WB-{i}"}
        ),
    )

    codes = sorted(r.status_code for r in responses)
    assert codes == [201] * 3 + [409] * 5  # 10 шт. хватает ровно на три заказа по 3
    # отказы — от проверки остатка под блокировкой, а не от страховочного CHECK в БД
    assert {r.json()["error"] for r in responses if r.status_code == 409} == {"shortage"}
    assert api.cell("SKU-101", "Север") == Cell(10, 1, 9)
    api.assert_reconciled()


def test_parallel_requests_with_same_key_apply_once(api):
    north = api.wh("Север")
    key = new_key()

    responses = _parallel(
        6,
        lambda client, _: client.post(
            "/api/receipts",
            json={"lines": [{"sku": "SKU-101", "warehouse_id": north, "qty": 10}]},
            headers={"Idempotency-Key": key},
        ),
    )

    assert [r.status_code for r in responses] == [201] * 6
    assert len({r.json()["documents"][0]["id"] for r in responses}) == 1
    assert api.cell("SKU-101", "Север") == Cell(10, 10, 0)
    api.assert_reconciled()
