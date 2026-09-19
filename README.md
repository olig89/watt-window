# Watt Window

Finds the cheapest hours to run things in Home Assistant, using Nord Pool spot prices, your own tariff, and an optional solar forecast.

You choose window lengths (1, 2, 4 and 6 hours by default, plus any lengths you add). For each one, Watt Window tells you when the cheapest block starts and gives you a sensor that is **on** while that block is running. Point your automations at those sensors to start the dishwasher, washing machine, heat pump boost, car or battery charging, and so on.

Watt Window never switches anything itself. It only provides sensors, so your automations stay in charge.

## What it takes into account

- **Spot prices** from the built-in [Nord Pool](https://www.home-assistant.io/integrations/nordpool/) integration, at 15-minute resolution. Tomorrow's prices are fetched once they are published, usually early afternoon.
- **Your tariff:** a network rate that can be flat, day/night, or day/night with winter peaks (Estonia's Võrk 5), plus supplier margin, other per-kWh charges and VAT. Presets for Elektrilevi Võrk 1, 2, 4 and 5 are included. Anyone else can enter their own rates.
- **Public holidays** for day/night tariffs that charge the night rate on holidays, from your country code.
- **Solar (optional)** from the built-in [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/) integration. Your panels first cover your typical house load. Any spare output then covers the load you want to run, and that share costs only the export price you'd otherwise have earned. So a sunny midday can beat a cheap night, and it only does when it really is cheaper.

Once a window has started, it stays put. A price update mid-window will not move it.

## Install

1. In HACS, open the menu (⋮) → **Custom repositories**. Add `https://github.com/olig89/watt-window` with type **Integration**.
2. Search HACS for **Watt Window** (the main list only shows what you've already downloaded), open it and download it. Restart Home Assistant.
3. Make sure **Nord Pool** is set up (Settings → Devices & services → Add integration → Nord Pool). Set up **Forecast.Solar** too if you have panels.
4. Add the **Watt Window** integration and follow the three steps: price area and tariff preset, tariff details, solar and loads.

**Watt Window** then appears in the sidebar (admins only).

## Updates

HACS shows an update when a new release is published. Download it and restart Home Assistant.

## The sidebar page

- **Overview:** the price now, each window with its start time, average price, solar share and estimated cost, and a chart of the next two days of prices with the solar forecast and your chosen window shaded.
- **Settings:** add or remove window lengths, change the load and house-load watts, and edit your tariff rates. Changes apply straight away.

To change the tariff type (for example moving from a flat rate to day/night) or the solar forecast, use **Settings → Devices & services → Watt Window → Configure**.

## Entities

| Entity | What it is |
|---|---|
| `sensor.watt_window_price_now` | Delivered import price now, per kWh. Attributes: tariff period, spot price, when prices are known until |
| `sensor.watt_window_export_price_now` | What exporting a kWh earns now |
| `sensor.watt_window_price_now_after_solar` | Cost of running your load now, after the solar forecast |
| `sensor.watt_window_solar_forecast_now` | Forecast solar output now, W (only with solar) |
| `sensor.watt_window_cheapest_<length>_window` | Start of the cheapest window of that length. Attributes: `end`, `average_price`, `average_import_price`, `solar_share`, `estimated_cost` |
| `binary_sensor.watt_window_in_cheapest_<length>_window` | On while that window is running |

`<length>` is written like `1_h`, `1_5_h` or `45_min`.

## Example: start the dishwasher in the cheapest 2 hours

```yaml
automation:
  - alias: Dishwasher in the cheapest 2 hours
    triggers:
      - trigger: state
        entity_id: binary_sensor.watt_window_in_cheapest_2_h_window
        to: "on"
    conditions:
      - condition: state
        entity_id: input_boolean.dishwasher_loaded
        state: "on"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.dishwasher_remote_start
      - action: input_boolean.turn_off
        target:
          entity_id: input_boolean.dishwasher_loaded
```

## Home battery

There's an **I have a home battery** switch (in setup, Configure and the Settings tab). It's saved but doesn't change anything yet. With a battery, spare solar can be stored for later instead of used straight away, which changes which window is cheapest. That logic comes in a later version. Leave it off for now.

## Limits

- Needs Home Assistant 2026.9 or later and the Nord Pool integration.
- Windows are searched within the prices that are known. Before tomorrow's prices are published, the search only reaches the end of today.
- Solar is a forecast. On a cloudy surprise, the window will already have been chosen.

## Licence

MIT
