"""Сквозной сценарий из задания: приход → резерв → отгрузка → перемещение → приёмка → FBO.
Цифры проверяются после каждого шага."""

from tests.helpers import Cell


def test_test_receipt_matches_task_table(stocked):
    assert stocked.cell("SKU-101", "Север") == Cell(10, 10, 0)
    assert stocked.cell("SKU-101", "Юг") == Cell(6, 6, 0)
    assert stocked.cell("SKU-102", "Север") == Cell(5, 5, 0)
    assert stocked.cell("SKU-102", "Юг") == Cell(0, 0, 0)
    assert stocked.cell("SKU-103", "Север") == Cell(0, 0, 0)
    assert stocked.cell("SKU-103", "Юг") == Cell(4, 4, 0)
    assert stocked.stock()["totals"]["on_hand"] == 25


def test_end_to_end(stocked):
    api = stocked

    # 1. Резерв FBS блокирует товар: физически 10, доступно 7, в резерве 3
    order = api.doc_id(api.reserve("SKU-101", 3, "Север", order="WB-1001"))
    assert api.cell("SKU-101", "Север") == Cell(10, 7, 3)

    # 2. Отгрузка заказа: товар уходит с ФФ и едет к клиенту
    shipped = api.ok(api.step(f"/fbs/{order}/ship"), 200)
    assert shipped["changed"] is True
    assert api.cell("SKU-101", "Север") == Cell(7, 7, 0)
    assert api.row("SKU-101")["transit_fbs"] == 3

    # 3. Повторное подтверждение отгрузки ничего не списывает
    again = api.ok(api.step(f"/fbs/{order}/ship"), 200)
    assert again["changed"] is False
    assert api.cell("SKU-101", "Север") == Cell(7, 7, 0)
    assert api.row("SKU-101")["transit_fbs"] == 3

    # 4. Доставка закрывает путь к клиенту
    api.ok(api.step(f"/fbs/{order}/deliver"), 200)
    assert api.row("SKU-101")["transit_fbs"] == 0

    # 5. Перемещение Север → Юг: товар в пути, ни на одном ФФ не числится
    transfer = api.doc_id(api.transfer("SKU-101", 4, "Север", "Юг"))
    assert api.cell("SKU-101", "Север") == Cell(3, 3, 0)
    assert api.cell("SKU-101", "Юг") == Cell(6, 6, 0)
    assert api.row("SKU-101")["transit_ff"] == 4

    # 6. Приёмка на Юге; повторная приёмка — no-op
    api.ok(api.step(f"/transfers/{transfer}/receive"), 200)
    assert api.ok(api.step(f"/transfers/{transfer}/receive"), 200)["changed"] is False
    assert api.cell("SKU-101", "Юг") == Cell(10, 10, 0)
    assert api.row("SKU-101")["transit_ff"] == 0

    # 7. FBO: товар покидает ФФ и числится в пути до приёмки складом МП
    fbo = api.doc_id(api.fbo("SKU-103", 3, "Юг", to="WB Коледино"))
    assert api.cell("SKU-103", "Юг") == Cell(1, 1, 0)
    assert api.row("SKU-103")["transit_fbo"] == 3
    api.ok(api.step(f"/fbo/{fbo}/accept"), 200)
    assert api.row("SKU-103")["transit_fbo"] == 0

    # 8. Отмена резерва возвращает товар в доступный
    order2 = api.doc_id(api.reserve("SKU-102", 2, "Север", order="WB-1002"))
    assert api.cell("SKU-102", "Север") == Cell(5, 3, 2)
    api.ok(api.step(f"/fbs/{order2}/cancel"), 200)
    assert api.cell("SKU-102", "Север") == Cell(5, 5, 0)

    # итог: принято 25 = на ФФ 19 + в пути 0 + выбыло 6 (3 клиенту + 3 на МП)
    report = api.client.get("/api/reconcile").json()
    assert (report["received"], report["on_ff"], report["in_transit"], report["left"]) == (25, 19, 0, 6)


def test_history_shows_what_how_many_from_where_to_where(stocked):
    api = stocked
    transfer = api.doc_id(api.transfer("SKU-101", 4, "Север", "Юг"))
    api.ok(api.step(f"/transfers/{transfer}/receive"), 200)

    send, receive = reversed(api.history(sku="SKU-101")[:2])
    assert (send["action"], send["qty"], send["from_label"], send["to_label"]) == (
        "transfer_send",
        4,
        "Север · свободно",
        "В пути Север → Юг",
    )
    assert (receive["action"], receive["qty"], receive["from_label"], receive["to_label"]) == (
        "transfer_receive",
        4,
        "В пути Север → Юг",
        "Юг · свободно",
    )


def test_where_is_lists_every_place(stocked):
    api = stocked
    api.reserve("SKU-101", 2, "Север", order="WB-7")
    api.transfer("SKU-101", 3, "Север", "Юг")
    api.fbo("SKU-101", 1, "Юг", to="Ozon Хоругвино")

    where = api.client.get("/api/stock/SKU-101").json()
    # Север: 10 − 3 уехали на Юг = 7 физически, из них 2 в резерве. Юг: 6 − 1 на FBO = 5.
    ff = {p["label"]: (p["qty"], p["available"], p["reserved"]) for p in where["places"] if p["kind"] == "ff"}
    assert ff == {"ФФ «Север»": (7, 5, 2), "ФФ «Юг»": (5, 5, 0)}
    other = {p["label"]: p["qty"] for p in where["places"] if p["kind"] != "ff"}
    assert other == {
        "Резерв FBS на «Север», заказ WB-7": 2,
        "В пути «Север» → «Юг»": 3,
        "В пути на склад МП «Ozon Хоругвино»": 1,
    }
    assert (where["on_hand"], where["in_transit"]) == (12, 4)
