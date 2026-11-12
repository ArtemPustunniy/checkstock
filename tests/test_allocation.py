"""Юнит-тесты выбора вариантов при нехватке (чистая функция)."""

from app.services.allocation import suggest

NORTH, SOUTH, EAST = 1, 2, 3


def test_whole_from_other_ff_and_split():
    assert suggest(8, {NORTH: 10, SOUTH: 6}, chosen=SOUTH) == [((NORTH, 8),), ((SOUTH, 6), (NORTH, 2))]


def test_chosen_empty_gives_single_option():
    assert suggest(3, {NORTH: 5, SOUTH: 0}, chosen=SOUTH) == [((NORTH, 3),)]


def test_split_takes_from_fullest_first():
    assert suggest(9, {NORTH: 4, SOUTH: 3, EAST: 5}, chosen=SOUTH) == [((SOUTH, 3), (EAST, 5), (NORTH, 1))]


def test_no_single_ff_and_chosen_empty_splits_others():
    assert suggest(7, {NORTH: 4, SOUTH: 0, EAST: 5}, chosen=SOUTH) == [((EAST, 5), (NORTH, 2))]


def test_not_enough_in_total():
    assert suggest(20, {NORTH: 10, SOUTH: 6}, chosen=SOUTH) == []


def test_every_option_sums_to_requested_and_fits():
    available = {NORTH: 7, SOUTH: 2, EAST: 9}
    for requested in range(1, sum(available.values()) + 1):
        for chosen in available:
            for option in suggest(requested, available, chosen):
                assert sum(qty for _, qty in option) == requested
                assert all(0 < qty <= available[wh] for wh, qty in option)
                assert len({wh for wh, _ in option}) == len(option)
