"""Случайные последовательности операций против простой модели в памяти.

Модель — словари с остатками, без БД и блокировок; её легко проверить глазами.
После каждого шага экран остатков API должен совпасть с моделью до единицы,
а сверка учёта (/api/reconcile) — пройти. Если реализация разойдётся с моделью,
тест покажет seed, номер шага и операцию.
"""

import random
from collections import defaultdict

import pytest

SKUS = ["SKU-101", "SKU-102", "SKU-103"]

# (тип, шаг) → (из статуса, в статус, статусы «уже сделано»)
STEP_RULES = {
    ("fbs", "ship"): ("reserved", "shipped", {"shipped", "delivered"}),
    ("fbs", "cancel"): ("reserved", "cancelled", {"cancelled"}),
    ("fbs", "deliver"): ("shipped", "delivered", {"delivered"}),
    ("transfer", "receive"): ("in_transit", "received", {"received"}),
    ("fbo", "accept"): ("in_transit", "accepted", {"accepted"}),
}
STEP_PATHS = {
    ("fbs", "ship"): "/fbs/{}/ship",
    ("fbs", "cancel"): "/fbs/{}/cancel",
    ("fbs", "deliver"): "/fbs/{}/deliver",
    ("transfer", "receive"): "/transfers/{}/receive",
    ("fbo", "accept"): "/fbo/{}/accept",
}
IN_TRANSIT = {"transfer": "in_transit", "fbo": "in_transit", "fbs": "shipped"}


class Model:
    def __init__(self, warehouses: list[str]) -> None:
        self.warehouses = warehouses
        self.deleted: set[str] = set()
        self.available: dict[tuple[str, str], int] = defaultdict(int)
        self.reserved: dict[tuple[str, str], int] = defaultdict(int)
        self.docs: dict[int, dict] = {}

    def inbound_transit(self, wh: str) -> bool:
        return any(
            d["kind"] == "transfer" and d["status"] == "in_transit" and d["dst"] == wh for d in self.docs.values()
        )

    def apply_step(self, doc: dict, step: str) -> None:
        sku, qty = doc["sku"], doc["qty"]
        if step in ("ship", "cancel"):
            self.reserved[(sku, doc["src"])] -= qty
        if step == "cancel":
            self.available[(sku, doc["src"])] += qty
        if step == "receive":
            self.available[(sku, doc["dst"])] += qty


class Simulation:
    def __init__(self, api, seed: int) -> None:
        self.api = api
        self.rnd = random.Random(seed)
        api.create_wh("Восток")
        self.ids = {name: api.wh(name) for name in ("Север", "Юг", "Восток")}
        self.model = Model(list(self.ids))
        self.log: list[str] = []

    # ---------- операции ----------

    def receipt(self):
        sku, wh, qty = self.rnd.choice(SKUS), self.rnd.choice(self.model.warehouses), self.rnd.randint(1, 8)
        response = self.api.receipt([(sku, wh, qty)])
        self.expect(response, 409 if wh in self.model.deleted else 201, f"приход {sku} {qty} → {wh}")
        if response.status_code == 201:
            self.model.available[(sku, wh)] += qty

    def dispatch(self):
        kind = self.rnd.choice(["fbs", "fbo", "transfer"])
        sku, qty = self.rnd.choice(SKUS), self.rnd.randint(1, 6)
        src = self.rnd.choice(self.model.warehouses)
        dst = None
        if kind == "transfer":
            dst = self.rnd.choice([w for w in self.model.warehouses if w != src])
            response = self.api.transfer(sku, qty, src, dst)
        elif kind == "fbo":
            response = self.api.fbo(sku, qty, src)
        else:
            response = self.api.reserve(sku, qty, src, order=f"R-{len(self.log)}")

        enough = self.model.available[(sku, src)] >= qty
        expected = 409 if (dst in self.model.deleted or not enough) else 201
        self.expect(response, expected, f"{kind} {sku} {qty} {src}→{dst or ''}")
        if response.status_code != 201:
            return
        doc_id = response.json()["documents"][0]["id"]
        status = {"fbs": "reserved", "fbo": "in_transit", "transfer": "in_transit"}[kind]
        self.model.docs[doc_id] = {"kind": kind, "status": status, "sku": sku, "qty": qty, "src": src, "dst": dst}
        self.model.available[(sku, src)] -= qty
        if kind == "fbs":
            self.model.reserved[(sku, src)] += qty

    def step(self):
        if not self.model.docs:
            return
        doc_id = self.rnd.choice(list(self.model.docs))
        doc = self.model.docs[doc_id]
        step = self.rnd.choice([s for (kind, s) in STEP_RULES if kind == doc["kind"]])
        from_status, to_status, done = STEP_RULES[(doc["kind"], step)]
        response = self.api.step(STEP_PATHS[(doc["kind"], step)].format(doc_id))

        if doc["status"] in done:
            self.expect(response, 200, f"повтор {step} №{doc_id}")
            assert response.json()["changed"] is False, self.trace()
        elif doc["status"] == from_status:
            self.expect(response, 200, f"{step} №{doc_id}")
            assert response.json()["changed"] is True, self.trace()
            self.model.apply_step(doc, step)
            doc["status"] = to_status
        else:
            self.expect(response, 409, f"{step} №{doc_id} из «{doc['status']}»")

    def delete_or_restore(self):
        wh = self.rnd.choice(self.model.warehouses)
        if wh in self.model.deleted:
            response = self.api.client.post(f"/api/warehouses/{self.ids[wh]}/restore")
            self.expect(response, 200, f"восстановить {wh}")
            self.model.deleted.discard(wh)
            return
        response = self.api.client.delete(f"/api/warehouses/{self.ids[wh]}")
        self.expect(response, 409 if self.model.inbound_transit(wh) else 200, f"удалить {wh}")
        if response.status_code == 200:
            self.model.deleted.add(wh)

    # ---------- проверки ----------

    def expect(self, response, status: int, what: str) -> None:
        self.log.append(f"{what} → {response.status_code}")
        assert response.status_code == status, self.trace() + f"\nожидали {status}: {response.text}"

    def trace(self) -> str:
        return "\n".join(self.log[-12:])

    def compare(self) -> None:
        self.api.assert_reconciled()
        stock = self.api.stock()
        columns = [w["name"] for w in stock["warehouses"]]
        for row in stock["rows"]:
            sku = row["sku"]
            for wh in self.model.warehouses:
                expected = (self.model.available[(sku, wh)], self.model.reserved[(sku, wh)])
                if wh in columns:
                    cell = row["cells"][columns.index(wh)]
                    actual = (cell["available"], cell["reserved"])
                else:
                    actual = (0, 0)
                assert actual == expected, self.trace() + f"\n{sku} на {wh}: API {actual}, модель {expected}"
            for kind, key in (("transfer", "transit_ff"), ("fbo", "transit_fbo"), ("fbs", "transit_fbs")):
                expected = sum(
                    d["qty"]
                    for d in self.model.docs.values()
                    if d["sku"] == sku and d["kind"] == kind and d["status"] == IN_TRANSIT[kind]
                )
                assert row[key] == expected, self.trace() + f"\n{sku} {key}: API {row[key]}, модель {expected}"
        # удалённый ФФ виден на экране остатков, пока на нём что-то числится
        for wh in self.model.deleted:
            holds = any(self.model.available[(s, wh)] + self.model.reserved[(s, wh)] for s in SKUS)
            assert (wh in columns) == holds, self.trace() + f"\nудалённый {wh}: виден={wh in columns}, держит={holds}"

    def run(self, steps: int) -> None:
        operations = [self.receipt] * 3 + [self.dispatch] * 5 + [self.step] * 6 + [self.delete_or_restore]
        for _ in range(steps):
            self.rnd.choice(operations)()
            self.compare()


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_random_operations_match_model(api, seed):
    Simulation(api, seed).run(steps=120)
