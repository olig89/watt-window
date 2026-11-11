"""Heat pump planning mode: plan boost / normal / hold back from every price known.

The look-ahead advice (``heatpump.advise``) only sees ``store_hours`` ahead. This
plans the whole stretch of known prices instead, with a simple model of the
house as a heat store. Everything is in kWh of heat pump electricity, so no
COP is needed. See docs/heat-pump-plan.md for the reasoning.

* **Need**: what normal running would use in a quarter,
  ``kwh_per_degree_day * max(0, balance_temp - outdoor) / 96``.
* **Store** ``S``: heat the house holds compared with normal running, kept
  between ``-C`` (coasting: ``C = store_hours * need per hour at 0 °C``) and
  ``+B`` (banking: ``B = bank_share * C``). A boost banks much less than a
  hold back can borrow: rooms at their setpoint close their valves.
* Per quarter, with ``tau = 4 * store_hours`` and ``tau_b = 4 * bank_hours / 2``
  quarters (``bank_hours`` is when a boost has stored most of what it can):

  - hold back: buys nothing, ``S -= need``;
  - normal: buys ``need - S / tau`` (at least 0), ``S`` drifts back to 0;
  - boost: delivers ``need + (B - S) / tau_b`` and buys that times
    ``1 + boost_penalty``; ``S`` drifts towards ``+B``.

The plan is the cheapest sequence of modes (dynamic programming over quarters,
store level on a grid with linear interpolation), with boost and hold back
blocks of at least ``min_block_quarters``, normal of at least
``min_normal_quarters``, a small cost per change, and a hold back that must
save ``margin`` per kWh it moves.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .heatpump import BOOST, HOLD_BACK, NORMAL
from .windows import QUARTER, Quarter

MODES = (HOLD_BACK, NORMAL, BOOST)
INF = float("inf")
# A compressor start, in the price currency. Small: it only breaks near-ties.
SWITCH_COST = 0.002
GRID = 41  # store levels between -C and +B
MAX_QUARTERS = 48 * 4  # never plan further than two days


@dataclass(frozen=True)
class PlanSettings:
    kwh_per_degree_day: float = 1.5
    balance_temp: float = 17.0
    store_hours: float = 4.0
    boost_penalty: float = 0.05  # 5 % more electricity for heat bought in a boost
    margin: float = 0.02  # a hold back must save this per kWh (price currency)
    min_block_quarters: int = 8  # shortest boost or hold back
    min_normal_quarters: int = 4  # shortest normal
    max_boost_quarters: int | None = None  # longest boost, None = no cap
    bank_share: float = 0.5  # a boost banks at most this share of what a hold back can borrow
    bank_hours: float = 3.0  # by then a boost has stored most (~86 %) of what it can

    def need_per_quarter(self, outdoor: float) -> float:
        return self.kwh_per_degree_day * max(0.0, self.balance_temp - outdoor) / 96

    @property
    def store_limit(self) -> float:
        """C: how much heat the house can lend (coast on), in kWh of electricity."""
        return self.store_hours * 4 * self.need_per_quarter(0.0)

    @property
    def bank_limit(self) -> float:
        """B: how much extra heat a boost can bank, in kWh of electricity."""
        return max(0.0, self.bank_share) * self.store_limit

    @property
    def tau(self) -> float:
        return max(1.0, 4 * self.store_hours)

    @property
    def bank_tau(self) -> float:
        return max(1.0, 4 * self.bank_hours / 2)


@dataclass(frozen=True)
class Block:
    start: datetime
    end: datetime
    mode: str
    price: float  # average effective price over the block


@dataclass(frozen=True)
class Plan:
    blocks: list[Block]
    made_at: datetime
    store_now: float
    store_limit: float
    bank_limit: float
    need_kwh: float  # electricity normal running would use until the end
    outdoor_average: float
    cost: float  # electricity cost of the plan
    normal_cost: float  # the same stretch in normal all the way
    _starts: list[datetime] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_starts", [b.start for b in self.blocks])

    @property
    def end(self) -> datetime | None:
        return self.blocks[-1].end if self.blocks else None

    def block_at(self, t: datetime) -> Block | None:
        i = bisect_right(self._starts, t) - 1
        if i >= 0 and t < self.blocks[i].end:
            return self.blocks[i]
        return None

    @property
    def saving(self) -> float:
        return self.normal_cost - self.cost


class Forecast:
    """Outdoor temperature at any time from forecast points (start, °C).

    Before the first point uses the first; after the last, the last."""

    def __init__(self, points: Sequence[tuple[datetime, float]]) -> None:
        pts = sorted(points)
        self._t = [t for t, _ in pts]
        self._v = [v for _, v in pts]

    def __bool__(self) -> bool:
        return bool(self._t)

    def at(self, t: datetime) -> float:
        i = bisect_right(self._t, t) - 1
        return self._v[max(i, 0)]


def step(settings: PlanSettings, mode: str, store: float, need: float, fraction: float = 1.0) -> tuple[float, float]:
    """One quarter (or ``fraction`` of one) in ``mode``: (electricity bought, new store)."""
    c, bank = settings.store_limit, settings.bank_limit
    if mode == HOLD_BACK:
        bought, new = 0.0, store - need * fraction
    elif mode == BOOST:
        heat = (need + max(0.0, bank - store) / settings.bank_tau) * fraction
        bought, new = heat * (1 + settings.boost_penalty), store + heat - need * fraction
    else:
        heat = max(0.0, need - store / settings.tau) * fraction
        bought, new = heat, store + heat - need * fraction
    return bought, max(-c, min(max(bank, store), new))


def _caps(s: PlanSettings) -> dict[str, int]:
    """Highest age kept per mode: enough to know the minimum (and boost maximum) is met."""
    boost_cap = max(s.min_block_quarters, s.max_boost_quarters or 0)
    return {HOLD_BACK: max(1, s.min_block_quarters), NORMAL: max(1, s.min_normal_quarters), BOOST: max(1, boost_cap)}


def _min_len(s: PlanSettings) -> dict[str, int]:
    return {HOLD_BACK: s.min_block_quarters, NORMAL: s.min_normal_quarters, BOOST: s.min_block_quarters}


def make_plan(
    quarters: Sequence[Quarter],
    now: datetime,
    forecast: Forecast,
    settings: PlanSettings,
    *,
    store_now: float = 0.0,
    mode_now: str | None = None,
    mode_quarters: int = 0,
    power_w: float = 2000,
    base_load_w: float = 500,
) -> Plan | None:
    """The cheapest plan from the quarter holding ``now`` to the end of the known prices.

    ``mode_now`` / ``mode_quarters``: the mode the heat pump is in and how many
    quarters it has been in it, so the plan never asks for a change the
    minimum would refuse. None when nothing is known (planned as normal for a
    long time). Returns None when there is no price for now.
    """
    slots = [q for q in quarters if q.end > now][:MAX_QUARTERS]
    if not slots or slots[0].start > now:
        return None
    n = len(slots)
    prices = [q.effective_price(power_w, base_load_w) for q in slots]
    temps = [forecast.at(q.start) for q in slots]
    need = [settings.need_per_quarter(t) for t in temps]
    c, bank = settings.store_limit, settings.bank_limit
    caps, min_len = _caps(settings), _min_len(settings)
    max_boost = settings.max_boost_quarters
    avg_price = sum(prices) / n

    if c <= 0:  # warm enough that the house needs nothing: nothing to shift
        blocks = [Block(slots[0].start, slots[-1].end, NORMAL, avg_price)]
        return Plan(blocks, now, 0.0, 0.0, 0.0, 0.0, sum(temps) / n, 0.0, 0.0)

    # Zero (normal running) is always a grid level: interpolating across it would
    # blur the cost of a debt and make "do nothing" look dearer than it is.
    n_bank = round((GRID - 1) * bank / (c + bank)) if bank > 0 else 0
    n_coast = GRID - 1 - n_bank
    levels = [-c + c * i / n_coast for i in range(n_coast)] + [0.0] + [bank * j / n_bank for j in range(1, n_bank + 1)]
    size = len(levels)
    states = [(m, a) for m in MODES for a in range(1, caps[m] + 1)]

    def interp(values: list[float], s: float) -> float:
        if s < -c - 1e-9 or s > levels[-1] + 1e-9:
            return INF
        j = min(max(bisect_right(levels, s) - 1, 0), size - 2)
        w = min(max((s - levels[j]) / (levels[j + 1] - levels[j]), 0.0), 1.0)
        a, b = values[j], values[j + 1]
        if a == INF or b == INF:
            return a if w < 1e-6 else b if w > 1 - 1e-6 else INF
        return a + w * (b - a)

    def options(m: str, a: int):
        """(next mode, its age after this quarter, switch cost) allowed from state (m, a)."""
        for m2 in MODES:
            if m2 == m:
                if m == BOOST and max_boost and a >= max_boost:
                    continue
                yield m2, min(a + 1, caps[m]), 0.0
            elif a >= min_len[m]:
                yield m2, 1, SWITCH_COST

    def quarter_cost(i: int, mode: str, s: float) -> tuple[float, float]:
        bought, s2 = step(settings, mode, s, need[i])
        if mode == HOLD_BACK:
            s2 = s - need[i]  # unclamped: running below -C is not allowed
        extra = settings.margin * need[i] if mode == HOLD_BACK else 0.0
        return bought * prices[i] + extra, s2

    # Backward pass: values[i][(m, a)][level] = cheapest cost from slot i on.
    end_values = [max(0.0, -s) * avg_price for s in levels]
    nxt = {st: end_values for st in states}
    values: list[dict] = [None] * (n + 1)  # type: ignore[list-item]
    values[n] = nxt
    for i in range(n - 1, -1, -1):
        trans = {m: [quarter_cost(i, m, s) for s in levels] for m in MODES}
        cur = {}
        for m, a in states:
            opts = list(options(m, a))
            row = []
            for li in range(size):
                best = INF
                for m2, a2, sc in opts:
                    cost, s2 = trans[m2][li]
                    v = interp(nxt[(m2, a2)], s2)
                    if v != INF and cost + sc + v < best:
                        best = cost + sc + v
                row.append(best)
            cur[(m, a)] = row
        values[i] = nxt = cur

    # Forward pass from the real store level, choosing with one-step look-ahead.
    mode = mode_now if mode_now in MODES else NORMAL
    age = min(max(mode_quarters, 1), caps[mode]) if mode_now in MODES else caps[mode]
    s = max(-c, min(bank, store_now))
    chosen: list[str] = []
    cost = 0.0
    for i in range(n):
        best, pick = INF, None
        for m2, a2, sc in options(mode, age):
            qc, s2 = quarter_cost(i, m2, s)
            v = interp(values[i + 1][(m2, a2)], s2)
            if v != INF and qc + sc + v < best:
                best, pick = qc + sc + v, (m2, a2)
        if pick is None:  # nothing feasible (store off the grid): normal is always safe
            pick = (NORMAL, min(age + 1, caps[NORMAL]) if mode == NORMAL else 1)
        mode, age = pick
        bought, s = step(settings, mode, s, need[i])
        cost += bought * prices[i]
        chosen.append(mode)

    blocks: list[Block] = []
    i = 0
    while i < n:
        j = i
        while j + 1 < n and chosen[j + 1] == chosen[i]:
            j += 1
        span = prices[i : j + 1]
        blocks.append(Block(slots[i].start, slots[j].end, chosen[i], sum(span) / len(span)))
        i = j + 1

    normal_cost, sn = 0.0, max(-c, min(bank, store_now))
    for i in range(n):
        bought, sn = step(settings, NORMAL, sn, need[i])
        normal_cost += bought * prices[i]

    return Plan(
        blocks=blocks,
        made_at=now,
        store_now=max(-c, min(bank, store_now)),
        store_limit=c,
        bank_limit=bank,
        need_kwh=sum(need),
        outdoor_average=sum(temps) / n,
        cost=cost,
        normal_cost=normal_cost,
    )


def quarters_in_mode(since: datetime | None, now: datetime) -> int:
    """Whole quarters the current mode has run (at least 1)."""
    if since is None:
        return 0
    return max(1, int((now - since) / QUARTER))


class BrineGuard:
    """No boost while the ground loop's brine is below ``min_c``; back on at ``min_c + 1``."""

    def __init__(self, min_c: float, hysteresis: float = 1.0) -> None:
        self.min_c = min_c
        self.hysteresis = hysteresis
        self.cold = False

    def update(self, brine: float | None) -> bool:
        """True while boosts are blocked. An unknown reading keeps the last state."""
        if brine is None:
            return self.cold
        if brine < self.min_c:
            self.cold = True
        elif brine >= self.min_c + self.hysteresis:
            self.cold = False
        return self.cold


@dataclass(frozen=True)
class HeatNeedFit:
    kwh_per_degree_day: float
    balance_temp: float
    r2: float
    days: int


def fit_heat_need(days: Sequence[tuple[float, float]], max_temp: float = 15.0, min_days: int = 7) -> HeatNeedFit | None:
    """Fit ``kwh = k * (balance - T)`` to daily (mean outdoor °C, heating kWh).

    Days warmer than ``max_temp`` or with almost no heating are left out (summer,
    heating off). Returns None without enough days, too little spread in
    temperature, or a fit that doesn't rise as it gets colder.
    """
    pts = [(t, e) for t, e in days if t is not None and e is not None and t <= max_temp and e > 0.5]
    if len(pts) < min_days:
        return None
    ts, es = [t for t, _ in pts], [e for _, e in pts]
    mt, me = sum(ts) / len(ts), sum(es) / len(es)
    sxx = sum((t - mt) ** 2 for t in ts)
    if sxx / len(ts) < 1.0:  # less than ~1 K spread: the slope is guesswork
        return None
    slope = sum((t - mt) * (e - me) for t, e in pts) / sxx
    if slope >= 0:
        return None
    intercept = me - slope * mt
    ss_res = sum((e - (intercept + slope * t)) ** 2 for t, e in pts)
    ss_tot = sum((e - me) ** 2 for e in es)
    return HeatNeedFit(
        kwh_per_degree_day=-slope,
        balance_temp=intercept / -slope,
        r2=1 - ss_res / ss_tot if ss_tot else 0.0,
        days=len(pts),
    )


