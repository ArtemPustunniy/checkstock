"""Тонкая обёртка над API для читаемых тестов.

После каждого изменяющего запроса Api сверяет учёт (GET /api/reconcile): так правило
«остатки сходятся после каждого действия» проверяется во всех тестах, а не в одном.
"""

import uuid
from dataclasses import dataclass

from fastapi.testclient import TestClient
from httpx import Response

TEST_RECEIPT = [("SKU-101", "Север", 10), ("SKU-101", "Юг", 6), ("SKU-102", "Север", 5), ("SKU-103", "Юг", 4)]


@dataclass(frozen=True)
class Cell:
    on_hand: int
    available: int
    reserved: int


class Api:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    # ---------- низкоуровневое ----------

    def post(self, url: str, json: dict | None = None, *, key: str | None = None) -> Response:
        headers = {"Idempotency-Key": key} if key else {}
        response = self.client.post(url, json=json or {}, headers=headers)
        self.assert_reconciled()
        return response

    def assert_reconciled(self) -> None:
        report = self.client.get("/api/reconcile").json()
        assert report["ok"], report["mismatches"]

    @staticmethod
    def ok(response: Response, status: int = 201) -> dict:
        assert response.status_code == status, response.text
        return response.json()

    # ---------- справочники ----------

    def wh(self, name: str) -> int:
        for warehouse in self.client.get("/api/warehouses").json():
            if warehouse["name"] == name:
                return warehouse["id"]
        raise KeyError(name)

    def create_wh(self, name: str) -> int:
        return self.ok(self.client.post("/api/warehouses", json={"name": name}))["warehouse"]["id"]

    def delete_wh(self, name: str) -> Response:
        response = self.client.delete(f"/api/warehouses/{self.wh(name)}")
        self.assert_reconciled()
        return response

    # ---------- операции ----------

    def receipt(self, lines: list[tuple[str, str, int]], key: str | None = None) -> Response:
        body = {"lines": [{"sku": sku, "warehouse_id": self.wh(wh), "qty": qty} for sku, wh, qty in lines]}
        return self.post("/api/receipts", body, key=key)

    def load_test_receipt(self) -> None:
        self.ok(self.receipt(TEST_RECEIPT))

    def _source(self, wh: str | None, split: dict[str, int] | None) -> dict:
        if split is not None:
            return {"allocations": [{"warehouse_id": self.wh(name), "qty": qty} for name, qty in split.items()]}
        return {"warehouse_id": self.wh(wh)}

    def reserve(self, sku: str, qty: int, wh: str | None = None, *, order: str = "WB-1", split=None, key=None):
        body = {"sku": sku, "qty": qty, "order_no": order, **self._source(wh, split)}
        return self.post("/api/fbs", body, key=key)

    def fbo(self, sku: str, qty: int, wh: str | None = None, *, to: str = "WB Коледино", split=None, key=None):
        body = {"sku": sku, "qty": qty, "destination": to, **self._source(wh, split)}
        return self.post("/api/fbo", body, key=key)

    def transfer(self, sku: str, qty: int, src: str | None, dst: str, *, split=None, key=None):
        body = {"sku": sku, "qty": qty, "dest_warehouse_id": self.wh(dst), **self._source(src, split)}
        return self.post("/api/transfers", body, key=key)

    def step(self, path: str) -> Response:
        return self.post(f"/api{path}")

    def doc_id(self, response: Response) -> int:
        return self.ok(response)["documents"][0]["id"]

    # ---------- чтение ----------

    def stock(self) -> dict:
        return self.client.get("/api/stock").json()

    def cell(self, sku: str, wh: str) -> Cell:
        data = self.stock()
        columns = [w["name"] for w in data["warehouses"]]
        row = next(r for r in data["rows"] if r["sku"] == sku)
        if wh not in columns:
            return Cell(0, 0, 0)
        return Cell(**row["cells"][columns.index(wh)])

    def row(self, sku: str) -> dict:
        return next(r for r in self.stock()["rows"] if r["sku"] == sku)

    def history(self, **params) -> list[dict]:
        return self.client.get("/api/history", params=params).json()


def new_key() -> str:
    return str(uuid.uuid4())
