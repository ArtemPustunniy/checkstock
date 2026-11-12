"""Варианты, откуда взять товар, если на выбранном ФФ его не хватает. Чистая функция, без БД.

Система ничего не перераспределяет молча: она предлагает варианты, а менеджер выбирает.
Со склада, где товара нет, отгрузку не придумываем; с удалённого ФФ брать можно —
товар там физически лежит, и вывезти его как раз нужно.
"""

Allocation = tuple[tuple[int, int], ...]  # ((warehouse_id, qty), ...)


def suggest(requested: int, available: dict[int, int], chosen: int | None) -> list[Allocation]:
    """Вернёт варианты распределения `requested` единиц по складам.

    1. Каждый другой ФФ, на котором хватает целиком — от самого полного к менее полному.
    2. Разделение: всё доступное с выбранного ФФ, недостающее — с других, начиная с самых полных.

    Если суммарно доступно меньше `requested`, вариантов нет — нужен приход или меньшее количество.
    """
    pool = {wh: qty for wh, qty in available.items() if qty > 0}
    if sum(pool.values()) < requested:
        return []

    by_size = sorted(pool.items(), key=lambda item: (-item[1], item[0]))
    options: list[Allocation] = [((wh, requested),) for wh, qty in by_size if wh != chosen and qty >= requested]

    split: list[tuple[int, int]] = []
    rest = requested
    if chosen in pool:
        take = min(pool[chosen], rest)
        split.append((chosen, take))
        rest -= take
    for wh, qty in by_size:
        if rest == 0:
            break
        if wh == chosen:
            continue
        take = min(qty, rest)
        split.append((wh, take))
        rest -= take

    if tuple(split) not in options:
        options.append(tuple(split))
    return options
