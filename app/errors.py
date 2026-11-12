"""Доменные ошибки. Каждая знает свой HTTP-статус; обработчик в main.py превращает их в JSON
вида {"error": "<код>", "message": "<текст для менеджера>", ...детали}."""

from typing import Any


class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str, *, code: str | None = None, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.details}


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    """Действие невозможно в текущем состоянии: удалённый ФФ, неверный статус документа и т. п."""

    status_code = 409
    code = "conflict"


class Shortage(Conflict):
    """Не хватает доступного остатка. В деталях — где сколько есть и варианты распределения."""

    code = "shortage"


class InvalidRequest(DomainError):
    status_code = 422
    code = "invalid_request"
