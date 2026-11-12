"""ФФ: создание, переименование, удаление с остатком, восстановление."""

from tests.helpers import Cell


def test_rename_keeps_stock_and_history(stocked):
    api = stocked
    north = api.wh("Север")
    api.ok(api.client.patch(f"/api/warehouses/{north}", json={"name": "Север-2"}), 200)

    assert api.cell("SKU-101", "Север-2") == Cell(10, 10, 0)
    assert api.history(warehouse_id=north)[-1]["to_label"] == "Север-2 · свободно"
    events = next(w for w in api.client.get("/api/warehouses").json() if w["id"] == north)["events"]
    assert [(e["event"], e["old_name"], e["new_name"]) for e in events][-1] == ("renamed", "Север", "Север-2")


def test_active_names_are_unique_case_insensitive(api):
    response = api.client.post("/api/warehouses", json={"name": "  север "})
    assert response.status_code == 409
    assert response.json()["error"] == "name_taken"


def test_delete_ff_with_stock_keeps_it_visible(stocked):
    api = stocked
    result = api.ok(api.delete_wh("Север"), 200)

    assert result["changed"] is True
    assert "числится 15 шт." in result["message"]
    stock = api.stock()
    north = next(w for w in stock["warehouses"] if w["name"] == "Север")
    assert north["deleted"] is True  # колонка осталась: товар там числится
    assert api.cell("SKU-101", "Север") == Cell(10, 10, 0)
    assert stock["totals"]["on_hand"] == 25  # общий остаток не «потерялся»

    where = api.client.get("/api/stock/SKU-102").json()
    assert where["places"][0]["label"] == "ФФ «Север (удалён)»"
    assert len(api.history(warehouse_id=north["id"])) == 2  # история прихода на месте


def test_deleted_ff_accepts_nothing(stocked):
    api = stocked
    api.delete_wh("Север")

    receipt = api.receipt([("SKU-101", "Север", 1)])
    transfer = api.transfer("SKU-101", 1, "Юг", "Север")

    assert (receipt.status_code, receipt.json()["error"]) == (409, "warehouse_deleted")
    assert (transfer.status_code, transfer.json()["error"]) == (409, "warehouse_deleted")
    assert api.cell("SKU-101", "Север") == Cell(10, 10, 0)


def test_stock_can_be_moved_out_of_deleted_ff(stocked):
    api = stocked
    api.delete_wh("Север")

    for sku, qty in (("SKU-101", 10), ("SKU-102", 5)):
        doc = api.doc_id(api.transfer(sku, qty, "Север", "Юг"))
        api.ok(api.step(f"/transfers/{doc}/receive"), 200)

    stock = api.stock()
    assert [w["name"] for w in stock["warehouses"]] == ["Юг"]  # пустой удалённый ФФ скрыт
    assert api.cell("SKU-101", "Юг") == Cell(16, 16, 0)
    assert stock["totals"]["on_hand"] == 25


def test_reserve_on_deleted_ff_can_still_be_shipped(stocked):
    api = stocked
    order = api.doc_id(api.reserve("SKU-101", 2, "Север"))
    api.delete_wh("Север")

    assert api.ok(api.step(f"/fbs/{order}/ship"), 200)["changed"] is True
    assert api.cell("SKU-101", "Север") == Cell(8, 8, 0)


def test_cannot_delete_ff_while_transfer_is_on_its_way(stocked):
    api = stocked
    doc = api.doc_id(api.transfer("SKU-101", 4, "Север", "Юг"))

    blocked = api.delete_wh("Юг")
    assert blocked.status_code == 409
    assert blocked.json() | {"message": None} == {"error": "inbound_transit", "message": None, "documents": [doc]}

    api.ok(api.step(f"/transfers/{doc}/receive"), 200)
    assert api.ok(api.delete_wh("Юг"), 200)["changed"] is True


def test_restore_and_repeat_delete(stocked):
    api = stocked
    north = api.wh("Север")
    api.delete_wh("Север")
    assert api.ok(api.delete_wh("Север"), 200)["changed"] is False  # повторное удаление — no-op

    api.ok(api.client.post(f"/api/warehouses/{north}/restore"), 200)
    api.ok(api.receipt([("SKU-101", "Север", 1)]))
    assert api.cell("SKU-101", "Север") == Cell(11, 11, 0)


def test_restore_blocked_if_name_reused(stocked):
    api = stocked
    north = api.wh("Север")
    api.delete_wh("Север")
    api.create_wh("Север")  # новый склад с тем же именем — можно, старый удалён

    response = api.client.post(f"/api/warehouses/{north}/restore")
    assert (response.status_code, response.json()["error"]) == (409, "name_taken")


def test_deleted_ff_cannot_be_renamed(stocked):
    api = stocked
    north = api.wh("Север")
    api.delete_wh("Север")
    response = api.client.patch(f"/api/warehouses/{north}", json={"name": "Другой"})
    assert response.status_code == 409
