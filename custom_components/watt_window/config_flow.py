"""Setup and options screens.

Setup:   1 area + tariff preset + holiday country -> 2 tariff details -> 3 solar
Options: the same tariff and solar steps (prefilled), then window lengths.
The sidebar page edits the same settings; both write the entry's options.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    ConfigEntrySelector,
    ConfigEntrySelectorConfig,
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
    CONF_LOAD_W,
    CONF_PRESET,
    CONF_SOLAR_ENTRY,
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


def _solar_schema(s: dict) -> vol.Schema:
    fields: dict[Any, Any] = {}
    solar_key = (
        vol.Optional(CONF_SOLAR_ENTRY, description={"suggested_value": s[CONF_SOLAR_ENTRY]})
        if s.get(CONF_SOLAR_ENTRY)
        else vol.Optional(CONF_SOLAR_ENTRY)
    )
    fields[solar_key] = ConfigEntrySelector(ConfigEntrySelectorConfig(integration=FORECAST_SOLAR_DOMAIN))
    fields[vol.Required(CONF_BASE_LOAD_W, default=s.get(CONF_BASE_LOAD_W, DEFAULT_BASE_LOAD_W))] = _watts()
    fields[vol.Required(CONF_LOAD_W, default=s.get(CONF_LOAD_W, DEFAULT_LOAD_W))] = _watts()
    return vol.Schema(fields)


class WattWindowConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return WattWindowOptionsFlow()

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        np_entries = self.hass.config_entries.async_entries(NORDPOOL_DOMAIN)
        if not np_entries:
            return self.async_abort(reason="no_nordpool")
        areas = list(np_entries[0].data.get("areas") or [])
        if user_input is not None:
            self._data = {
                CONF_AREA: user_input[CONF_AREA],
                CONF_COUNTRY: (user_input.get(CONF_COUNTRY) or "").strip().upper(),
                CONF_PRESET: user_input[CONF_PRESET],
                CONF_TARIFF: tariff_from_preset(user_input[CONF_PRESET]),
            }
            return await self.async_step_tariff()
        default_country = self.hass.config.country or ""
        default_preset = "ee_vork2" if default_country == "EE" else "custom_day_night"
        schema = vol.Schema(
            {
                vol.Required(CONF_AREA, default=areas[0] if areas else None): SelectSelector(
                    SelectSelectorConfig(options=areas, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(CONF_PRESET, default=default_preset): _preset_selector(False),
                vol.Optional(CONF_COUNTRY, default=default_country): TextSelector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_tariff(self, user_input: dict | None = None) -> ConfigFlowResult:
        t = self._data[CONF_TARIFF]
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._data[CONF_TARIFF] = _tariff_from_input(t["network_plan"], user_input)
                return await self.async_step_solar()
            except SettingsError as err:
                errors["base"] = err.key
        return self.async_show_form(step_id="tariff", data_schema=_tariff_schema(t), errors=errors)

    async def async_step_solar(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(
                {
                    CONF_SOLAR_ENTRY: user_input.get(CONF_SOLAR_ENTRY) or None,
                    CONF_BASE_LOAD_W: user_input[CONF_BASE_LOAD_W],
                    CONF_LOAD_W: user_input[CONF_LOAD_W],
                    CONF_WINDOWS: list(DEFAULT_WINDOWS),
                }
            )
            return self.async_create_entry(title=NAME, data=self._data)
        return self.async_show_form(step_id="solar", data_schema=_solar_schema(self._data))


class WattWindowOptionsFlow(OptionsFlow):
    def __init__(self) -> None:
        self._opts: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        current = settings_of(self.config_entry)
        if user_input is not None:
            self._opts = dict(self.config_entry.options)
            if user_input[CONF_PRESET] != KEEP:
                self._opts[CONF_PRESET] = user_input[CONF_PRESET]
                self._opts[CONF_TARIFF] = tariff_from_preset(user_input[CONF_PRESET])
            else:
                self._opts[CONF_TARIFF] = current[CONF_TARIFF]
            self._opts[CONF_COUNTRY] = (user_input.get(CONF_COUNTRY) or "").strip().upper()
            return await self.async_step_tariff()
        schema = vol.Schema(
            {
                vol.Required(CONF_PRESET, default=KEEP): _preset_selector(True),
                vol.Optional(CONF_COUNTRY, default=current.get(CONF_COUNTRY) or ""): TextSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_tariff(self, user_input: dict | None = None) -> ConfigFlowResult:
        t = self._opts[CONF_TARIFF]
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._opts[CONF_TARIFF] = _tariff_from_input(t["network_plan"], user_input)
                return await self.async_step_solar()
            except SettingsError as err:
                errors["base"] = err.key
        return self.async_show_form(step_id="tariff", data_schema=_tariff_schema(t), errors=errors)

    async def async_step_solar(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._opts[CONF_SOLAR_ENTRY] = user_input.get(CONF_SOLAR_ENTRY) or None
            self._opts[CONF_BASE_LOAD_W] = user_input[CONF_BASE_LOAD_W]
            self._opts[CONF_LOAD_W] = user_input[CONF_LOAD_W]
            return await self.async_step_windows()
        return self.async_show_form(step_id="solar", data_schema=_solar_schema(settings_of(self.config_entry)))

    async def async_step_windows(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._opts[CONF_WINDOWS] = parse_hours_list(user_input["hours"])
                return self.async_create_entry(data=self._opts)
            except SettingsError as err:
                errors["base"] = err.key
        current = settings_of(self.config_entry).get(CONF_WINDOWS) or DEFAULT_WINDOWS
        text = ", ".join(f"{m / 60:g}" for m in current)
        return self.async_show_form(
            step_id="windows",
            data_schema=vol.Schema({vol.Required("hours", default=text): TextSelector()}),
            errors=errors,
        )
