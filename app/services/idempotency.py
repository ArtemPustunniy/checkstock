"""Идемпотентность создающих запросов (приход, перемещение, FBO, резерв FBS).

Клиент присылает заголовок Idempotency-Key — UUID, который UI генерирует при открытии формы.
Ключ записывается в той же транзакции, что и операция:

- первый запрос вставляет ключ, проводит операцию и сохраняет ответ;
- повтор с тем же ключом упирается в PRIMARY KEY. Если первый ещё выполняется, Postgres
  ждёт его коммита, после чего повтор получает сохранённый ответ и ничего не проводит;
- если операция упала (например, нехватка), откатывается и ключ — повтор выполнится заново.
"""

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import InvalidRequest
from app.models import IdempotencyKey

REPLAY_HEADER = "Idempotent-Replayed"


def run(
    session: Session,
    key: str | None,
    endpoint: str,
    request: BaseModel,
    operation: Callable[[], dict[str, Any]],
    status_code: int = 201,
) -> JSONResponse:
    if not key:
        with session.begin():
            body = jsonable_encoder(operation())
        return JSONResponse(body, status_code=status_code)

    request_hash = hashlib.sha256(
        json.dumps(request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    try:
        with session.begin():
            record = IdempotencyKey(key=key, endpoint=endpoint, request_hash=request_hash)
            session.add(record)
            session.flush()  # здесь повтор с тем же ключом ждёт первый запрос или падает на PK
            body = jsonable_encoder(operation())
            record.status_code = status_code
            record.response = body
        return JSONResponse(body, status_code=status_code)
    except IntegrityError as exc:
        if getattr(exc.orig.diag, "constraint_name", None) != "pk_idempotency_keys":
            raise
        session.rollback()

    saved = session.get(IdempotencyKey, key)
    if saved.endpoint != endpoint or saved.request_hash != request_hash:
        raise InvalidRequest("Этот Idempotency-Key уже использован для другого запроса", code="idempotency_key_reused")
    return JSONResponse(saved.response, status_code=saved.status_code, headers={REPLAY_HEADER: "true"})
