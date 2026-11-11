# Heat pump advice: planning mode (design)

Status: design, not built. Target version 0.19.0.

## Why

Today the heat pump advice looks only `store_hours` (default 4) ahead. It
boosts when now is cheaper than the next 4 hours and holds back when a
cheaper stretch is inside them. That misses anything further out: at 17:00 it
can boost at 40 c/kWh because the next 4 hours are 45 c, while 10 c is 5
hours away. Day-ahead prices reach 11 to 35 hours ahead, so the advice can
use all of them.

## What stays the same

- The sensor `sensor.watt_window_heat_pump_advice`, its states
  `boost` / `normal` / `hold_back`, and the attributes it has today
  (`since`, `reason`, `wanted`, `price_now`, …). An automation written for
  the look-ahead advice works unchanged.
- Spare solar right now still means `boost`.
- The minimum time in a mode (`heat_pump_min_minutes`).
- Watt Window still only advises. It never touches the heat pump.

The planner is a choice on the Settings tab: **Look ahead** (today's
behaviour, the default) or **Plan the day**. Planning needs an outdoor
temperature forecast; without one it falls back to look ahead and says so in
`reason`.

## The model

Everything is in kWh of heat pump electricity, so no COP is needed: the user
measures what they can measure (their heat pump's electricity against outdoor
temperature).

**Heat need.** For each quarter-hour, the electricity the heat pump would use
in normal running:

    need = kwh_per_degree_day × max(0, balance_temp − outdoor) / 96

`kwh_per_degree_day` and `balance_temp` are settings (defaults 1.5 and
17 °C; a house's own figures come from a few weeks of daily heat pump kWh
against the daily mean outdoor temperature). `outdoor` comes from a weather
entity's hourly forecast (`weather.get_forecasts`), refreshed every hour.
Hours past the end of the forecast reuse its last value.

**The house as a store.** `S` is how much heat the house holds compared with
normal running, in kWh: below zero after a hold back, above zero after a
boost. It is kept between −C and +C, where

    C = store_hours × need at 0 °C outside

So `store_hours` keeps its meaning ("how long the house can coast"), read at
0 °C. The store is a fixed amount of heat, so the time it lasts shrinks as it
gets colder: a house that coasts 4 h at 0 °C coasts about 6 h at 5 °C and
2 h at −15 °C.

**Each mode, per quarter-hour:**

With `τ = 4 × store_hours` quarter-hours:

| Mode | Heat delivered | Electricity bought | Change in S |
|---|---|---|---|
| hold back | 0 (the compressor stops) | 0 | − need |
| normal | need − S / τ, at least 0 | the same | − S / τ |
| boost | need + (C − S) / τ | heat × (1 + boost_penalty) | + (C − S) / τ |

- In **normal** the heat pump follows its own curve. After a hold back the
  rooms are cooler, valves stay open longer and it runs more: the house pays
  back its debt by itself over a few hours, and uses up banked heat the same
  way. That is the `S / τ` term: normal drifts S back to 0.
- **Boost** raises the flow temperature, which moves the house's balance up:
  S drifts towards +C at the same pace. A higher flow temperature costs
  efficiency: `boost_penalty` (setting, default 5 %) more electricity for all
  the heat bought in a boost. So a boost is only worth it when the price gap
  beats the penalty.
- A **hold back** must save at least `heat_pump_margin` per kWh it moves
  (default 2 c/kWh), as today. Forecasts and the model are rough; this keeps
  it from shifting for a few tenths of a cent.
- Prices are the effective prices Watt Window already uses (tariff, VAT,
  forecast solar at `heat_pump_power_w`). The plan doesn't use the power
  otherwise: boost and payback are paced by the house, and a modulating heat
  pump simply runs harder. (A fixed-speed heat pump that already runs
  non-stop on a cold day can't boost; the plan will then overrate boosts on
  those days.)

**The plan** is the cheapest sequence of modes from now to the end of the
known prices, such that:

- S stays within ±C;
- a boost or hold back lasts at least `heat_pump_min_block_hours` (setting,
  default 2 h): long runs, few compressor starts;
- normal lasts at least `heat_pump_min_minutes`;
- each change of mode costs a small fixed amount (a compressor start), so
  near-ties don't flip the mode;
- the plan starts from the current mode and how long it has run, so it never
  asks for a change the minimum time would refuse;
- a debt left at the end of the known prices is valued at their average
  price (it will be paid back later). Heat banked at the end is worth
  nothing, so it never boosts just before the prices run out.

It is solved exactly by dynamic programming over quarter-hours (store level on
a grid, interpolated), well under a second for 36 hours.

**When it runs:** on every price refresh, every forecast refresh, and every
quarter-hour, from the current S. The minute tick only reads the plan.

**Tracking S.** Watt Window adds up S from the advice it gave and the need at
each minute (clamped to ±C). After a restart S starts at 0. If the house
automation ends a hold back early (rooms too cold), S simply reaches −C and
the plan pays the debt back: the model and the house agree again.

## Ground loop (optional)

A ground source heat pump's efficiency barely changes through the day, so
price decides. The one thing to watch is very long runs, which pull the ground
loop's brine down. With a brine temperature sensor set:

- no boost block is planned longer than `heat_pump_max_boost_hours`
  (default 6);
- a running boost turns to normal while the brine is below
  `heat_pump_brine_min_c` (default 0 °C), until it is 1 K above it.

Without a sensor neither applies: a boost stops paying once the store is
nearly full, which already keeps most boosts to a few hours.

## New attributes

- `planner`: `look_ahead` or `plan`.
- `plan`: the coming blocks, `[{start, end, mode, price}]` (average effective
  price), up to the end of the known prices. Not recorded in history.
- `store_kwh`: the current S. `store_limit_kwh`: C.
- `need_kwh`: the heat pump electricity the plan expects until the end of the
  known prices; `outdoor_average`: the forecast mean over that time.
- `plan_saving`: estimated cost of the plan against normal all the way, in
  the price currency.

The Overview's heat pump card shows the plan's blocks on the price chart.

## New settings (Settings tab, heat pump section)

| Setting | Default |
|---|---|
| Planner: Look ahead / Plan the day | Look ahead |
| Weather forecast entity | the first weather entity |
| Heat pump electricity per degree-day (kWh) | 1.5 |
| Heating stops above (°C) | 17 |
| Boost costs this much more electricity (%) | 5 |
| Shortest boost or hold back (hours) | 2 |
| Brine temperature sensor (optional) | none |
| Longest boost (hours), lowest brine (°C) | 6, 0 |

`store_hours`, `heat_pump_margin`, `heat_pump_power_w` and
`heat_pump_min_minutes` are reused.

## Prototype check

A throwaway version of the planner on made-up prices (36 h of quarters in
0.09 s), house as in the defaults with `store_hours` 4:

- Dear evening (40–45 c for 5 h, then 10 c), 5 °C outside: hold back through
  the 5 dear hours, boost in the cheap ones. At −15 °C the house can't coast
  5 hours, so it boosts briefly at 40 c and coasts through the 45 c hours
  instead. That is the right call for a cold house, and it is what a fixed
  4-hour look-ahead can't tell apart.
- Flat prices, or a 0.5 c dip: normal all the way.
- A typical winter day-ahead shape (afternoon dip, evening peak, night low,
  morning peak): cost 0.53 / 0.62 / 0.71 of running normally at +5 / −5 /
  −15 °C, with hold backs of 8 / 4 / 3 hours and about 4 compressor starts a
  day. (Model figures only: the real saving is smaller, since the house
  doesn't bank heat as neatly as the model.)

## Limits

- The heat-need line is only as good as the user's two numbers. A later
  version could suggest them from the heat pump's energy sensor and outdoor
  history.
- Boost banking assumes the house takes the extra heat. A room that is
  already at its setpoint closes its valve and takes none; measure it before
  relying on boosts.
- Hot water is not planned here. The advice is one signal; an automation can
  still use it for the tank.
- The house automation stays responsible for comfort (ending a hold back when
  rooms get cold). The plan does not see room temperatures.

## Tests (core, no Home Assistant)

- The 17:00 case: dear now, 10 c five hours away → hold back, not boost.
- A flat day → normal throughout.
- A gap smaller than the boost penalty → no boost.
- Blocks are never shorter than the minimum; changes respect the current
  mode's age.
- Colder forecast → shorter hold backs (S never below −C).
- Debt at the end of the prices is paid back; no boost in the last hours.
- Brine: no boost block longer than the cap; cold brine ends a boost.
- No forecast → look ahead, with the reason.
- Runtime for 36 hours of quarters stays under a second.
