"""Per-run configuration: channels, budget, and every guardrail threshold.

Nothing about a run is hardcoded. A different budget, swing limit, or kill
rule is a different YAML file, not a code change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spend_orchestrator import yamlish


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ChannelConfig:
    name: str
    label: str
    min_learning_budget: float
    starting_budget: float | None  # None means "equal split of total_budget"


@dataclass(frozen=True)
class Config:
    run_name: str
    total_budget: float
    max_swing_pct: float
    lookback_weeks: int
    kill_threshold: float  # pipeline-per-dollar below which a week counts toward the kill rule
    kill_after_weeks: int
    channels: tuple[ChannelConfig, ...]

    @property
    def channel_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.channels)

    def channel(self, name: str) -> ChannelConfig:
        for c in self.channels:
            if c.name == name:
                return c
        raise KeyError(name)

    def label(self, name: str) -> str:
        return self.channel(name).label

    def starting_allocation(self) -> dict[str, float]:
        equal = round(self.total_budget / len(self.channels), 2)
        return {
            c.name: c.starting_budget if c.starting_budget is not None else equal
            for c in self.channels
        }


def normalize_channel(raw: str) -> str:
    """`Paid Search`, `paid-search`, and `paid_search` all become `paid_search`."""
    return re.sub(r"[\s\-]+", "_", raw.strip().lower())


def _number(value: Any, key: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} must be a number")
    if float(value) < minimum:
        raise ConfigError(f"{key} must be >= {minimum:g}, got {value}")
    return float(value)


def _int(value: Any, key: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be a whole number")
    if value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}, got {value}")
    return value


def parse_config(data: Any) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping")

    run_name = data.get("run_name")
    if not isinstance(run_name, str) or not run_name:
        raise ConfigError("run_name is required")

    total = _number(data.get("total_budget"), "total_budget")
    if total <= 0:
        raise ConfigError("total_budget must be positive")
    swing = _number(data.get("max_swing_pct"), "max_swing_pct")
    if not 0 < swing <= 100:
        raise ConfigError(f"max_swing_pct must be in (0, 100], got {swing:g}")
    lookback = _int(data.get("lookback_weeks"), "lookback_weeks")

    kill = data.get("kill_rule")
    if not isinstance(kill, dict):
        raise ConfigError("kill_rule must be a mapping with threshold_pipeline_per_dollar and consecutive_weeks")
    kill_threshold = _number(
        kill.get("threshold_pipeline_per_dollar"), "kill_rule.threshold_pipeline_per_dollar"
    )
    kill_after = _int(kill.get("consecutive_weeks"), "kill_rule.consecutive_weeks")

    raw_channels = data.get("channels")
    if not isinstance(raw_channels, list) or not raw_channels:
        raise ConfigError("channels must be a non-empty list")
    channels: list[ChannelConfig] = []
    for i, raw in enumerate(raw_channels):
        where = f"channels[{i}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{where} must be a mapping")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{where}.name is required")
        norm = normalize_channel(name)
        label = raw.get("label", name)
        if not isinstance(label, str):
            raise ConfigError(f"{where}.label must be a string")
        min_budget = _number(raw.get("min_learning_budget"), f"{where}.min_learning_budget")
        start_raw = raw.get("starting_budget")
        start = None if start_raw is None else _number(start_raw, f"{where}.starting_budget")
        channels.append(ChannelConfig(norm, label, min_budget, start))

    names = [c.name for c in channels]
    if len(set(names)) != len(names):
        raise ConfigError(f"duplicate channel names: {names}")
    floors = sum(c.min_learning_budget for c in channels)
    if floors > total:
        raise ConfigError(
            f"learning minimums sum to {floors:,.0f}, more than total_budget {total:,.0f}"
        )
    starts = [c.starting_budget for c in channels]
    if any(s is not None for s in starts):
        if any(s is None for s in starts):
            raise ConfigError("set starting_budget on every channel or on none")
        if sum(s for s in starts if s is not None) > total + 0.01:
            raise ConfigError("starting budgets sum to more than total_budget")

    return Config(
        run_name=run_name,
        total_budget=total,
        max_swing_pct=swing,
        lookback_weeks=lookback,
        kill_threshold=kill_threshold,
        kill_after_weeks=kill_after,
        channels=tuple(channels),
    )


def load_config(path: Path) -> Config:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    try:
        return parse_config(yamlish.loads(text))
    except yamlish.YamlError as exc:
        raise ConfigError(f"{path}: {exc}") from None
