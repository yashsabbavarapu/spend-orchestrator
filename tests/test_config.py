"""YAML config loading and validation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conftest import DEMO_CONFIG, make_config
from spend_orchestrator.config import ConfigError, load_config, normalize_channel


def test_demo_config_loads() -> None:
    config = load_config(DEMO_CONFIG)
    assert config.total_budget == 30000
    assert config.max_swing_pct == 25
    assert config.lookback_weeks == 3
    assert config.kill_threshold == 0.5 and config.kill_after_weeks == 2
    assert config.channel_names == ("linkedin_abm", "paid_search", "niche_community")
    assert config.channel("niche_community").min_learning_budget == 2000
    assert config.starting_allocation() == {c: 10000.0 for c in config.channel_names}


def test_channel_names_are_normalized() -> None:
    assert normalize_channel(" Paid Search ") == "paid_search"
    assert normalize_channel("paid-search") == "paid_search"
    config = make_config(channels=[{"name": "LinkedIn ABM", "min_learning_budget": 0}])
    assert config.channel_names == ("linkedin_abm",)
    assert config.label("linkedin_abm") == "LinkedIn ABM"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"total_budget": 0}, "positive"),
        ({"max_swing_pct": 0}, "max_swing_pct"),
        ({"max_swing_pct": 150}, "max_swing_pct"),
        ({"lookback_weeks": 0}, "lookback_weeks"),
        ({"kill_rule": None}, "kill_rule"),
        ({"kill_rule": {"threshold_pipeline_per_dollar": 0.5}}, "consecutive_weeks"),
        ({"channels": []}, "non-empty"),
        ({"channels": [{"name": "a", "min_learning_budget": 20000}, {"name": "b", "min_learning_budget": 20000}]}, "learning minimums"),
        ({"channels": [{"name": "a", "min_learning_budget": 1}, {"name": "A", "min_learning_budget": 1}]}, "duplicate"),
        ({"channels": [{"name": "a", "min_learning_budget": 1, "starting_budget": 5}, {"name": "b", "min_learning_budget": 1}]}, "every channel"),
        ({"total_budget": True}, "number"),
    ],
)
def test_invalid_config_is_rejected(override: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        make_config(**override)


def test_missing_and_malformed_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("run_name: x\n  oops: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="indentation"):
        load_config(bad)
