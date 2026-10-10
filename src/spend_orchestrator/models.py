"""Plain data types shared by ingest, storage, the allocator, and the report."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class WeekPerformance:
    """One channel's funnel numbers for one week (week = the Monday it starts on)."""

    week: date
    channel: str
    spend: float
    impressions: int
    clicks: int
    qualified_leads: int
    pipeline: float

    @property
    def pipeline_per_dollar(self) -> float | None:
        return self.pipeline / self.spend if self.spend > 0 else None


# Decision actions. "reserve" is the pseudo-channel for budget no live channel could absorb.
INCREASE = "increase"
DECREASE = "decrease"
HOLD = "hold"
PAUSE = "pause"
PAUSED = "paused"
RESERVE = "reserve"
RESERVE_CHANNEL = "unallocated"


@dataclass(frozen=True)
class Decision:
    """One line of the decision log: what changed for one channel, by how much, and why."""

    cycle_week: date  # last week of data the decision was made on
    target_week: date  # the week the new budget applies to
    channel: str
    action: str
    previous: float
    proposed: float
    efficiency: float | None  # lookback pipeline-per-dollar, None if no spend in the window
    reason: str

    @property
    def delta(self) -> float:
        return round(self.proposed - self.previous, 2)

    @property
    def delta_pct(self) -> float | None:
        return self.delta / self.previous * 100 if self.previous > 0 else None


@dataclass(frozen=True)
class Cycle:
    """One allocation cycle: the budget split for target_week plus the log that explains it."""

    cycle_week: date
    target_week: date
    allocations: dict[str, float]  # every configured channel, paused ones at 0
    reserve: float
    decisions: tuple[Decision, ...]
    paused: frozenset[str]
    efficiencies: dict[str, float | None]
