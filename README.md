# Spend Orchestrator
[![CI](https://github.com/yashsabbavarapu/spend-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/yashsabbavarapu/spend-orchestrator/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) [![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

A weekly budget allocator for paid acquisition channels that a performance marketer can actually sign off on. It reads funnel performance per channel, proposes next week's budget split, and writes a reason in plain language next to every dollar it moves.

The thesis: the allocation math is the easy part. No marketer lets an algorithm move money without minimum learning budgets, swing limits, and kill rules, so **the guardrails are the product**. The optimizer here is deliberately simple (follow pipeline per dollar), and the work goes into making sure it can never starve a channel mid-test, whipsaw a budget, or keep funding a channel that has stopped producing, and into explaining every move it makes.

It is the allocation half of a pair. [pipeline-attribution](../pipeline-attribution) measures which plays sourced pipeline; this project decides where next week's money goes. Measure with that, allocate with this.

## How it works

```
funnel.csv ──► ingest ──► SQLite ─────────────────────────────────────────────┐
(week, channel,           performance (upsert by week x channel)              │
 spend, impressions,                                                          │
 clicks, qualified_leads,                                                     ▼
 pipeline_dollars)                       allocate, once per week of data, oldest first
                                         ┌─────────────────────────────────────────┐
config.yaml ────────────────────────────►│ 1. kill rule      pause dying channels  │
(budget, learning minimums,              │ 2. learning floor per channel           │
 swing %, kill thresholds,               │ 3. swing limit vs last cycle's budget   │
 lookback weeks)                         │ 4. fill by pipeline per dollar, exact   │
                                         │    solve, leftover held as reserve      │
                                         └───────────────┬─────────────────────────┘
                                                         │ one Decision per channel per
                                                         │ cycle, each with its reason
                                                         ▼
                                         SQLite allocations (rebuilt each run)
                                                         │
                                         report: next week's split ─► this cycle's moves
                                                 ─► paused channels ─► allocator vs static
                                                 ─► efficiency trend ─► full decision log
                                                         │
                                                   allocations.md
```

1. **Ingest** weekly funnel rows from CSV. Channel names are normalized (`Paid Search` and `paid-search` both become `paid_search`). Malformed rows are skipped with a stderr warning, never fatal. Re-ingesting the same or a corrected export upserts by (week, channel), so nothing double counts.
2. **Allocate** replays one cycle per week of data, once `lookback_weeks` exist. Each cycle uses data through that week to set the next week's budget, starting from the previous cycle's proposal (the first cycle starts from an equal split, or from `starting_budget` per channel if set). The last cycle is "this cycle", the budget you would actually ship.
3. **Report** a Markdown summary and store every decision in SQLite. The report is a pure function of data plus config (no timestamps), so the same inputs always give byte-identical output.

## The guardrails, and why each exists

They apply in a fixed priority order. All thresholds live in `config.yaml`, none in code.

| # | Guardrail | Config | What it does | Why a marketer insists on it |
|---|---|---|---|---|
| 1 | Kill rule | `kill_rule.threshold_pipeline_per_dollar`, `kill_rule.consecutive_weeks` | A channel whose weekly pipeline per dollar sits below the threshold for N consecutive reported weeks is paused at $0. Overrides the swing limit and the learning minimum. A paused channel stays paused. | A slow 25% bleed on a dead channel is still money burned. One bad week is noise, two or three in a row is a pattern. Resuming is a human call (new creative, new offer), not something an optimizer should do on its own. |
| 2 | Learning minimum | `channels[].min_learning_budget` | Every live channel keeps at least its floor, even when efficiency says starve it. Beats the swing limit when they disagree. | Starved channels stop generating signal, and then you can never learn they recovered. Platforms also need minimum volume for their own bidding to work. |
| 3 | Swing limit | `max_swing_pct` | No live channel moves more than this percent up or down in one cycle. | Ad platforms reset learning phases on big budget jumps, and one lucky week should not move half the budget. Gradual moves are also reversible. |
| 4 | Budget cap and reserve | `total_budget` | Spend never exceeds the budget. Money the guardrails will not let any live channel absorb this cycle is held as an explicit, logged reserve. | Forcing freed budget onto a channel past its swing limit would break guardrail 3 by the back door. The reserve drains on its own as swing room compounds. |

Inside those bounds, budget follows lookback pipeline per dollar (pooled: total pipeline over total spend across the window). Each live channel gets `k * efficiency`, clamped to `[lower, upper]` where `lower = max(floor, previous * (1 - swing))` and `upper = max(floor, previous * (1 + swing))`. Total spend is nondecreasing and piecewise linear in `k`, so `k` is solved exactly from the breakpoints rather than by iteration (`solve_fill` in `allocate.py`). Same input, same output, to the cent.

Every decision row says what changed, by how much, and why. A sample from the demo:

> **LinkedIn ABM** (increase): Increased $2,418 (+25.0%) from $9,671 to $12,088. Pipeline-per-dollar over the last 3 weeks (2026-08-17 to 2026-08-31) was $3.37, vs $2.58 blended across live channels. Increase capped by the 25% swing limit; efficiency alone pointed to $13,044.

> **Niche community sponsorship** (pause): Paused, cut $6,476 to $0. Weekly pipeline-per-dollar was below the $0.50 kill threshold for 2 consecutive weeks (2026-08-31: $0.35, 2026-09-07: $0.15). Its budget returns to the pool for live channels. The kill rule overrides the swing limit and the learning minimum.

## Demo

```bash
python -m pip install -e .   # or: export PYTHONPATH=src
python -m spend_orchestrator ingest --performance fixtures/funnel.csv
python -m spend_orchestrator allocate --config config.yaml --output allocations.md
```

```
ingested 24 channel-weeks into spend_orchestrator.db; store now holds 24 channel-weeks
budget for week of 2026-09-28 (q3-demand-gen-demo):
  LinkedIn ABM: $18,333
  Paid search: $11,667
  Niche community sponsorship: $0 (paused)
wrote 6 cycles of decisions to allocations.md
```

The fixtures (`fixtures/funnel.csv`, regenerated deterministically by `scripts/gen_fixtures.py`) are 3 channels across 8 weeks at about $10k/week each:

| Channel | Story | Pipeline per $ week 1 to week 8 |
|---|---|---|
| LinkedIn ABM | Slow start, matures as account lists warm up | $1.80 to $4.60 |
| Paid search | Steady, slowly saturating | $3.40 to $2.70 |
| Niche community | The early star that dies mid-stream | $4.20 to $0.05 |

What the allocator does with them, cycle by cycle:

- **Budget for 2026-08-24.** Community still looks best on its 3-week average, so it gains 13% and LinkedIn is cut 23%. This is the allocator's one losing week: lookback efficiency trails reality, and the report says so.
- **2026-08-31 and 2026-09-07.** Community's average catches up with its collapse: it is cut, by the full 25% swing limit in the second cycle. LinkedIn grows by the capped 25% twice.
- **Budget for 2026-09-14.** Community has been below $0.50 for two consecutive weeks: the kill rule pauses it. Its $6,476 returns to the pool, but the two live channels can only grow 25% each, so $595 is held as reserve with its own logged reason.
- **2026-09-21 onward.** The reserve is released, community stays paused, and budget settles around the efficiency split of LinkedIn and search.

Allocator vs a static equal split ($10k per channel every week, nothing ever paused), both scored on the same fixtures:

| Week | Allocator pipeline | Static pipeline | Lift |
|---|---:|---:|---:|
| 2026-08-24 | $72,713 | $76,000 | -$3,287 (-4.3%) |
| 2026-08-31 | $74,856 | $71,500 | +$3,356 (+4.7%) |
| 2026-09-07 | $84,907 | $72,500 | +$12,407 (+17.1%) |
| 2026-09-14 | $106,512 | $72,800 | +$33,712 (+46.3%) |
| 2026-09-21 | $114,507 | $73,500 | +$41,007 (+55.8%) |
| **Total** | **$453,494** | **$366,300** | **+$87,194 (+23.8%)** |

The report (`allocations.md`) has, in order: next week's budget table, this cycle's moves with reasons, paused channels, the allocator vs static table above plus a next-week projection, a weekly efficiency trend with cost per qualified lead, and the full decision log for every cycle. Both tables in SQLite (`performance`, `allocations`) are queryable directly; every `allocations` row has a `reason` column.

## Configuration

```yaml
run_name: q3-demand-gen-demo
total_budget: 30000          # per week
max_swing_pct: 25            # per cycle, up or down
lookback_weeks: 3            # efficiency window
kill_rule:
  threshold_pipeline_per_dollar: 0.5
  consecutive_weeks: 2
channels:
  - name: linkedin_abm
    label: LinkedIn ABM
    min_learning_budget: 3000
    # starting_budget: 12000  # optional; set on every channel or none (default: equal split)
```

Config is a strict YAML subset parsed by a small stdlib parser (`yamlish.py`), so there are zero runtime dependencies. Validation rejects learning minimums that sum past the budget, swing limits outside (0, 100], duplicate channels, and partial `starting_budget` sets.

## What v1 does NOT do

- **Fixtures are synthetic.** The data is generated to tell a clear story (one channel maturing, one saturating, one dying). Real channels are noisier, and the defaults have not been tuned on real spend.
- **No ad-platform APIs.** Ingest is CSV only, and the allocator proposes budgets; it never pushes them to Google, Meta, or LinkedIn. A human ships the numbers.
- **Efficiency is pipeline per dollar, not incrementality.** It is a correlation: it cannot tell you what pipeline would have arrived without the spend. Retargeting and branded search look great on this metric whether or not they cause anything. Holdout tests or geo experiments are the way to measure that, and v1 has neither.
- **Linear returns in the backtest.** Both plans are scored as budget times that week's realized pipeline per dollar. Real channels saturate, so the lift is directional, not a forecast. The allocator also trails turns by roughly the lookback window, which the demo shows honestly in its first week.
- **Pipeline is whatever the CSV says.** Platform-reported pipeline over-credits; v1 does not reconcile it against the CRM.
- **No auto-resume.** A killed channel stays paused until someone edits the config or data. Deliberate, but it means a recovered channel will not come back by itself.
- **Single budget pool, weekly cadence.** No per-campaign or per-geo splits, no daily pacing, no funnel-stage (prospecting vs retargeting) budgets.

## v2 roadmap

- **Ad-platform ingestion.** Google Ads, Meta Marketing, and LinkedIn Marketing API sources behind the existing ingest boundary. The seam exists today: `sources.py` defines a `Source` protocol, the CLI ingests through `CsvSource`, and `GoogleAdsSource`, `MetaAdsSource`, and `LinkedInAdsSource` are typed stubs that raise `NotImplementedError`.
- **pipeline-attribution as the measurement leg.** `PipelineAttributionSource` (stubbed) will read per-channel credited pipeline from a pipeline-attribution store, so budget follows CRM-measured pipeline under a chosen attribution model instead of platform-reported numbers.
- **MMM-lite.** Fit a simple saturating response curve per channel (spend to pipeline, with adstock) so the allocator equalizes marginal returns instead of average returns, and the backtest scores with diminishing returns instead of linear ones.
- **Assisted resume.** Flag paused channels whose upstream signals (CTR, qualified lead rate) recovered, as a suggestion for a human-approved retest at the learning minimum.
- **Funnel-stage budgets.** Separate pools for prospecting and retargeting, moving accounts between plays as they progress through stages.

## Development

```bash
python -m pip install pytest mypy
python -m pytest -q
python -m mypy --strict src
```

Python 3.11+, stdlib only at runtime. CI runs both on 3.11 and 3.12.

```
src/spend_orchestrator/
  config.py     YAML config and validation
  yamlish.py    strict YAML subset parser (stdlib)
  ingest.py     CSV reader
  sources.py    ingest boundary: CsvSource plus v2 stubs
  storage.py    SQLite performance and allocations tables
  allocate.py   guardrails, exact fill, decision reasons
  backtest.py   allocator vs static equal split
  report.py     Markdown report
  __main__.py   CLI
```
