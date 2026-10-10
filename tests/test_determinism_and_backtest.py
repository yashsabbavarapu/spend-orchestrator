"""Same input, same output; and the allocator beats a static equal split on the fixtures."""
from __future__ import annotations

import random

import pytest

from conftest import week
from spend_orchestrator.allocate import run_cycles
from spend_orchestrator.backtest import backtest, project_next, static_split
from spend_orchestrator.config import Config
from spend_orchestrator.models import WeekPerformance
from spend_orchestrator.report import render_report


def _report(config: Config, perf: list[WeekPerformance]) -> str:
    cycles = run_cycles(config, perf)
    return render_report(config, cycles, perf, backtest(config, cycles, perf))


def test_same_input_same_output(demo_config: Config, demo_perf: list[WeekPerformance]) -> None:
    assert run_cycles(demo_config, demo_perf) == run_cycles(demo_config, demo_perf)
    assert _report(demo_config, demo_perf) == _report(demo_config, demo_perf)


def test_row_order_does_not_matter(demo_config: Config, demo_perf: list[WeekPerformance]) -> None:
    shuffled = list(demo_perf)
    random.Random(7).shuffle(shuffled)
    assert run_cycles(demo_config, shuffled) == run_cycles(demo_config, demo_perf)
    assert _report(demo_config, shuffled) == _report(demo_config, demo_perf)


def test_allocator_beats_static_on_fixtures(
    demo_config: Config, demo_perf: list[WeekPerformance]
) -> None:
    cycles = run_cycles(demo_config, demo_perf)
    scores = backtest(demo_config, cycles, demo_perf)
    assert len(scores) == 5  # target weeks 4..8 have realized data
    allocator = sum(s.allocator for s in scores)
    static = sum(s.static for s in scores)
    assert allocator > static * 1.15
    # Honest about lag: the first week, it still trusted the fading community channel.
    assert scores[0].lift < 0
    # The forward projection also favors the allocator.
    nxt = project_next(demo_config, cycles[-1])
    assert nxt.allocator > nxt.static


def test_static_baseline_hand_check(demo_config: Config, demo_perf: list[WeekPerformance]) -> None:
    assert static_split(demo_config) == {
        "linkedin_abm": 10000.0,
        "paid_search": 10000.0,
        "niche_community": 10000.0,
    }
    scores = backtest(demo_config, run_cycles(demo_config, demo_perf), demo_perf)
    # Week of 2026-08-24: realized ppd 3.4, 3.1, 1.1 -> $10k each = 34k + 31k + 11k.
    assert scores[0].static == pytest.approx(76000.0)


def test_test_helper_weeks_are_mondays() -> None:
    assert week(0).weekday() == 0 and week(5).weekday() == 0
