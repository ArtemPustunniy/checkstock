"""Мутационная проверка тестов: вносим в код типичную ошибку и убеждаемся, что тесты её ловят.

    python scripts/mutation_check.py

Каждая мутация — правдоподобная ошибка (её мог допустить и человек, и ИИ): забытая блокировка,
резерв, посчитанный как доступный, пропущенная проводка в журнал. Если тесты прошли с мутацией —
они чего-то не проверяют. Файлы восстанавливаются после каждой мутации.
"""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYTHON = sys.executable
MUTANTS = [
    (
        "повтор шага не распознаётся (нет проверки done)",
        "app/services/operations.py",
        "    if doc.status in step.done:\n        return doc, False\n",
        "",
    ),
    (
        "нет блокировки документа при шаге",
        "app/services/operations.py",
        "select(Document).where(Document.id == doc_id).with_for_update()",
        "select(Document).where(Document.id == doc_id)",
    ),
    (
        "нет блокировки товара при отправке (проверка остатка без блокировки)",
        "app/services/operations.py",
        "    lock_product(session, product.id)\n    warehouses = ",
        "    warehouses = ",
    ),
    (
        "резерв считается доступным",
        "app/services/operations.py",
        "return row.available if row else 0",
        "return row.on_hand if row else 0",
    ),
    (
        "проводка не пишется в журнал при отгрузке FBS",
        "app/services/ledger.py",
        "    session.add(movement)\n",
        "    if action is not Action.FBS_SHIP:\n        session.add(movement)\n",
    ),
    (
        "удаление ФФ не проверяет входящие перемещения",
        "app/services/warehouses.py",
        "    if inbound:\n",
        "    if False:\n",
    ),
    (
        "приход на удалённый ФФ разрешён",
        "app/services/operations.py",
        '        if warehouse.is_deleted:\n            raise Conflict(f"ФФ «{warehouse.name}» удалён — оформить приход',
        '        if False:\n            raise Conflict(f"ФФ «{warehouse.name}» удалён — оформить приход',
    ),
    ("Idempotency-Key игнорируется", "app/services/idempotency.py", "    if not key:\n", "    if True:\n"),
    (
        "отмена резерва не возвращает товар в доступный",
        "app/domain.py",
        'Bucket.RESERVED, Bucket.AVAILABLE, "отменить резерв"',
        'Bucket.RESERVED, Bucket.RESERVED, "отменить резерв"',
    ),
    (
        "удалённый пустой ФФ остаётся на экране / непустой пропадает",
        "app/services/queries.py",
        "columns = [w for w in warehouses if not w.is_deleted or w.id in holding]",
        "columns = [w for w in warehouses if not w.is_deleted]",
    ),
]

results = []
for title, rel, old, new in MUTANTS:
    path = ROOT / rel
    original = path.read_text(encoding="utf-8")
    assert old in original, f"не найдено место мутации: {title}"
    path.write_text(original.replace(old, new, 1), encoding="utf-8")
    try:
        proc = subprocess.run(
            [PYTHON, "-m", "pytest", "-x", "-q", "-p", "no:warnings", "--deselect", "tests/test_random_model.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )
        last = [line for line in proc.stdout.splitlines() if line.strip()][-1]
        killed = proc.returncode != 0
        failed_test = next((line for line in proc.stdout.splitlines() if line.startswith("FAILED")), "")
        results.append((killed, title, failed_test[:110] or last))
    finally:
        path.write_text(original, encoding="utf-8")

for killed, title, info in results:
    print(("ПОЙМАН   " if killed else "ПРОПУЩЕН ") + title + "\n         " + info)
sys.exit(0 if all(k for k, _, _ in results) else 1)
