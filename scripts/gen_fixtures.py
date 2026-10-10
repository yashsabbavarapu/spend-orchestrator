"""Regenerate fixtures/funnel.csv. Deterministic: no randomness, so diffs mean real changes.

Story across 8 weeks, three channels, about $10k/week each (the historical
equal split the allocator is measured against):

- linkedin_abm: slow start, then matures as account lists warm up (ppd 1.8 -> 4.6).
- paid_search: steady, slowly saturating (ppd 3.4 -> 2.7).
- niche_community: the early star that dies mid-stream (ppd 4.2 -> 0.05),
  as if a sponsored newsletter lost its audience.

Run: python scripts/gen_fixtures.py
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

START = date(2026, 8, 3)  # a Monday

# channel -> (weekly spend, weekly pipeline-per-dollar, CTR, $ per impression, $ pipeline per qualified lead)
CHANNELS: dict[str, tuple[list[float], list[float], float, float, float]] = {
    "linkedin_abm": (
        [10000, 10200, 9800, 10100, 9900, 10000, 10300, 9700],
        [1.8, 2.3, 2.9, 3.4, 3.8, 4.2, 4.4, 4.6],
        0.006,
        0.045,
        9000.0,
    ),
    "paid_search": (
        [10000, 9900, 10100, 10000, 10200, 9800, 10000, 10100],
        [3.4, 3.3, 3.2, 3.1, 3.0, 2.9, 2.8, 2.7],
        0.035,
        0.09,
        4000.0,
    ),
    "niche_community": (
        [10000, 10000, 9900, 10100, 10000, 9900, 10000, 10000],
        [4.2, 3.6, 2.4, 1.1, 0.35, 0.15, 0.08, 0.05],
        0.012,
        0.03,
        5000.0,
    ),
}


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "fixtures" / "funnel.csv"
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["week", "channel", "spend", "impressions", "clicks", "qualified_leads", "pipeline_dollars"]
        )
        for i in range(8):
            week = START + timedelta(weeks=i)
            for name, (spends, ppds, ctr, cpm_unit, per_lead) in CHANNELS.items():
                spend = spends[i]
                pipeline = round(spend * ppds[i], 2)
                impressions = round(spend / cpm_unit)
                clicks = round(impressions * ctr)
                leads = round(pipeline / per_lead)
                writer.writerow(
                    [week.isoformat(), name, f"{spend:.2f}", impressions, clicks, leads, f"{pipeline:.2f}"]
                )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
