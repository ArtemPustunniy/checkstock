import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from app.api import demo, operations, stock, warehouses
from app.errors import DomainError

log = logging.getLogger("checkstock")
STATIC = Path(__file__).parent / "static"

app = FastAPI(
    title="CheckStock",
    version="1.0.0",
    description=(
        "Где сейчас товар: остатки по ФФ, резерв FBS, товар в пути между ФФ, на склад МП и к клиенту.\n\n"
        "Создающие запросы принимают заголовок `Idempotency-Key`. Шаги документов (приёмка, отгрузка, "
        "отмена, доставка) идемпотентны сами по себе: повтор возвращает `changed: false`."
    ),
)
app.include_router(stock.router)
app.include_router(operations.router)
app.include_router(warehouses.router)
app.include_router(demo.router)


@app.exception_handler(DomainError)
def domain_error(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(exc.to_dict(), status_code=exc.status_code)


# Страховка на уровне БД: сюда попадаем, только если гонку не поймали блокировки в коде.
CONSTRAINT_MESSAGES = {
    "ck_stock_balances_available_non_negative": "Остаток изменился во время операции — обновите данные и повторите.",
    "ck_stock_balances_reserved_non_negative": "Резерв изменился во время операции — обновите данные и повторите.",
    "uq_warehouses_active_name": "Действующий ФФ с таким названием уже есть.",
}


@app.exception_handler(IntegrityError)
def integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint in CONSTRAINT_MESSAGES:
        return JSONResponse({"error": "conflict", "message": CONSTRAINT_MESSAGES[constraint]}, status_code=409)
    log.exception("Нарушено ограничение БД: %s", constraint)
    return JSONResponse({"error": "integrity_error", "message": "Операция нарушает ограничения базы данных."}, 500)


VALIDATION_TEXT = {
    "missing": "обязательное поле",
    "greater_than": "должно быть больше 0",
    "less_than_equal": "слишком большое значение",
    "int_parsing": "нужно целое число",
    "string_too_short": "не может быть пустым",
    "string_too_long": "слишком длинное значение",
    "extra_forbidden": "лишнее поле",
}


@app.exception_handler(RequestValidationError)
def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "type": e["type"], "message": e["msg"]}
        for e in exc.errors()
    ]
    parts = []
    for e in errors:
        text = VALIDATION_TEXT.get(e["type"]) or e["message"].removeprefix("Value error, ")
        parts.append(f"{e['field']}: {text}" if e["field"] else text)
    return JSONResponse(
        {"error": "invalid_request", "message": "Проверьте данные — " + "; ".join(parts), "fields": errors},
        status_code=422,
    )


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}
