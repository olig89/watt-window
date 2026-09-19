"""Where spot prices come from.

A price source hands the coordinator spot prices (per kWh, before any network
rate, fee or VAT) for every interval it knows, plus when it expects to know
more. Everything after that (tariff, solar, Watt Windows) is source-agnostic.

Sources: Nord Pool (Nordics and Baltics, via Home Assistant's own Nord Pool
integration). More plug in here, e.g. a UK source.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from homeassistant.core import HomeAssistant

from ..core.nordpool import SpotInterval


class PriceSourceError(Exception):
    """No usable prices right now (shown to the user as the reason)."""


@dataclass
class PriceFetch:
    spots: list[SpotInterval]  # contiguous, sorted, per kWh
    currency: str
    # When the source expects to publish further prices (None if unknown).
    next_prices_at: datetime | None


class PriceSource(ABC):
    #: Shown on the page ("Nord Pool sets tomorrow's prices ...").
    name: str

    def __init__(self, hass: HomeAssistant, settings: dict) -> None:
        self.hass = hass
        self.settings = settings

    @abstractmethod
    async def async_fetch(self, now: datetime, start: datetime, end: datetime) -> PriceFetch:
        """Spot prices from ``start`` up to at most ``end`` (UTC). Raise PriceSourceError."""


def price_source(hass: HomeAssistant, settings: dict, cache: dict) -> PriceSource:
    """The configured source. ``cache`` survives between refreshes (owned by the coordinator)."""
    from .nordpool import NordPoolSource

    kind = settings.get("price_source", "nordpool")
    if kind == "nordpool":
        return NordPoolSource(hass, settings, cache)
    raise PriceSourceError(f"Unknown price source: {kind}")
