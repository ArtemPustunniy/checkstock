"""Нехватка на выбранном ФФ: при достаточном общем остатке — варианты, иначе — отказ.
В обоих случаях ничего не проводится."""

from tests.helpers import Cell


def test_shortage_on_chosen_ff_offers_options_and_changes_nothing(stocked):
    api = stocked
    before = api.stock()

    response = api.reserve("SKU-101", 8, "Юг", order="WB-2001")  # на Юге 6, всего 16

    assert response.status_code == 409
    error = response.json()
    assert error["error"] == "shortage"
    assert (error["requested"], error["total_available"]) == (8, 16)
    assert [o["label"] for o in error["options"]] == [
        "8 шт. с «Север»",
        "6 шт. с «Юг» + 2 шт. с «Север»",
    ]
    assert api.stock() == before


def test_apply_split_option(stocked):
    api = stocked
    error = api.reserve("SKU-101", 8, "Юг", order="WB-2001").json()
    split = error["options"][1]["allocations"]

    body = {"sku": "SKU-101", "qty": 8, "order_no": "WB-2001", "allocations": split}
    result = api.ok(api.post("/api/fbs", body))

    assert [(d["source"]["name"], d["qty"], d["reference"]) for d in result["documents"]] == [
        ("Юг", 6, "WB-2001"),
        ("Север", 2, "WB-2001"),
    ]
    assert api.cell("SKU-101", "Юг") == Cell(6, 0, 6)
    assert api.cell("SKU-101", "Север") == Cell(10, 8, 2)


def test_reserved_units_are_not_available(stocked):
    api = stocked
    api.ok(api.reserve("SKU-101", 4, "Север"))

    error = api.fbo("SKU-101", 8, "Север").json()  # физически 10, но 4 в резерве

    assert error["error"] == "shortage"
    assert "доступно 6 шт." in error["message"]
    assert "4 шт. там в резерве FBS" in error["message"]


def test_total_shortage_has_no_options(stocked):
    api = stocked
    response = api.fbo("SKU-102", 6, "Север")  # всего SKU-102 — 5

    assert response.status_code == 409
    error = response.json()
    assert error["error"] == "shortage_total"
    assert error["options"] == []
    assert api.cell("SKU-102", "Север") == Cell(5, 5, 0)


def test_empty_ff_suggests_taking_everything_elsewhere(stocked):
    api = stocked
    error = api.reserve("SKU-102", 3, "Юг").json()  # на Юге SKU-102 нет совсем

    assert [o["allocations"] for o in error["options"]] == [[{"warehouse_id": api.wh("Север"), "qty": 3}]]


def test_transfer_shortage_never_suggests_destination(stocked):
    api = stocked
    api.create_wh("Восток")
    api.ok(api.receipt([("SKU-103", "Восток", 2)]))

    error = api.transfer("SKU-103", 5, "Восток", "Юг").json()  # Восток 2, Юг 4 — но Юг получатель

    assert error["error"] == "shortage_total"
    assert all(w["name"] != "Юг" for w in error["warehouses"])


def test_split_that_no_longer_fits_is_rejected_whole(stocked):
    api = stocked
    split = {"Юг": 6, "Север": 2}
    api.ok(api.fbo("SKU-101", 9, "Север"))  # пока менеджер думал, с Севера отгрузили 9

    response = api.reserve("SKU-101", 8, split=split)

    assert response.status_code == 409
    assert api.cell("SKU-101", "Юг") == Cell(6, 6, 0)  # частично ничего не провелось
    assert api.cell("SKU-101", "Север") == Cell(1, 1, 0)
