"""Spot price -> what a kWh actually costs you, per interval.

Delivered import price (all EUR/kWh, ex-VAT inside the bracket):

    (spot + margin + network[period] + other_per_kwh) * (1 + vat)

Export is spot minus any export fee: no VAT, no network, no margin. So a kWh
you would otherwise export is worth far less than one you buy, which is why
solar surplus makes an interval cheap.

Monthly fees are fixed and never change WHEN to use power, so they are not
modelled. Every rate is stored ex-VAT; VAT is applied once, here.
Configuration is a plain dict; this module never reads a file or an entity.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import date, datetime

from .tariff import tariff_key


def per_kwh_adders(cfg: Mapping) -> float:
    """Every per-kWh charge except spot and network, ex-VAT."""
    return float(cfg.get("margin", 0.0) or 0.0) + float(cfg.get("other_per_kwh", 0.0) or 0.0)


def import_price(
    spot: float, t: datetime, cfg: Mapping, holidays: Collection[date] = ()
) -> float:
    key = tariff_key(t, cfg, holidays)
    rates = cfg.get("network_rates") or {}
    if key not in rates:
        raise KeyError(f"network_rates has no rate for {key!r} (plan {cfg.get('network_plan')!r})")
    ex_vat = spot + float(rates[key]) + per_kwh_adders(cfg)
    return ex_vat * (1 + float(cfg.get("vat", 0.0) or 0.0))


def export_price(spot: float, cfg: Mapping) -> float:
    return spot - float(cfg.get("export_fee", 0.0) or 0.0)
