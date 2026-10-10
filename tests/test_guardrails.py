"""Guardrail enforcement: learning minimums, swing limits, kill rule, budget cap."""
from __future__ import annotations

from datetime import date

import pytest

from conftest import make_config, series
from spend_orchestrator.allocate import AllocationError, run_cycles, solve_fill
from spend_orchestrator.config import Config
from spend_orchestrator.models import PAUSE, PAUSED, RESERVE, RESERVE_CHANNEL, WeekPerformance

TOL = 0.011  # cent rounding


def _check_invariants(config: Config, perf: list[WeekPerformance]) -> None:
    cycles = run_cycles(config, perf)
    swing = config.max_swing_pct / 100
    previous = config.starting_allocation()
    for cycle in cycles:
        assert sum(cycle.allocations.values()) + cycle.reserve <= config.total_budget + TOL
        for c in config.channel_names:
            new, prev = cycle.allocations[c], previous[c]
            if c in cycle.paused:
                assert new == 0.0
                continue
            floor = config.channel(c).min_learning_budget
            assert new >= floor - TOL, f"{c} below learning minimum in {cycle.cycle_week}"
            # Swing cap holds unless the learning minimum forces a bigger move up.
            assert new <= max(prev * (1 + swing), floor) + TOL, f"{c} swung up too far"
            assert new >= prev * (1 - swing) - TOL, f"{c} swung down too far"
        previous = cycle.allocations


def test_demo_fixture_respects_every_guardrail(
    demo_config: Config, demo_perf: list[WeekPerformance]
) -> None:
    _check_invariants(demo_config, demo_perf)


def test_learning_minimum_holds_a_weak_but_alive_channel() -> None:
    # c is weak (0.6, above the 0.5 kill line) for many weeks: efficiency says starve it,
    # the learning minimum says keep it at $3,000.
    config = make_config()
    perf = series("a", [4.0] * 12) + series("b", [4.0] * 12) + series("c", [0.6] * 12)
    cycles = run_cycles(config, perf)
    _check_invariants(config, perf)
    final = cycles[-1]
    assert "c" not in final.paused
    assert final.allocations["c"] == pytest.approx(3000.0)
    floor_reason = [d for d in final.decisions if d.channel == "c"][0].reason
    assert "learning minimum" in floor_reason


def test_swing_limit_caps_a_sudden_winner() -> None:
    # b suddenly becomes 10x better: efficiency alone would hand it almost everything.
    config = make_config()
    perf = series("a", [3.0] * 6) + series("b", [3.0, 3.0, 30.0, 30.0, 30.0, 30.0]) + series("c", [3.0] * 6)
    cycles = run_cycles(config, perf)
    _check_invariants(config, perf)
    previous = config.starting_allocation()
    for cycle in cycles:
        assert cycle.allocations["b"] <= previous["b"] * 1.25 + TOL
        previous = cycle.allocations
    capped = [d for cy in cycles for d in cy.decisions if d.channel == "b" and "swing limit" in d.reason]
    assert capped, "a capped increase must say the swing limit caused it"


def test_kill_rule_pauses_the_dying_channel_on_schedule(
    demo_config: Config, demo_perf: list[WeekPerformance]
) -> None:
    cycles = run_cycles(demo_config, demo_perf)
    by_week = {cy.cycle_week: cy for cy in cycles}
    # 2026-08-31 is the first week below $0.50 (0.35): one week is not enough.
    assert "niche_community" not in by_week[date(2026, 8, 31)].paused
    # 2026-09-07 (0.15) is the second consecutive week: kill fires.
    fired = by_week[date(2026, 9, 7)]
    assert "niche_community" in fired.paused
    assert fired.allocations["niche_community"] == 0.0
    pause = [d for d in fired.decisions if d.channel == "niche_community"][0]
    assert pause.action == PAUSE
    assert pause.previous > 0 and pause.proposed == 0.0
    # And it stays paused, at $0, for every later cycle.
    for cy in cycles:
        if cy.cycle_week > date(2026, 9, 7):
            assert cy.allocations["niche_community"] == 0.0
            assert [d for d in cy.decisions if d.channel == "niche_community"][0].action == PAUSED
    # Healthy channels are never killed.
    assert all(cy.paused <= {"niche_community"} for cy in cycles)


def test_kill_needs_consecutive_weeks() -> None:
    # Below threshold, recovers, below again: never two in a row, never killed.
    config = make_config()
    perf = series("a", [3.0] * 6) + series("b", [3.0] * 6) + series("c", [0.2, 2.0, 0.2, 2.0, 0.2, 2.0])
    assert all("c" not in cy.paused for cy in run_cycles(config, perf))


def test_zero_spend_week_breaks_a_kill_streak() -> None:
    config = make_config()
    c_rows = series("c", [0.2, 0.2, 0.2])
    c_rows[1] = WeekPerformance(c_rows[1].week, "c", 0.0, 0, 0, 0, 0.0)
    perf = series("a", [3.0] * 3) + series("b", [3.0] * 3) + c_rows
    cycles = run_cycles(config, perf)
    assert "c" not in cycles[0].paused  # weeks 0-1: second week has no reading
    assert "c" not in cycles[1].paused  # weeks 1-2: same


def test_kill_overrides_swing_and_minimum() -> None:
    config = make_config()
    perf = series("a", [3.0] * 3) + series("b", [3.0] * 3) + series("c", [0.1] * 3)
    first = run_cycles(config, perf)[0]
    assert "c" in first.paused
    assert first.allocations["c"] == 0.0  # a -100% move, well past the 25% swing and $3,000 floor


def test_unabsorbable_budget_is_held_as_logged_reserve() -> None:
    # Two of three channels die at once; the survivor can only grow 25% per cycle.
    config = make_config()
    perf = series("a", [4.0] * 8) + series("b", [0.1] * 8) + series("c", [0.1] * 8)
    cycles = run_cycles(config, perf)
    first = cycles[0]
    assert first.allocations["a"] == pytest.approx(12500.0)
    assert first.reserve == pytest.approx(17500.0)
    reserve = [d for d in first.decisions if d.channel == RESERVE_CHANNEL]
    assert reserve and reserve[0].action == RESERVE and "swing limit" in reserve[0].reason
    # The reserve drains as the swing room compounds, and spend never exceeds budget.
    assert cycles[-1].reserve < first.reserve
    _check_invariants(config, perf)


def test_floor_wins_when_previous_budget_is_below_it() -> None:
    config = make_config(
        channels=[
            {"name": "a", "min_learning_budget": 3000, "starting_budget": 26000},
            {"name": "b", "min_learning_budget": 3000, "starting_budget": 3000},
            {"name": "c", "min_learning_budget": 3000, "starting_budget": 1000},
        ]
    )
    perf = series("a", [3.0] * 3) + series("b", [3.0] * 3) + series("c", [0.6] * 3)
    first = run_cycles(config, perf)[0]
    assert first.allocations["c"] >= 3000 - TOL  # +200%, allowed because the floor wins


def test_too_little_data_is_an_error() -> None:
    config = make_config(lookback_weeks=4)
    with pytest.raises(AllocationError, match="at least 4 weeks"):
        run_cycles(config, series("a", [3.0] * 3))


def test_solve_fill_hits_total_inside_bounds() -> None:
    weights = {"a": 4.0, "b": 2.0, "c": 1.0}
    lo = {"a": 1000.0, "b": 1000.0, "c": 3000.0}
    hi = {"a": 12000.0, "b": 9000.0, "c": 9000.0}
    x = solve_fill(20000.0, weights, lo, hi)
    assert sum(x.values()) == pytest.approx(20000.0)
    assert all(lo[c] - 1e-9 <= x[c] <= hi[c] + 1e-9 for c in x)
    # k = 17000 / 6: a and b follow 4k and 2k, c would get k but is floored at 3000.
    assert x["c"] == pytest.approx(3000.0)
    assert x["a"] == pytest.approx(34000 / 3)
    assert x["b"] == pytest.approx(17000 / 3)
    capped = solve_fill(20000.0, weights, lo, {**hi, "a": 8000.0})
    assert capped["a"] == pytest.approx(8000.0)  # upper bound binds
    assert sum(capped.values()) == pytest.approx(20000.0)


def test_solve_fill_stops_at_upper_bounds() -> None:
    x = solve_fill(100.0, {"a": 1.0, "b": 1.0}, {"a": 0.0, "b": 0.0}, {"a": 20.0, "b": 30.0})
    assert x == {"a": 20.0, "b": 30.0}
