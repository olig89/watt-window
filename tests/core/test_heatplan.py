import time
from datetime import datetime, timedelta, timezone

from custom_components.watt_window.core.heatplan import (
    BrineGuard,
    Forecast,
    PlanSettings,
    fit_heat_need,
    make_plan,
    quarters_in_mode,
    step,
)
from custom_components.watt_window.core.heatpump import BOOST, HOLD_BACK, NORMAL
from custom_components.watt_window.core.windows import Quarter

T0 = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
Q = timedelta(minutes=15)
H = timedelta(hours=1)
S = PlanSettings()  # 1.5 kWh/degree-day, 17 °C, 4 h store, 5 %, 2 c, 2 h blocks


def day(prices_by_hour):
    """Quarters from hourly import prices (c/kWh), starting at T0."""
    qs = []
    for h, c in enumerate(prices_by_hour):
        for k in range(4):
            t = T0 + h * H + k * Q
            qs.append(Quarter(start=t, end=t + Q, spot=0, import_price=c / 100, export_price=0,
                              solar_w=0, tariff_key="day"))
    return qs


def flat(temp):
    return Forecast([(T0, temp)])


def modes_by_hour(plan, hours):
    return [plan.block_at(T0 + h * H + Q).mode for h in range(hours)]


EVENING = [40, 45, 45, 42, 40, 10, 9, 9, 10, 12, 15, 20, 25, 25, 22, 20, 18, 18, 20, 25, 30, 35, 30, 25]
# Afternoon dip, evening peak, night low, morning peak, midday dip, evening.
WINTER = [14, 13, 15, 22, 35, 42, 38, 25, 18, 12, 9, 7, 6, 6, 7, 10, 18, 28, 32, 24, 16, 12, 11, 13, 20, 30, 36, 30, 20, 14, 10]


def test_dear_evening_with_cheap_hours_five_hours_away_holds_back_not_boosts():
    # The look-ahead advice boosted here at 40 c: the next 4 h were dearer, 10 c was 5 h away.
    plan = make_plan(day(EVENING), T0, flat(5), S)
    assert plan.block_at(T0).mode == HOLD_BACK
    assert modes_by_hour(plan, 5) == [HOLD_BACK] * 5
    assert BOOST in modes_by_hour(plan, 9)[5:]  # store heat in the cheap hours
    assert plan.cost < plan.normal_cost


def test_flat_prices_mean_normal_all_the_way():
    plan = make_plan(day([20] * 30), T0, flat(5), S)
    assert [b.mode for b in plan.blocks] == [NORMAL]


def test_a_gap_smaller_than_the_margin_is_not_worth_a_hold_back():
    plan = make_plan(day([20, 20, 20, 19, 19, 19, 20, 20, 20, 20, 20, 20]), T0, flat(5), S)
    assert [b.mode for b in plan.blocks] == [NORMAL]


def test_a_gap_smaller_than_the_boost_penalty_is_not_worth_a_boost():
    # 4 % cheaper for 3 h, then back: a boost would cost 5 % on all its heat.
    plan = make_plan(day([24, 24, 24, 25, 25, 25, 25, 25, 25, 25, 25, 25]), T0, flat(5), S)
    assert BOOST not in [b.mode for b in plan.blocks]


def test_boost_when_cheap_now_and_dear_later():
    plan = make_plan(day([10, 10, 10, 10, 30, 30, 30, 30, 20, 20, 20, 20]), T0, flat(5), S)
    assert plan.block_at(T0).mode == BOOST
    assert plan.block_at(T0 + 5 * H).mode == HOLD_BACK


def test_blocks_are_never_shorter_than_the_minimum():
    for temp in (5, -5, -15):
        plan = make_plan(day(WINTER), T0, flat(temp), S)
        for b in plan.blocks[:-1]:  # the last block is cut by the end of the prices
            minimum = S.min_normal_quarters if b.mode == NORMAL else S.min_block_quarters
            assert b.end - b.start >= minimum * Q, (temp, b)


def test_current_mode_is_kept_until_its_minimum_is_met():
    # Boost started 15 min ago at a cheap price; prices then jump. The 2 h minimum holds.
    plan = make_plan(day([10, 40, 40, 40, 40, 10, 10]), T0, flat(5), S, mode_now=BOOST, mode_quarters=1)
    assert plan.block_at(T0).mode == BOOST
    assert plan.blocks[0].end - T0 >= 7 * Q


def test_colder_means_shorter_hold_backs():
    longest = {}
    for temp in (5, -15):
        plan = make_plan(day(WINTER), T0, flat(temp), S)
        longest[temp] = max((b.end - b.start for b in plan.blocks if b.mode == HOLD_BACK), default=timedelta(0))
    assert longest[-15] < longest[5]
    assert longest[-15] <= timedelta(hours=4)


def test_store_never_runs_below_its_limit():
    s, temp = PlanSettings(), -10
    plan = make_plan(day(WINTER), T0, flat(temp), s)
    store = 0.0
    for b in plan.blocks:
        t = b.start
        while t < b.end:
            _, store = step(s, b.mode, store, s.need_per_quarter(temp))
            # step clamps; check the unclamped hold back too
            assert store >= -s.store_limit - 1e-9
            t += Q
    # a hold back from the limit itself is never planned
    deep = make_plan(day([40] * 4 + [10] * 8), T0, flat(temp), s, store_now=-s.store_limit)
    assert deep.block_at(T0).mode != HOLD_BACK


def store_at_end(plan, s, temp):
    store = plan.store_now
    for b in plan.blocks:
        for _ in range(int((b.end - b.start) / Q)):
            _, store = step(s, b.mode, store, s.need_per_quarter(temp))
    return store


def test_no_heat_left_banked_when_the_prices_run_out():
    # Holding back at 20 c and paying it back at 12 c is fine; banking extra at the end is not
    # (nothing after the known prices says it will be worth it).
    plan = make_plan(day([20] * 10 + [12, 12, 12]), T0, flat(5), S)
    assert store_at_end(plan, S, 5) <= 0.05


def test_debt_at_the_end_is_paid_for():
    # Dear last hours would invite a free hold back if a debt cost nothing at the end.
    plan = make_plan(day([20] * 8 + [21, 21, 21, 21]), T0, flat(5), S)
    assert [b.mode for b in plan.blocks] == [NORMAL]


def test_a_capped_boost_lands_at_the_end_of_the_cheap_stretch():
    # Banked heat leaks back out, so with a short boost the best time is just before the dear hours.
    small = PlanSettings(bank_share=0.35, bank_hours=2.5, max_boost_quarters=8)
    plan = make_plan(day([5] * 8 + [40] * 6 + [20] * 4), T0, flat(5), small)
    boosts = [b for b in plan.blocks if b.mode == BOOST]
    assert boosts[-1].end == T0 + 8 * H
    assert all(b.end - b.start <= 2 * H for b in boosts)
    assert plan.block_at(T0 + 8 * H).mode == HOLD_BACK
    assert small.bank_limit == 0.35 * small.store_limit


def test_a_small_bank_shortens_the_hold_back_after_a_boost():
    def first_hold(settings):
        plan = make_plan(day([5] * 4 + [40] * 10 + [20] * 4), T0, flat(5), settings)
        return next(b.end - b.start for b in plan.blocks if b.mode == HOLD_BACK)

    assert first_hold(PlanSettings(bank_share=0.35)) < first_hold(PlanSettings(bank_share=1.0))


def test_brine_cap_limits_boost_blocks():
    capped = PlanSettings(max_boost_quarters=12)
    plan = make_plan(day([5] * 8 + [40] * 6 + [20] * 4), T0, flat(-5), capped)
    boosts = [b for b in plan.blocks if b.mode == BOOST]
    assert boosts and all(b.end - b.start <= 3 * H for b in boosts)


def test_warm_weather_needs_no_plan():
    plan = make_plan(day(WINTER), T0, flat(20), S)
    assert [b.mode for b in plan.blocks] == [NORMAL] and plan.need_kwh == 0


def test_no_price_for_now_means_no_plan():
    assert make_plan(day(WINTER), T0 - H, flat(5), S) is None
    assert make_plan([], T0, flat(5), S) is None


def test_forecast_reuses_its_ends():
    f = Forecast([(T0 + H, 2.0), (T0, 4.0)])
    assert f.at(T0 - H) == 4.0 and f.at(T0 + 30 * Q) == 2.0 and f.at(T0 + 10 * H) == 2.0


def test_plan_reports_need_and_saving():
    plan = make_plan(day(WINTER), T0, flat(5), S)
    assert abs(plan.need_kwh - 31 * 4 * S.need_per_quarter(5)) < 1e-9
    assert plan.saving > 0 and plan.store_limit == S.store_limit


def test_fast_enough_for_two_days_of_quarters():
    started = time.perf_counter()
    make_plan(day((WINTER * 2)[:36]), T0, flat(-5), PlanSettings(max_boost_quarters=24))
    assert time.perf_counter() - started < 1.0


def test_brine_guard_has_hysteresis():
    g = BrineGuard(0.0)
    assert not g.update(2.0)
    assert g.update(-0.5)
    assert g.update(0.5)  # still within 1 K
    assert g.update(None)  # unknown keeps the state
    assert not g.update(1.0)


def test_quarters_in_mode():
    assert quarters_in_mode(None, T0) == 0
    assert quarters_in_mode(T0, T0) == 1
    assert quarters_in_mode(T0 - 50 * timedelta(minutes=1), T0) == 3


def test_fit_heat_need_recovers_a_line():
    days = [(t, 1.4 * (18 - t) + (0.3 if i % 2 else -0.3)) for i, t in enumerate([2, 4, 5, 7, 8, 10, 11, 12, 13])]
    fit = fit_heat_need(days + [(20.0, 1.0), (9.0, 0.0)])  # a summer day and a day without heating are left out
    assert fit.days == 9
    assert abs(fit.kwh_per_degree_day - 1.4) < 0.05 and abs(fit.balance_temp - 18) < 0.3 and fit.r2 > 0.95


def test_fit_heat_need_refuses_thin_data():
    assert fit_heat_need([(5.0, 10.0)] * 3) is None  # too few days
    assert fit_heat_need([(5.0, 10.0), (5.2, 11.0)] * 5) is None  # no spread in temperature
    assert fit_heat_need([(t, t) for t in range(2, 12)]) is None  # rises with warmth: not heating
