"""Every allocation decision carries its reason: what changed, by how much, and why."""
from __future__ import annotations

import re

from conftest import make_config, series
from spend_orchestrator.allocate import money, run_cycles
from spend_orchestrator.config import Config
from spend_orchestrator.models import (
    DECREASE,
    HOLD,
    INCREASE,
    PAUSE,
    PAUSED,
    RESERVE,
    RESERVE_CHANNEL,
    WeekPerformance,
)


def test_every_channel_gets_a_decision_every_cycle(
    demo_config: Config, demo_perf: list[WeekPerformance]
) -> None:
    for cycle in run_cycles(demo_config, demo_perf):
        logged = [d.channel for d in cycle.decisions if d.channel != RESERVE_CHANNEL]
        assert sorted(logged) == sorted(demo_config.channel_names)
        for d in cycle.decisions:
            assert d.reason.strip(), f"empty reason for {d.channel} in {d.cycle_week}"


def test_every_move_states_what_how_much_and_why(
    demo_config: Config, demo_perf: list[WeekPerformance]
) -> None:
    moves = [d for cy in run_cycles(demo_config, demo_perf) for d in cy.decisions]
    assert {d.action for d in moves} >= {INCREASE, DECREASE, PAUSE, PAUSED, RESERVE}
    for d in moves:
        if d.action in (INCREASE, DECREASE):
            # What changed and by how much: old and new budget plus the delta, in dollars.
            assert money(d.previous) in d.reason and money(d.proposed) in d.reason
            assert money(abs(d.delta)) in d.reason
            assert re.search(r"[+-]\d+\.\d%", d.reason)
            # Why: the efficiency evidence and the rule that shaped the number.
            assert "Pipeline-per-dollar over" in d.reason
            assert re.search(
                r"efficiency-weighted share|swing limit|learning minimum|efficiency share", d.reason
            )
        elif d.action == PAUSE:
            assert "kill threshold" in d.reason and "consecutive weeks" in d.reason
            assert money(d.previous) in d.reason
        elif d.action == PAUSED:
            assert "Remains paused" in d.reason
        elif d.action == RESERVE:
            assert "unallocated" in d.reason or "released" in d.reason


def test_hold_is_logged_with_a_reason() -> None:
    config = make_config()
    perf = series("a", [3.0] * 3) + series("b", [3.0] * 3) + series("c", [3.0] * 3)
    cycle = run_cycles(config, perf)[0]
    assert {d.action for d in cycle.decisions} == {HOLD}
    for d in cycle.decisions:
        assert d.reason.startswith(f"Held at {money(d.proposed)}.")
        assert "Pipeline-per-dollar" in d.reason


def test_no_data_channel_is_held_and_says_why() -> None:
    config = make_config()
    perf = series("a", [3.0] * 3) + series("b", [3.0] * 3)  # c never reported
    cycle = run_cycles(config, perf)[0]
    c = [d for d in cycle.decisions if d.channel == "c"][0]
    assert c.proposed == c.previous
    assert "no efficiency read" in c.reason
