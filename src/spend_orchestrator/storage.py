"""SQLite storage (stdlib only). Tables: performance, allocations.

Re-ingesting is idempotent: performance rows are upserted by (week, channel),
so loading the same export twice, or a corrected export, never duplicates a
week. Allocations are a pure function of performance plus config, so each
`allocate` run replaces the whole table inside one transaction rather than
appending to it. Every allocation row carries its plain-language reason.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from spend_orchestrator.models import Cycle, Decision, WeekPerformance

_SCHEMA = """
CREATE TABLE IF NOT EXISTS performance(
    week TEXT NOT NULL,
    channel TEXT NOT NULL,
    spend REAL NOT NULL,
    impressions INTEGER NOT NULL,
    clicks INTEGER NOT NULL,
    qualified_leads INTEGER NOT NULL,
    pipeline REAL NOT NULL,
    PRIMARY KEY (week, channel)
);
CREATE TABLE IF NOT EXISTS allocations(
    run_name TEXT NOT NULL,
    cycle_week TEXT NOT NULL,
    target_week TEXT NOT NULL,
    channel TEXT NOT NULL,
    action TEXT NOT NULL,
    previous_budget REAL NOT NULL,
    proposed_budget REAL NOT NULL,
    delta REAL NOT NULL,
    efficiency REAL,
    reason TEXT NOT NULL,
    PRIMARY KEY (target_week, channel)
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def save_performance(self, rows: list[WeekPerformance]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO performance VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        r.week.isoformat(),
                        r.channel,
                        r.spend,
                        r.impressions,
                        r.clicks,
                        r.qualified_leads,
                        r.pipeline,
                    )
                    for r in rows
                ],
            )

    def performance(self) -> list[WeekPerformance]:
        cur = self._conn.execute(
            "SELECT week, channel, spend, impressions, clicks, qualified_leads, pipeline"
            " FROM performance ORDER BY week, channel"
        )
        return [
            WeekPerformance(date.fromisoformat(w), c, s, i, k, q, p)
            for w, c, s, i, k, q, p in cur.fetchall()
        ]

    def replace_allocations(self, run_name: str, cycles: list[Cycle]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM allocations")
            self._conn.executemany(
                "INSERT INTO allocations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_name,
                        d.cycle_week.isoformat(),
                        d.target_week.isoformat(),
                        d.channel,
                        d.action,
                        d.previous,
                        d.proposed,
                        d.delta,
                        d.efficiency,
                        d.reason,
                    )
                    for cycle in cycles
                    for d in cycle.decisions
                ],
            )

    def decisions(self) -> list[Decision]:
        cur = self._conn.execute(
            "SELECT cycle_week, target_week, channel, action, previous_budget, proposed_budget,"
            " efficiency, reason FROM allocations ORDER BY target_week, channel"
        )
        return [
            Decision(date.fromisoformat(cw), date.fromisoformat(tw), c, a, prev, prop, eff, why)
            for cw, tw, c, a, prev, prop, eff, why in cur.fetchall()
        ]

    def counts(self) -> dict[str, int]:
        return {
            table: int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("performance", "allocations")
        }
