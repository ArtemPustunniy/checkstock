"""Правила учёта в одном месте: где может находиться товар и как его перемещают документы.

Модель — журнал проводок. Каждое действие переносит `qty` единиц товара из одного
места (Bucket) в другое. Товар не появляется и не исчезает посередине: он приходит
от поставщика (SUPPLIER) и выбывает к маркетплейсу (MARKETPLACE) или клиенту (CLIENT).

    SUPPLIER ─приход─▶ AVAILABLE(ФФ) ─перемещение─▶ TRANSIT_FF ─приёмка─▶ AVAILABLE(другой ФФ)
                          │  ▲
               резерв FBS │  │ отмена
                          ▼  │
                       RESERVED(ФФ) ─отгрузка─▶ TRANSIT_FBS ─доставка─▶ CLIENT
    AVAILABLE(ФФ) ─отгрузка FBO─▶ TRANSIT_FBO ─приёмка МП─▶ MARKETPLACE

«Физически на ФФ» = AVAILABLE + RESERVED, «доступно» = AVAILABLE, «в резерве FBS» = RESERVED.
"""

from dataclasses import dataclass
from enum import StrEnum


class Bucket(StrEnum):
    SUPPLIER = "supplier"  # внешний источник — приход
    AVAILABLE = "available"  # ФФ: свободный остаток
    RESERVED = "reserved"  # ФФ: заблокировано под заказ FBS
    TRANSIT_FF = "transit_ff"  # в пути между ФФ
    TRANSIT_FBO = "transit_fbo"  # в пути на склад маркетплейса
    TRANSIT_FBS = "transit_fbs"  # в пути к клиенту
    MARKETPLACE = "marketplace"  # принято складом МП — выбыло из учёта ФФ
    CLIENT = "client"  # доставлено клиенту — выбыло из учёта ФФ


# Места, привязанные к конкретному ФФ: по ним ведутся остатки в stock_balances.
FF_BUCKETS = frozenset({Bucket.AVAILABLE, Bucket.RESERVED})
TRANSIT_BUCKETS = frozenset({Bucket.TRANSIT_FF, Bucket.TRANSIT_FBO, Bucket.TRANSIT_FBS})


class DocKind(StrEnum):
    RECEIPT = "receipt"  # приход на ФФ
    TRANSFER = "transfer"  # перемещение между ФФ
    FBO = "fbo"  # отгрузка на склад маркетплейса
    FBS = "fbs"  # заказ FBS: резерв → отгрузка → доставка


class DocStatus(StrEnum):
    DONE = "done"
    IN_TRANSIT = "in_transit"
    RECEIVED = "received"
    ACCEPTED = "accepted"
    RESERVED = "reserved"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Action(StrEnum):
    RECEIPT = "receipt"
    TRANSFER_SEND = "transfer_send"
    TRANSFER_RECEIVE = "transfer_receive"
    FBO_SHIP = "fbo_ship"
    FBO_ACCEPT = "fbo_accept"
    FBS_RESERVE = "fbs_reserve"
    FBS_SHIP = "fbs_ship"
    FBS_CANCEL = "fbs_cancel"
    FBS_DELIVER = "fbs_deliver"


@dataclass(frozen=True)
class Creation:
    """Какой статус получает новый документ и куда уходит товар со свободного остатка ФФ."""

    action: Action
    status: DocStatus
    src: Bucket
    dst: Bucket


CREATION: dict[DocKind, Creation] = {
    DocKind.RECEIPT: Creation(Action.RECEIPT, DocStatus.DONE, Bucket.SUPPLIER, Bucket.AVAILABLE),
    DocKind.TRANSFER: Creation(Action.TRANSFER_SEND, DocStatus.IN_TRANSIT, Bucket.AVAILABLE, Bucket.TRANSIT_FF),
    DocKind.FBO: Creation(Action.FBO_SHIP, DocStatus.IN_TRANSIT, Bucket.AVAILABLE, Bucket.TRANSIT_FBO),
    DocKind.FBS: Creation(Action.FBS_RESERVE, DocStatus.RESERVED, Bucket.AVAILABLE, Bucket.RESERVED),
}


@dataclass(frozen=True)
class Step:
    """Переход документа по статусам и проводка, которая при этом выполняется.

    `done` — статусы, в которых шаг уже сделан. Повторное подтверждение в таком статусе
    ничего не проводит: так «отгрузить» дважды не спишет товар дважды.
    """

    kind: DocKind
    from_status: DocStatus
    to_status: DocStatus
    done: frozenset[DocStatus]
    src: Bucket
    dst: Bucket
    verb: str


STEPS: dict[Action, Step] = {
    Action.TRANSFER_RECEIVE: Step(
        DocKind.TRANSFER,
        DocStatus.IN_TRANSIT,
        DocStatus.RECEIVED,
        frozenset({DocStatus.RECEIVED}),
        Bucket.TRANSIT_FF,
        Bucket.AVAILABLE,
        "принять перемещение",
    ),
    Action.FBO_ACCEPT: Step(
        DocKind.FBO,
        DocStatus.IN_TRANSIT,
        DocStatus.ACCEPTED,
        frozenset({DocStatus.ACCEPTED}),
        Bucket.TRANSIT_FBO,
        Bucket.MARKETPLACE,
        "отметить приёмку на складе МП",
    ),
    Action.FBS_SHIP: Step(
        DocKind.FBS,
        DocStatus.RESERVED,
        DocStatus.SHIPPED,
        frozenset({DocStatus.SHIPPED, DocStatus.DELIVERED}),
        Bucket.RESERVED,
        Bucket.TRANSIT_FBS,
        "отгрузить заказ",
    ),
    Action.FBS_CANCEL: Step(
        DocKind.FBS,
        DocStatus.RESERVED,
        DocStatus.CANCELLED,
        frozenset({DocStatus.CANCELLED}),
        Bucket.RESERVED,
        Bucket.AVAILABLE,
        "отменить резерв",
    ),
    Action.FBS_DELIVER: Step(
        DocKind.FBS,
        DocStatus.SHIPPED,
        DocStatus.DELIVERED,
        frozenset({DocStatus.DELIVERED}),
        Bucket.TRANSIT_FBS,
        Bucket.CLIENT,
        "отметить доставку",
    ),
}

# Статусы, в которых товар документа «в пути» (числится в TRANSIT_*).
IN_TRANSIT_STATUS: dict[DocKind, DocStatus] = {
    DocKind.TRANSFER: DocStatus.IN_TRANSIT,
    DocKind.FBO: DocStatus.IN_TRANSIT,
    DocKind.FBS: DocStatus.SHIPPED,
}
OPEN_STATUSES = frozenset({DocStatus.IN_TRANSIT, DocStatus.RESERVED, DocStatus.SHIPPED})


KIND_LABELS = {
    DocKind.RECEIPT: "Приход",
    DocKind.TRANSFER: "Перемещение",
    DocKind.FBO: "Отгрузка FBO",
    DocKind.FBS: "Заказ FBS",
}
STATUS_LABELS = {
    DocStatus.DONE: "проведён",
    DocStatus.IN_TRANSIT: "в пути",
    DocStatus.RECEIVED: "принято на ФФ",
    DocStatus.ACCEPTED: "принято складом МП",
    DocStatus.RESERVED: "в резерве",
    DocStatus.SHIPPED: "едет к клиенту",
    DocStatus.DELIVERED: "доставлен",
    DocStatus.CANCELLED: "резерв отменён",
}
ACTION_LABELS = {
    Action.RECEIPT: "Приход",
    Action.TRANSFER_SEND: "Перемещение: отправка",
    Action.TRANSFER_RECEIVE: "Перемещение: приёмка",
    Action.FBO_SHIP: "FBO: отгрузка",
    Action.FBO_ACCEPT: "FBO: принято МП",
    Action.FBS_RESERVE: "FBS: резерв",
    Action.FBS_SHIP: "FBS: отгрузка",
    Action.FBS_CANCEL: "FBS: отмена резерва",
    Action.FBS_DELIVER: "FBS: доставлено",
}
