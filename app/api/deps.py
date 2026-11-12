from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.db import get_session

SessionDep = Annotated[Session, Depends(get_session)]

IdempotencyKeyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        max_length=100,
        description="UUID запроса. Повтор с тем же ключом вернёт первый ответ и ничего не проведёт повторно.",
    ),
]


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """plural(3, ("строка", "строки", "строк")) → "строки"."""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]
