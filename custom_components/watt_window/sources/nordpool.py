"""Nord Pool, through Home Assistant's own Nord Pool integration.

We call the integration's ``get_prices_for_date`` service for a chosen setup and
area; its sensors (and whatever they're named) are never read.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from ..const import CONF_AREA, CONF_NORDPOOL_ENTRY, NORDPOOL_DOMAIN
from ..core import nordpool
from . import PriceFetch, PriceSource, PriceSourceError

_LOGGER = logging.getLogger(__name__)


class NordPoolSource(PriceSource):
    name = "Nord Pool"

    def __init__(self, hass, settings: dict, cache: dict[date, list[dict]]) -> None:
        super().__init__(hass, settings)
        # Prices for a market date never change once published: cached by the caller.
        self._cache = cache

    def _entry(self) -> ConfigEntry | None:
        entries = self.hass.config_entries.async_loaded_entries(NORDPOOL_DOMAIN)
        chosen = self.settings.get(CONF_NORDPOOL_ENTRY)
        for e in entries:
            if e.entry_id == chosen:
                return e
        return entries[0] if entries else None

    async def _market_date(self, entry: ConfigEntry, area: str, d: date) -> list[dict]:
        if self._cache.get(d):
            return self._cache[d]
        try:
            resp = await self.hass.services.async_call(
                NORDPOOL_DOMAIN,
                "get_prices_for_date",
                {"config_entry": entry.entry_id, "date": d.isoformat(), "areas": [area]},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("Nord Pool had no prices for %s yet: %s", d, err)
            return []
        rows = list((resp or {}).get(area) or [])
        if rows:
            self._cache[d] = rows
        return rows

    async def async_fetch(self, now: datetime, start: datetime, end: datetime) -> PriceFetch:
        entry = self._entry()
        if entry is None:
            raise PriceSourceError("The Nord Pool integration is not set up")
        area = self.settings[CONF_AREA]
        today = dt_util.as_local(now).date()

        # Yesterday + today cover today's local day in zones at or ahead of CET
        # (every Nord Pool area). Tomorrow's auction is published around
        # 12:45 CET: don't ask for it before 10:30 UTC, and a published date is
        # cached forever, so a normal day costs a handful of calls.
        wanted = [today - timedelta(days=1), today]
        if now.hour * 60 + now.minute >= 10 * 60 + 30 or today + timedelta(days=1) in self._cache:
            wanted.append(today + timedelta(days=1))
        responses = []
        for d in wanted:
            rows = await self._market_date(entry, area, d)
            if rows:
                responses.append({area: rows})
        for old in [d for d in self._cache if d < today - timedelta(days=1)]:
            del self._cache[old]
        if not responses:
            raise PriceSourceError(f"No Nord Pool prices for area {area}")

        spots = nordpool.splice([iv for r in responses for iv in nordpool.parse_response(r, area)], start, end)
        return PriceFetch(
            spots=spots,
            currency=entry.data.get("currency", "EUR"),
            next_prices_at=nordpool.next_publication(now, bool(self._cache.get(today + timedelta(days=1)))),
        )
