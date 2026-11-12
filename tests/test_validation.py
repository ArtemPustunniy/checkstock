"""Некорректные запросы отклоняются с понятным сообщением и ничего не меняют."""

import pytest


@pytest.mark.parametrize(
    "body",
    [
        {"sku": "SKU-101", "qty": 0, "warehouse_id": 1, "order_no": "A"},
        {"sku": "SKU-101", "qty": -2, "warehouse_id": 1, "order_no": "A"},
        {"sku": "SKU-101", "qty": 3, "order_no": "A"},  # нет ни warehouse_id, ни allocations
        {"sku": "SKU-101", "qty": 3, "order_no": "A", "allocations": [{"warehouse_id": 1, "qty": 2}]},  # сумма ≠ qty
        {
            "sku": "SKU-101",
            "qty": 3,
            "order_no": "A",
            "allocations": [{"warehouse_id": 1, "qty": 2}, {"warehouse_id": 1, "qty": 1}],
        },
        {"sku": "SKU-101", "qty": 3, "warehouse_id": 1, "order_no": "   "},
        {"sku": "SKU-101", "qty": 3, "warehouse_id": 1, "order_no": "A", "price": 10},  # лишнее поле
    ],
)
def test_invalid_fbs_request(stocked, body):
    before = stocked.stock()
    response = stocked.post("/api/fbs", body)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert response.json()["message"].startswith("Проверьте данные")
    assert stocked.stock() == before


def test_transfer_to_same_ff(stocked):
    response = stocked.transfer("SKU-101", 1, "Север", "Север")
    assert response.status_code == 422


def test_unknown_sku_and_warehouse(stocked):
    assert stocked.reserve("SKU-999", 1, "Север").status_code == 404
    response = stocked.post("/api/fbs", {"sku": "SKU-101", "qty": 1, "warehouse_id": 999, "order_no": "A"})
    assert response.status_code == 404


def test_unknown_document(stocked):
    assert stocked.step("/fbs/999/ship").status_code == 404
