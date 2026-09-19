"""Validation shared by the setup screens and the panel's settings page."""

from __future__ import annotations

from .const import MAX_WINDOW_MINUTES
from .core.tariff import PLANS, rate_keys


class SettingsError(ValueError):
    """A user-facing validation error. ``key`` is a translation key."""

    def __init__(self, key: str, detail: str = "") -> None:
        super().__init__(detail or key)
        self.key = key


def clean_tariff(t: dict) -> dict:
    plan = t.get("network_plan")
    if plan not in PLANS:
        raise SettingsError("bad_plan")
    rates = {}
    for k in rate_keys(plan):
        try:
            rates[k] = float((t.get("network_rates") or {})[k])
        except (KeyError, TypeError, ValueError) as err:
            raise SettingsError("bad_rate", k) from err
    out = {
        "network_plan": plan,
        "network_rates": rates,
        "vat": _fraction(t.get("vat", 0)),
        "margin": float(t.get("margin") or 0),
        "other_per_kwh": float(t.get("other_per_kwh") or 0),
        "export_fee": float(t.get("export_fee") or 0),
    }
    if plan == "day_night":
        start, end = int(t.get("day_start", 7)), int(t.get("day_end", 22))
        if not (0 <= start < end <= 24):
            raise SettingsError("bad_hours")
        out |= {
            "day_start": start,
            "day_end": end,
            "weekends_night": bool(t.get("weekends_night", True)),
            "holidays_night": bool(t.get("holidays_night", True)),
        }
    return out


def _fraction(v) -> float:
    v = float(v or 0)
    if not 0 <= v < 1:
        raise SettingsError("bad_vat")
    return v


def clean_windows(minutes) -> list[int]:
    out = sorted({int(m) for m in minutes})
    if not out:
        raise SettingsError("no_windows")
    for m in out:
        if m < 15 or m > MAX_WINDOW_MINUTES or m % 15:
            raise SettingsError("bad_window", str(m))
    return out


def clean_day_hours(start, end) -> tuple[int, int]:
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError) as err:
        raise SettingsError("bad_day_hours") from err
    if not 0 <= start < end <= 24 or (start == 0 and end == 24):
        raise SettingsError("bad_day_hours")
    return start, end


def parse_hours_list(text: str) -> list[int]:
    """'1, 2, 1.5' (hours) -> [60, 90, 120] minutes."""
    try:
        return clean_windows(round(float(p) * 60) for p in text.replace(";", ",").split(",") if p.strip())
    except ValueError as err:
        if isinstance(err, SettingsError):
            raise
        raise SettingsError("bad_window", text) from err
