"""Setup and options screens.

Setup:   area + preset + holiday country -> tariff details
         -> solar? (pick Forecast.Solar + house load / skip) -> battery? (yes / skip)
The appliance wattage for cost estimates defaults to 1000 W and is edited on the sidebar page.
Options: the same steps (prefilled), then window lengths.
Nord Pool is required (it is the price source); solar and battery can be skipped.
The sidebar page edits the same settings; both write the entry's options.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_AREA,
    CONF_BASE_LOAD_W,
    CONF_COUNTRY,
    CONF_HAS_BATTERY,
    CONF_LOAD_W,
    CONF_NORDPOOL_ENTRY,
    CONF_PRESET,
    CONF_SOLAR_ENTRIES,
    CONF_TARIFF,
    CONF_WINDOWS,
    DEFAULT_BASE_LOAD_W,
    DEFAULT_LOAD_W,
    DEFAULT_WINDOWS,
    DOMAIN,
    FORECAST_SOLAR_DOMAIN,
    NAME,
    NORDPOOL_DOMAIN,
    settings_of,
    solar_entry_ids,
    solar_entry_label,
)
from .core.presets import PRESETS, tariff_from_preset
from .core.tariff import rate_keys
from .validation import SettingsError, clean_tariff, parse_hours_list

KEEP = "keep"


def _money() -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(min=-1, max=10, step="any", mode=NumberSelectorMode.BOX))


def _watts() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(min=0, max=50000, step=50, unit_of_measurement="W", mode=NumberSelectorMode.BOX)
    )


def _preset_selector(with_keep: bool) -> SelectSelector:
    options = [SelectOptionDict(value=k, label=v["label"]) for k, v in PRESETS.items()]
    if with_keep:
        options.insert(0, SelectOptionDict(value=KEEP, label="Keep my current tariff"))
    return SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN))


def _tariff_schema(t: dict) -> vol.Schema:
    fields: dict[Any, Any] = {}
    for k in rate_keys(t["network_plan"]):
        fields[vol.Required(f"rate_{k}", default=t["network_rates"].get(k, 0.0))] = _money()
    if t["network_plan"] == "day_night":
        hours = NumberSelector(NumberSelectorConfig(min=0, max=24, step=1, mode=NumberSelectorMode.BOX))
        fields[vol.Required("day_start", default=t.get("day_start", 7))] = hours
        fields[vol.Required("day_end", default=t.get("day_end", 22))] = hours
        fields[vol.Required("weekends_night", default=t.get("weekends_night", True))] = BooleanSelector()
        fields[vol.Required("holidays_night", default=t.get("holidays_night", True))] = BooleanSelector()
    fields[vol.Required("vat_percent", default=round(float(t.get("vat", 0)) * 100, 2))] = NumberSelector(
        NumberSelectorConfig(min=0, max=99, step=0.1, unit_of_measurement="%", mode=NumberSelectorMode.BOX)
    )
    fields[vol.Required("margin", default=t.get("margin", 0.0))] = _money()
    fields[vol.Required("other_per_kwh", default=t.get("other_per_kwh", 0.0))] = _money()
    fields[vol.Required("export_fee", default=t.get("export_fee", 0.0))] = _money()
    return vol.Schema(fields)


def _tariff_from_input(plan: str, user: dict) -> dict:
    t = {
        "network_plan": plan,
        "network_rates": {k: user[f"rate_{k}"] for k in rate_keys(plan)},
        "vat": float(user["vat_percent"]) / 100,
        "margin": user["margin"],
        "other_per_kwh": user["other_per_kwh"],
        "export_fee": user["export_fee"],
    }
    for k in ("day_start", "day_end", "weekends_night", "holidays_night"):
        if k in user:
            t[k] = user[k]
    return clean_tariff(t)


def _solar_schema(hass, s: dict) -> vol.Schema:
    options = [
        SelectOptionDict(value=e.entry_id, label=solar_entry_label(e))
        for e in hass.config_entries.async_entries(FORECAST_SOLAR_DOMAIN)
    ]
    chosen = solar_entry_ids(s) or [o["value"] for o in options]
    return vol.Schema(
        {
            vol.Required(CONF_SOLAR_ENTRIES, default=chosen): SelectSelector(
                SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
            ),
            # Only matters with solar: the panels cover this first, the rest is spare.
            vol.Required(CONF_BASE_LOAD_W, default=s.get(CONF_BASE_LOAD_W, DEFAULT_BASE_LOAD_W)): _watts(),
        }
    )


class _SharedSteps:
    """Steps both flows share: tariff, and the yes/skip questions for solar and battery.

    Answers collect in ``self._store``; each flow finishes in its own ``_after_battery``.
    """

    _store: dict[str, Any]

    async def async_step_tariff(self, user_input: dict | None = None) -> ConfigFlowResult:
        t = self._store[CONF_TARIFF]
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._store[CONF_TARIFF] = _tariff_from_input(t["network_plan"], user_input)
                return await self.async_step_solar_menu()
            except SettingsError as err:
                errors["base"] = err.key
        return self.async_show_form(step_id="tariff", data_schema=_tariff_schema(t), errors=errors)

    async def async_step_solar_menu(self, user_input: dict | None = None) -> ConfigFlowResult:
        # Only offer "use solar" when there's a Forecast.Solar setup to pick.
        has_forecast = bool(self.hass.config_entries.async_entries(FORECAST_SOLAR_DOMAIN))
        return self.async_show_menu(
            step_id="solar_menu",
            menu_options=["solar", "skip_solar"] if has_forecast else ["skip_solar"],
            description_placeholders={
                "note": "" if has_forecast else
                "\n\n**No Forecast.Solar setup found.** [Add Forecast.Solar](/config/integrations/dashboard/add?domain=forecast_solar) "
                "then turn solar on from Watt Window's Settings tab. Skip for now."
            },
        )

    async def async_step_solar(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._store[CONF_SOLAR_ENTRIES] = list(user_input[CONF_SOLAR_ENTRIES])
            self._store[CONF_BASE_LOAD_W] = user_input[CONF_BASE_LOAD_W]
            return await self.async_step_battery_menu()
        return self.async_show_form(step_id="solar", data_schema=_solar_schema(self.hass, self._store))

    async def async_step_skip_solar(self, user_input: dict | None = None) -> ConfigFlowResult:
        self._store[CONF_SOLAR_ENTRIES] = []
        return await self.async_step_battery_menu()

    async def async_step_battery_menu(self, user_input: dict | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="battery_menu", menu_options=["battery", "skip_battery"])

    async def async_step_battery(self, user_input: dict | None = None) -> ConfigFlowResult:
        self._store[CONF_HAS_BATTERY] = True
        return await self._after_battery()

    async def async_step_skip_battery(self, user_input: dict | None = None) -> ConfigFlowResult:
        self._store[CONF_HAS_BATTERY] = False
        return await self._after_battery()

    async def _after_battery(self) -> ConfigFlowResult:
        raise NotImplementedError


class WattWindowConfigFlow(_SharedSteps, ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return WattWindowOptionsFlow()

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        np_entries = self.hass.config_entries.async_entries(NORDPOOL_DOMAIN)
        if not np_entries:
            return self.async_abort(reason="no_nordpool")
        # One choice per (Nord Pool setup, area); the setup is named only if there are several.
        choices = [
            SelectOptionDict(
                value=f"{e.entry_id}|{a}",
                label=a if len(np_entries) == 1 else f"{a} ({e.title or 'Nord Pool'})",
            )
            for e in np_entries
            for a in (e.data.get("areas") or [])
        ]
        if user_input is not None:
            entry_id, _, area = user_input[CONF_AREA].rpartition("|")
            self._store = {
                CONF_AREA: area,
                CONF_NORDPOOL_ENTRY: entry_id or np_entries[0].entry_id,
                CONF_COUNTRY: (user_input.get(CONF_COUNTRY) or "").strip().upper(),
                CONF_PRESET: user_input[CONF_PRESET],
                CONF_TARIFF: tariff_from_preset(user_input[CONF_PRESET]),
            }
            return await self.async_step_tariff()
        default_country = self.hass.config.country or ""
        default_preset = "ee_vork2" if default_country == "EE" else "custom_day_night"
        schema = vol.Schema(
            {
                vol.Required(CONF_AREA, default=choices[0]["value"] if choices else None): SelectSelector(
                    SelectSelectorConfig(options=choices, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(CONF_PRESET, default=default_preset): _preset_selector(False),
                vol.Optional(CONF_COUNTRY, default=default_country): TextSelector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def _after_battery(self) -> ConfigFlowResult:
        self._store[CONF_WINDOWS] = list(DEFAULT_WINDOWS)
        self._store.setdefault(CONF_BASE_LOAD_W, DEFAULT_BASE_LOAD_W)
        self._store.setdefault(CONF_LOAD_W, DEFAULT_LOAD_W)
        return self.async_create_entry(title=NAME, data=self._store)


class WattWindowOptionsFlow(_SharedSteps, OptionsFlow):
    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        current = settings_of(self.config_entry)
        if user_input is not None:
            self._store = dict(self.config_entry.options)
            self._store[CONF_TARIFF] = current[CONF_TARIFF]
            self._store[CONF_WINDOWS] = current.get(CONF_WINDOWS) or list(DEFAULT_WINDOWS)
            for key in (CONF_BASE_LOAD_W, CONF_LOAD_W, CONF_HAS_BATTERY):
                self._store.setdefault(key, current.get(key))
            self._store[CONF_SOLAR_ENTRIES] = solar_entry_ids(current)
            if user_input[CONF_PRESET] != KEEP:
                self._store[CONF_PRESET] = user_input[CONF_PRESET]
                self._store[CONF_TARIFF] = tariff_from_preset(user_input[CONF_PRESET])
            self._store[CONF_COUNTRY] = (user_input.get(CONF_COUNTRY) or "").strip().upper()
            return await self.async_step_tariff()
        schema = vol.Schema(
            {
                vol.Required(CONF_PRESET, default=KEEP): _preset_selector(True),
                vol.Optional(CONF_COUNTRY, default=current.get(CONF_COUNTRY) or ""): TextSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def _after_battery(self) -> ConfigFlowResult:
        return await self.async_step_windows()

    async def async_step_windows(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._store[CONF_WINDOWS] = parse_hours_list(user_input["hours"])
                return self.async_create_entry(data=self._store)
            except SettingsError as err:
                errors["base"] = err.key
        text = ", ".join(f"{m / 60:g}" for m in self._store[CONF_WINDOWS])
        return self.async_show_form(
            step_id="windows",
            data_schema=vol.Schema({vol.Required("hours", default=text): TextSelector()}),
            errors=errors,
        )
