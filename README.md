# Watt Window

Finds the cheapest hours to run things in Home Assistant, using Nord Pool spot prices, your own tariff, and an optional solar forecast. Set-up asks for as little as possible; you add detail later, when you have it.

Each block of cheap hours it finds is a **Watt Window**. You choose the Watt Window lengths (1, 2 and 4 hours by default, plus any lengths you add). For each one, Watt Window tells you when the cheapest block starts and gives you a sensor that is **on** while that block is running. Point your automations at those sensors to start the dishwasher, washing machine, heat pump boost, car or battery charging, and so on.

Watt Window never switches anything itself. It only provides sensors, so your automations stay in charge.

## What it takes into account

- **Spot prices** from the built-in [Nord Pool](https://www.home-assistant.io/integrations/nordpool/) integration, at 15-minute resolution. Tomorrow's prices are fetched once they are published, usually early afternoon.
- **Your tariff:** a network rate that can be flat, day/night, or day/night with winter peaks (Estonia's Võrk 5), plus supplier margin, other per-kWh charges and VAT. Presets for Elektrilevi Võrk 1, 2, 4 and 5 are included. Anyone else can enter their own rates.
- **Public holidays** for day/night tariffs that charge the night rate on holidays, from your country code.
- **Solar (optional)**, two ways:
  - **Estimate it for me** (free, no account): Watt Window turns [Open-Meteo](https://open-meteo.com/)'s sunlight forecast into expected output. It needs one number to start, your total panel size in kWp, and assumes a south-facing roof at 35°. On the Settings tab you can add each roof plane with its own size, direction and tilt whenever you know them. Weather data by Open-Meteo.com, CC BY 4.0.
  - **Forecast.Solar**, if you already use Home Assistant's [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/) integration.

  Either way, your panels first cover your typical house load. Any spare output then covers the load you want to run. What that share costs depends on one setting, **My system sends spare power to the grid**: if it does, using spare solar costs the export price you'd otherwise have earned; if your inverter holds the panels back instead ("zero export"), spare solar would simply be lost, so using it is free. So a sunny midday beats a cheap night only when it really is cheaper.

When prices are flat for a while (common at night, or on a day/night tariff at weekends), several Watt Windows can start at the same time: ties go to the earliest start. The sidebar page then says how late you could start for the same price, and the `latest_same_price_start` attribute lets an automation use that.

Each length also gets a cheapest **daytime** and cheapest **overnight** window, for jobs that must happen in one or the other. The day runs 08:00-20:00 by default (change it on the Settings tab); a window is searched in the current day or night if enough of it is left, otherwise in the next one.

**How far ahead it can see:** only as far as prices are published. Nord Pool's day-ahead auction closes at 12:00 CET and prices appear at about 12:45 CET for the next day (midnight to midnight CET). So from early afternoon you can see to the end of tomorrow; in the morning, only to the end of today. The page says when the next prices are due, and the daytime/overnight sensors have a `settled` attribute that stays `false` while part of their period isn't priced yet.

Watt Window reads prices through the Nord Pool integration's own price service, not its entities, so it doesn't matter what your Nord Pool sensors are called. If you have more than one Nord Pool setup, you pick which one (and which area) during setup.

Once a window has started, it stays put. A price update mid-window will not move it.

## Install

1. In HACS, open the menu (⋮) → **Custom repositories**. Add `https://github.com/olig89/watt-window` with type **Integration**.
2. Search HACS for **Watt Window** (the main list only shows what you've already downloaded), open it and download it. Restart Home Assistant.
3. Make sure **Nord Pool** is set up (Settings → Devices & services → Add integration → Nord Pool). Set up **Forecast.Solar** too if you have panels.
4. Add the **Watt Window** integration: price area and tariff preset, tariff details, then two questions you can skip: solar panels ("estimate it for me" needs only your panel size) and home battery.

**Nord Pool is required** (it's where the prices come from). Solar and battery are optional and can be turned on or off later under **Configure**.

**Watt Window** then appears in the sidebar (admins only).

## Updates

HACS shows an update when a new release is published. Download it and restart Home Assistant.

## The sidebar page

- **Overview:** the price now; a **Solar** switch next to the Watt Windows heading (turn it off and the Watt Windows, their sensors and the chart all use grid prices only, while your solar setup is kept; a Battery switch sits beside it for later); a card per Watt Window length with its start time, its daytime (sun) and overnight (moon) versions, and details you can fold away; and a chart of every published price with a lane per Watt Window underneath and its explanation behind the same fold-out button.
- **Settings:** window lengths, your day hours (a slider with a handle for when the day starts and one for when it ends), your solar setup, home battery, appliance watts, and your tariff rates. Changes apply straight away.

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

## Spare solar now

Watt Windows plan ahead from forecasts. **Spare solar now** watches what's actually happening, and tells you when there's solar to spare right now:

- `sensor.watt_window_spare_solar_now_estimate`: spare solar in watts.
- `binary_sensor.watt_window_spare_solar_for_your_appliance`: on when the spare covers your appliance's watts (Settings, Cost estimates).

How it knows depends on your system:

- **If spare power goes to the grid**, the spare is simply what's going out: measured.
- **If your inverter holds the panels back instead** ("zero export"), the spare never shows up in any reading, because the panels only ever make what the house uses. What does show is the grid sitting at about zero while the panels produce: they're being held back. How much more they could make is an **estimate**: the solar forecast for right now minus what they're actually making. The sensor's `basis` and `is_estimate` attributes always say which it is, and the page marks it "estimate".

Readings are averaged over 5 minutes; the on/off sensor waits 3 minutes before switching on and 5 before switching off, so a passing cloud doesn't flick it. All adjustable on the Settings tab.

It uses the **live power sensors from your Energy dashboard** (Settings, Dashboards, Energy: the grid and solar "power" sensors), so usually there's nothing to set up. You can pick other sensors on the Settings tab instead; if your grid meter shows importing as a negative number, tick the box that says so.

## Home battery

Setup asks whether you have a home battery (you can skip it), and there's a matching switch in Configure and on the Settings tab. It's saved but doesn't change anything yet. With a battery, spare solar can be stored for later instead of used straight away, which changes which window is cheapest. That logic comes in a later version. Leave it off for now.

## Limits

- Needs Home Assistant 2026.9 or later and the Nord Pool integration.
- Windows are searched within the prices that are known. Before tomorrow's prices are published, the search only reaches the end of today.
- Solar is a forecast. On a cloudy surprise, the window will already have been chosen.

## Licence

MIT
