"""Tariff presets: starting values a user can accept or edit.

Rates are ex-VAT, EUR/kWh. Elektrilevi figures come from its published price
list (the incl.-VAT figures divided by 1.24), as billed in August 2026.
"other_per_kwh" is the Estonian renewable fee 0.0084 + security-of-supply
fee 0.00758 + excise 0.0021 + a typical retailer balancing charge 0.00373.
Check your own bill: retailers' margins and balancing charges differ.
"""

from __future__ import annotations

EE_OTHER = 0.0084 + 0.00758 + 0.0021 + 0.00373

PRESETS: dict[str, dict] = {
    "ee_vork1": {
        "label": "Elektrilevi Võrk 1 (Estonia, flat)",
        "network_plan": "flat",
        "network_rates": {"flat": 0.0772},
        "vat": 0.24,
        "other_per_kwh": EE_OTHER,
    },
    "ee_vork2": {
        "label": "Elektrilevi Võrk 2 (Estonia, day/night)",
        "network_plan": "day_night",
        "day_start": 7,
        "day_end": 22,
        "weekends_night": True,
        "holidays_night": True,
        "network_rates": {"day": 0.0607, "night": 0.0351},
        "vat": 0.24,
        "other_per_kwh": EE_OTHER,
    },
    "ee_vork4": {
        "label": "Elektrilevi Võrk 4 (Estonia, day/night)",
        "network_plan": "day_night",
        "day_start": 7,
        "day_end": 22,
        "weekends_night": True,
        "holidays_night": True,
        "network_rates": {"day": 0.0369, "night": 0.0210},
        "vat": 0.24,
        "other_per_kwh": EE_OTHER,
    },
    "ee_vork5": {
        "label": "Elektrilevi Võrk 5 (Estonia, day/night/peak)",
        "network_plan": "vork5",
        "network_rates": {
            "day": 0.0529,
            "night": 0.0303,
            "weekday_peak": 0.0818,
            "weekend_peak": 0.0474,
        },
        "vat": 0.24,
        "other_per_kwh": EE_OTHER,
    },
    "custom_flat": {
        "label": "Custom: one rate",
        "network_plan": "flat",
        "network_rates": {"flat": 0.0},
        "vat": 0.0,
        "other_per_kwh": 0.0,
    },
    "custom_day_night": {
        "label": "Custom: day/night",
        "network_plan": "day_night",
        "day_start": 7,
        "day_end": 22,
        "weekends_night": False,
        "holidays_night": False,
        "network_rates": {"day": 0.0, "night": 0.0},
        "vat": 0.0,
        "other_per_kwh": 0.0,
    },
}


def tariff_from_preset(name: str) -> dict:
    """A fresh tariff config dict from a preset (without its label)."""
    p = dict(PRESETS[name])
    p.pop("label")
    p["network_rates"] = dict(p["network_rates"])
    p.setdefault("margin", 0.0)
    p.setdefault("export_fee", 0.0)
    return p
