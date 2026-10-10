"""Read weekly funnel performance from CSV.

Columns: week, channel, spend, impressions, clicks, qualified_leads,
pipeline_dollars. Week is an ISO date (the Monday the week starts on).
Malformed rows are skipped with a warning on stderr, never fatal, so one bad
export line does not block a budget cycle. If a file repeats a
(week, channel) pair, the last row wins and a warning names it.
"""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path
from typing import TextIO

from spend_orchestrator.config import normalize_channel
from spend_orchestrator.models import WeekPerformance

REQUIRED_COLUMNS = (
    "week",
    "channel",
    "spend",
    "impressions",
    "clicks",
    "qualified_leads",
    "pipeline_dollars",
)


class IngestError(ValueError):
    pass


def _parse_row(row: dict[str, str]) -> WeekPerformance:
    week = date.fromisoformat(row["week"].strip())
    channel = normalize_channel(row["channel"])
    if not channel:
        raise ValueError("empty channel")
    spend = float(row["spend"])
    impressions = int(row["impressions"])
    clicks = int(row["clicks"])
    leads = int(row["qualified_leads"])
    pipeline = float(row["pipeline_dollars"])
    if min(spend, impressions, clicks, leads, pipeline) < 0:
        raise ValueError("negative value")
    return WeekPerformance(week, channel, round(spend, 2), impressions, clicks, leads, round(pipeline, 2))


def read_performance(path: Path, warn: TextIO = sys.stderr) -> list[WeekPerformance]:
    try:
        handle = path.open(newline="", encoding="utf-8")
    except FileNotFoundError:
        raise IngestError(f"performance file not found: {path}") from None
    with handle:
        reader = csv.DictReader(handle)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise IngestError(f"{path}: missing columns {', '.join(missing)}")
        rows: dict[tuple[date, str], WeekPerformance] = {}
        for line_no, row in enumerate(reader, start=2):
            try:
                perf = _parse_row(row)
            except (ValueError, TypeError, AttributeError) as exc:
                print(f"warning: {path}:{line_no}: skipped malformed row ({exc})", file=warn)
                continue
            key = (perf.week, perf.channel)
            if key in rows:
                print(
                    f"warning: {path}:{line_no}: duplicate {perf.channel} for week"
                    f" {perf.week.isoformat()}, keeping the later row",
                    file=warn,
                )
            rows[key] = perf
    return [rows[k] for k in sorted(rows)]
