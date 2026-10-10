"""Allocator vs a static equal split, scored on the same fixtures.

The static baseline puts `total_budget / n_channels` on every configured
channel every week and never pauses anything: the "set it and forget it"
plan the allocator has to beat.

Scoring assumption, stated plainly: pipeline scales linearly with spend at
the channel's realized pipeline-per-dollar for that week. So a budget of B on
a channel that week produced pipeline/spend of E is scored as B * E, for both
plans. This ignores diminishing returns and is why v1 calls the numbers
"projected", not "measured".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from spend_orchestrator.config import Config
from spend_orchestrator.models import Cycle, WeekPerformance


@dataclass(frozen=True)
class WeekScore:
    week: date
    allocator: float
    static: float

    @property
    def lift(self) -> float:
        return self.allocator - self.static


def static_split(config: Config) -> dict[str, float]:
    share = config.total_budget / len(config.channels)
    return {c: share for c in config.channel_names}


def _score(budget: dict[str, float], ppd: dict[str, float | None]) -> float:
    return sum(b * (ppd.get(c) or 0.0) for c, b in budget.items())


def backtest(
    config: Config, cycles: list[Cycle], performance: list[WeekPerformance]
) -> list[WeekScore]:
    """Score every cycle whose target week has realized data."""
    realized: dict[date, dict[str, float | None]] = {}
    for r in performance:
        realized.setdefault(r.week, {})[r.channel] = r.pipeline_per_dollar
    static = static_split(config)
    return [
        WeekScore(
            cycle.target_week,
            round(_score(cycle.allocations, realized[cycle.target_week]), 2),
            round(_score(static, realized[cycle.target_week]), 2),
        )
        for cycle in cycles
        if cycle.target_week in realized
    ]


def project_next(config: Config, cycle: Cycle) -> WeekScore:
    """Forecast the upcoming week using each channel's lookback pipeline-per-dollar."""
    return WeekScore(
        cycle.target_week,
        round(_score(cycle.allocations, cycle.efficiencies), 2),
        round(_score(static_split(config), cycle.efficiencies), 2),
    )
