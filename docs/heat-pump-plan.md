# Heat pump advice: planning mode

Status: built in 0.19.0 (`core/heatplan.py`, `advisor.py`).

## Why

The look-ahead advice sees only `store_hours` (default 4) ahead. It boosts
when now is cheaper than the next 4 hours and holds back when a cheaper
stretch is inside them. That misses anything further out: at 17:00 it can
boost at 40 c/kWh because the next 4 hours are 45 c, while 10 c is 5 hours
away. Day-ahead prices reach 11 to 35 hours ahead, so the advice can use all
of them.

## What stays the same

- The sensor `sensor.watt_window_heat_pump_advice`, its states
  `boost` / `normal` / `hold_back`, and the attributes it had
  (`since`, `reason`, `price_now`, `average_ahead`, …). An automation written
  for the look-ahead advice works unchanged.
- Spare solar right now still means `boost`.
- The minimum time in a mode (`heat_pump_min_minutes`).
- Watt Window still only advises. It never touches the heat pump.

The planner is a choice on the Settings tab: **Look ahead** (the default) or
**Plan the day**. Planning needs an outdoor temperature forecast; without one
it falls back to look ahead and says so in `reason`.

## The model

Everything is in kWh of heat pump electricity, so no COP is needed: the user
measures what they can measure (their heat pump's electricity against outdoor
temperature).

**Heat need.** For each quarter-hour, the electricity the heat pump would use
in normal running:

    need = kwh_per_degree_day × max(0, balance_temp − outdoor) / 96

`kwh_per_degree_day` and `balance_temp` are settings (defaults 1.5 and
17 °C). **Suggest from history** on the Settings tab fits them from the
recorder: daily heating kWh of a heat pump energy sensor against the daily
mean of an outdoor sensor, over the last 45 days (days above 15 °C or with
almost no heating are left out; it shows R² so you can judge the line).
`outdoor` comes from a weather entity's forecast (`weather.get_forecasts`,
hourly if offered), fetched every 30 minutes. Hours past the end of the
forecast reuse its last value.

**The house as a store.** `S` is how much heat the house holds compared with
normal running, in kWh: below zero after a hold back, above zero after a
boost. It is kept between −C and +B:

    C = store_hours × need at 0 °C outside      (what the house can coast on)
    B = bank_share × C                           (what a boost can bank)

So `store_hours` keeps its meaning ("how long the house can coast"), read at
0 °C. The store is a fixed amount of heat, so the time it lasts shrinks as it
gets colder: a house that coasts 4 h at 0 °C coasts about 6 h at 5 °C and
2 h at −15 °C.

A boost banks much less than a hold back can borrow. Underfloor heating in a
screed coasts for hours, but a room already at its setpoint closes its valve
and takes no extra heat, so only the rooms below setpoint charge. Measured on
one such house: a boost banked about 35–40 % of C, mostly within 1.5 hours,
and almost nothing more after 2.5–3 hours. Hence `bank_share` (default 50 %)
and `bank_hours`, when a boost has banked most of it (default 3 h).

**Each mode, per quarter-hour.** With `τ = 4 × store_hours` and
`τ_b = 4 × bank_hours / 2` quarter-hours:

| Mode | Heat delivered | Electricity bought | Change in S |
|---|---|---|---|
| hold back | 0 (the compressor stops) | 0 | − need |
| normal | need − S / τ, at least 0 | the same | − S / τ |
| boost | need + (B − S) / τ_b | heat × (1 + boost_penalty) | + (B − S) / τ_b |

- In **normal** the heat pump follows its own curve. After a hold back the
  rooms are cooler, valves stay open longer and it runs more: the house pays
  back its debt by itself over a few hours, and uses up banked heat the same
  way. That is the `S / τ` term: normal drifts S back to 0.
- **Boost** raises the flow temperature: S drifts towards +B. A higher flow
  temperature costs efficiency: `boost_penalty` (setting, default 5 %) more
  electricity for all the heat bought in a boost. Measured on one ground
  source heat pump: about 3 % per K of flow, so about 6 % for +2 K. A boost
  is only worth it when the price gap beats the penalty.
- A **hold back** must save at least `heat_pump_margin` per kWh it moves
  (default 2 c/kWh), as before. Forecasts and the model are rough; this keeps
  it from shifting for a few tenths of a cent.
- Prices are the effective prices Watt Window already uses (tariff, VAT,
  forecast solar at `heat_pump_power_w`). The plan doesn't use the power
  otherwise: boost and payback are paced by the house, and a modulating heat
  pump simply runs harder. (A fixed-speed heat pump that already runs
  non-stop on a cold day can't boost; the plan will then overrate boosts on
  those days.)

**The plan** is the cheapest sequence of modes from now to the end of the
known prices (at most 48 h), such that:

- S stays between −C and +B;
- a boost or hold back lasts at least `heat_pump_min_block_hours` (default
  2 h): long runs, few compressor starts;
- a boost lasts at most `heat_pump_max_boost_hours` (default 4 h);
- normal lasts at least `heat_pump_min_minutes`;
- each change of mode costs a small fixed amount (a compressor start), so
  near-ties don't flip the mode;
- the plan starts from the current mode and how long it has run, so it never
  asks for a change the minimum time would refuse;
- a debt left at the end of the known prices is valued at their average
  price (it will be paid back later). Heat banked at the end is worth
  nothing, so it never banks just before the prices run out.

It is solved exactly by dynamic programming over quarter-hours (store level on
a 41-point grid with 0 on it, interpolated), in about 0.1 s for 36 hours, in
the executor.

Banked heat leaks back out (normal drifts S to 0), so a boost lands at the end
of a cheap stretch, right before the dear hours it is banking for.

**When it runs:** on every price refresh (each quarter-hour) and every
forecast refresh, from the current S. The minute tick only reads the plan.

**Tracking S.** Watt Window adds up S from the advice it gave and the need at
each minute. A reload (saving settings) keeps it; after a restart S starts at
0. If the house automation ends a hold back early (rooms too cold), S reaches
−C and the plan pays the debt back: the model and the house agree again.

## Ground loop (optional)

A ground source heat pump's efficiency barely changes through the day, so
price decides. The one thing to watch is long runs, which pull the ground
loop's brine down. The longest boost applies to everyone; with a brine
temperature sensor set, a running boost also turns to normal while the brine
is below `heat_pump_brine_min` (default 0 °C), until it is 1 K above it.

## New attributes

- `planner`: `look_ahead` or `plan`.
- In plan mode:
  - `plan`: the coming blocks, `[{start, end, mode, price}]` (average
    effective price), up to the end of the known prices;
  - `store_kwh`: the current S; `store_limit_kwh`: C; `bank_limit_kwh`: B;
  - `need_kwh`: the heat pump electricity the plan expects until the end of
    the known prices; `outdoor_average`: the forecast mean over that time;
  - `plan_saving`: the plan's cost against normal all the way, in the price
    currency (model figure).

`plan`, `plan_made_at` and `store_kwh` change often and are not recorded in
history. The Overview's heat pump card lists the next blocks and the saving.

## New settings (Settings tab, heat pump section)

| Setting | Default |
|---|---|
| Planner: Look ahead / Plan the day | Look ahead |
| Temperature forecast from | the first weather entity |
| Heat pump electricity per degree-day (kWh) | 1.5 |
| Heating stops above (°C) | 17 |
| Heating energy + outdoor sensor (for Suggest from history) | none |
| A boost costs this much more electricity (%) | 5 |
| A boost banks at most (% of the coasting store) | 50 |
| …and has banked most of it after (hours) | 3 |
| Shortest boost or hold back (hours) | 2 |
| Longest boost (hours) | 4 |
| Brine temperature sensor (optional), no boost below (°C) | none, 0 |

`store_hours`, `heat_pump_margin`, `heat_pump_power_w` and
`heat_pump_min_minutes` are reused.

## Worked example

Made-up prices, house as in the defaults with `store_hours` 4 and a longest
boost of 4 h:

- Dear evening (40–45 c for 5 h, then 10 c), 5 °C outside: hold back through
  the 5 dear hours, boost in the cheap ones. At −15 °C the house can't coast
  5 hours, so it boosts 2 h at 40 c and coasts through the 45 c hours
  instead. That is the right call for a cold house, and it is what a fixed
  4-hour look-ahead can't tell apart.
- Flat prices, or a 1 c dip: normal all the way.
- A typical winter day-ahead shape (afternoon dip, evening peak, night low,
  morning peak, over 31 h): cost 0.52 / 0.63 / 0.71 of running normally at
  +5 / −5 / −15 °C, longest hold back 7 / 4 / 3 h, 3 compressor restarts.
  With a small bank (35 %, 2.5 h, longest boost 2 h): 0.55 / 0.65 / 0.75.
  These are model figures: the house never follows the model exactly.

## Limits

- The heat-need line is only as good as its two numbers. Suggest from
  history fits them, but only from the temperatures the last weeks had; a
  first cold snap is extrapolation.
- Boost banking depends on how many rooms are below their setpoint. Measure
  it (`bank_share`) rather than trusting the default.
- Hot water is not planned here. The advice is one signal; an automation can
  still use it for the tank.
- The house automation stays responsible for comfort (ending a hold back when
  rooms get cold). The plan does not see room temperatures.
