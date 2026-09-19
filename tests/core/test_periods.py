from datetime import datetime, timedelta, timezone

from custom_components.watt_window.core.periods import period_for

TZ = "Europe/Tallinn"
H = timedelta(hours=1)


def at(s: str) -> datetime:
    return datetime.fromisoformat(s)


def test_evening_night_is_tonight():
    now = at("2026-09-21T18:00:00+00:00")  # 21:00 Tallinn
    start, end = period_for(now, TZ, 8, 20, "night", 2 * H)
    assert start == at("2026-09-21T20:00:00+03:00") and end == at("2026-09-22T08:00:00+03:00")


def test_small_hours_are_still_last_nights_night():
    now = at("2026-09-21T00:00:00+00:00")  # 03:00 Tallinn
    start, end = period_for(now, TZ, 8, 20, "night", 2 * H)
    assert start == at("2026-09-20T20:00:00+03:00") and end == at("2026-09-21T08:00:00+03:00")


def test_too_little_left_moves_to_the_next_period():
    now = at("2026-09-21T15:30:00+00:00")  # 18:30 Tallinn: 1.5 h of day left
    start, _ = period_for(now, TZ, 8, 20, "day", 2 * H)
    assert start == at("2026-09-22T08:00:00+03:00")


def test_whole_day_leaves_no_night():
    now = at("2026-09-21T12:00:00+00:00")
    assert period_for(now, TZ, 0, 24, "night", H) is None


def test_dst_change_keeps_local_hours():
    now = at("2026-10-24T18:00:00+00:00")  # the night clocks go back in EE
    start, end = period_for(now, TZ, 8, 20, "night", 2 * H)
    assert start.astimezone(timezone.utc) == at("2026-10-24T17:00:00+00:00")
    assert end - start >= timedelta(hours=12)
