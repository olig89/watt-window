# Watt Window

Finds the cheapest hours to run things in Home Assistant, using Nord Pool spot prices, your own tariff, and an optional solar forecast.

Each block of cheap hours it finds is a **Watt Window**. You choose the Watt Window lengths (1, 2, 4 and 6 hours by default, plus any lengths you add). For each one, Watt Window tells you when the cheapest block starts and gives you a sensor that is **on** while that block is running. Point your automations at those sensors to start the dishwasher, washing machine, heat pump boost, car or battery charging, and so on.

Watt Window never switches anything itself. It only provides sensors, so your automations stay in charge.

## What it takes into account

- **Spot prices** from the built-in [Nord Pool](https://www.home-assistant.io/integrations/nordpool/) integration, at 15-minute resolution. Tomorrow's prices are fetched once they are published, usually early afternoon.
- **Your tariff:** a network rate that can be flat, day/night, or day/night with winter peaks (Estonia's Võrk 5), plus supplier margin, other per-kWh charges and VAT. Presets for Elektrilevi Võrk 1, 2, 4 and 5 are included. Anyone else can enter their own rates.
- **Public holidays** for day/night tariffs that charge the night rate on holidays, from your country code.
- **Solar (optional)** from the built-in [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/) integration. If you tick more than one Forecast.Solar setup, their forecasts are added up. (Forecast.Solar needs a paid API key for more than one roof plane in a setup.) Your panels first cover your typical house load. Any spare output then covers the load you want to run, and that share costs only the export price you'd otherwise have earned. So a sunny midday can beat a cheap night, and it only does when it really is cheaper.

When prices are flat for a while (common at night, or on a day/night tariff at weekends), several windows can start at the same time: ties go to the earliest start. The sidebar page then says how late you could start for the same price, and the `latest_same_price_start` attribute lets an automation use that.

Each length also gets a cheapest **daytime** and cheapest **overnight** window, for jobs that must happen in one or the other. The day runs 08:00-20:00 by default (change it on the Settings tab); a window is searched in the current day or night if enough of it is left, otherwise in the next one.

**How far ahead it can see:** only as far as prices are published. Nord Pool's day-ahead auction closes at 12:00 CET and prices appear at about 12:45 CET for the next day (midnight to midnight CET). So from early afternoon you can see to the end of tomorrow; in the morning, only to the end of today. The page says when the next prices are due, and the daytime/overnight sensors have a `settled` attribute that stays `false` while part of their period isn't priced yet.

Watt Window reads prices through the Nord Pool integration's own price service, not its entities, so it doesn't matter what your Nord Pool sensors are called. If you have more than one Nord Pool setup, you pick which one (and which area) during setup.

Once a window has started, it stays put. A price update mid-window will not move it.

## Install

1. In HACS, open the menu (⋮) → **Custom repositories**. Add `https://github.com/olig89/watt-window` with type **Integration**.
2. Search HACS for **Watt Window** (the main list only shows what you've already downloaded), open it and download it. Restart Home Assistant.
3. Make sure **Nord Pool** is set up (Settings → Devices & services → Add integration → Nord Pool). Set up **Forecast.Solar** too if you have panels.
4. Add the **Watt Window** integration: price area and tariff preset, tariff details, then two questions you can skip: solar panels and home battery.

**Nord Pool is required** (it's where the prices come from). Solar and battery are optional and can be turned on or off later under **Configure**.

**Watt Window** then appears in the sidebar (admins only).

## Updates

HACS shows an update when a new release is published. Download it and restart Home Assistant.

## The sidebar page

- **Overview:** the price now, each window with its start time, average price, solar share and estimated cost, and a chart of the next two days of prices with the solar forecast and your chosen window shaded.
- **Settings:** window lengths, your day hours, which Forecast.Solar setups to use, home battery, appliance watts, and your tariff rates. Changes apply straight away.

To change the tariff type (for example moving from a flat rate to day/night) or the solar forecast, use **Settings → Devices & services → Watt Window → Configure**.

## Entities

| Entity | What it is |
|---|---|
| `sensor.watt_window_price_now` | Delivered import price now, per kWh. Attributes: tariff period, spot price, when prices are known until |
| `sensor.watt_window_export_price_now` | What exporting a kWh earns now |
| `sensor.watt_window_price_now_after_solar` | Cost of running your load now, after the solar forecast |
| `sensor.watt_window_solar_forecast_now` | Forecast solar output now, W (only with solar) |
| `sensor.watt_window_cheapest_<length>_window` | Start of the cheapest window of that length. Attributes: `end`, `average_price`, `average_import_price`, `solar_share`, `estimated_cost`, `latest_same_price_start` |
| `binary_sensor.watt_window_in_cheapest_<length>_window` | On while that window is running |
| `sensor.watt_window_cheapest_daytime_<length>_window`, `..._overnight_...` | The same, within the day or the night. Extra attributes: `period_start`, `period_end` |
| `binary_sensor.watt_window_in_cheapest_daytime_<length>_window`, `..._overnight_...` | On while that window is running |

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

Setup asks whether you have a home battery (you can skip it), and there's a matching switch in Configure and on the Settings tab. It's saved but doesn't change anything yet. With a battery, spare solar can be stored for later instead of used straight away, which changes which window is cheapest. That logic comes in a later version. Leave it off for now.

## Limits

- Needs Home Assistant 2026.9 or later and the Nord Pool integration.
- Windows are searched within the prices that are known. Before tomorrow's prices are published, the search only reaches the end of today.
- Solar is a forecast. On a cloudy surprise, the window will already have been chosen.

## Licence

MIT
