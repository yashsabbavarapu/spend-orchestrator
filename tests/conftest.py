"""Shared builders: the demo fixtures plus small hand-built series for edge cases."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from spend_orchestrator.config import Config, load_config, parse_config
from spend_orchestrator.ingest import read_performance
from spend_orchestrator.models import WeekPerformance

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "funnel.csv"
DEMO_CONFIG = ROOT / "config.yaml"
W0 = date(2026, 1, 5)  # a Monday


@pytest.fixture
def demo_config() -> Config:
    return load_config(DEMO_CONFIG)


@pytest.fixture
def demo_perf() -> list[WeekPerformance]:
    return read_performance(FIXTURE)


def week(i: int) -> date:
    return W0 + timedelta(weeks=i)


def series(channel: str, ppds: list[float], spend: float = 10000.0) -> list[WeekPerformance]:
    """One channel's weekly rows with the given pipeline-per-dollar, week 0 onward."""
    return [
        WeekPerformance(week(i), channel, spend, 1000, 10, 1, round(spend * p, 2))
        for i, p in enumerate(ppds)
    ]


def make_config(**overrides: Any) -> Config:
    data: dict[str, Any] = {
        "run_name": "test",
        "total_budget": 30000,
        "max_swing_pct": 25,
        "lookback_weeks": 2,
        "kill_rule": {"threshold_pipeline_per_dollar": 0.5, "consecutive_weeks": 2},
        "channels": [
            {"name": "a", "min_learning_budget": 3000},
            {"name": "b", "min_learning_budget": 3000},
            {"name": "c", "min_learning_budget": 3000},
        ],
    }
    data.update(overrides)
    return parse_config(data)
