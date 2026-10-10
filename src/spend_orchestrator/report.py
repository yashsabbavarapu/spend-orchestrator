"""Render the Markdown summary a marketer skims before approving next week's budget.

Order is by decision value: the proposed split first, then why each line
moved, what got paused, whether the allocator is actually beating a static
split, the efficiency trend behind it all, and the full decision log last.
Output is a pure function of its inputs (no timestamps), so the same data
and config always produce byte-identical reports.
"""
from __future__ import annotations

from spend_orchestrator.allocate import money, ratio, window_efficiency
from spend_orchestrator.backtest import WeekScore, project_next, static_split
from spend_orchestrator.config import Config
from spend_orchestrator.models import (
    PAUSE,
    RESERVE_CHANNEL,
    Cycle,
    Decision,
    WeekPerformance,
)


def _signed_money(x: float) -> str:
    if abs(x) < 1:
        return "$0"
    return f"+{money(x)}" if x > 0 else f"-{money(-x)}"


def _change(d: Decision) -> str:
    pct = d.delta_pct
    if abs(d.delta) < 1:
        return "no change"
    return _signed_money(d.delta) + (f" ({pct:+.1f}%)" if pct is not None else "")


def _label(config: Config, channel: str) -> str:
    return "Unallocated reserve" if channel == RESERVE_CHANNEL else config.label(channel)


def _status(cycle: Cycle, channel: str) -> str:
    return "paused" if channel in cycle.paused else "live"


def _trend(series: list[WeekPerformance], lookback: int) -> str:
    recent = window_efficiency(series[-lookback:])
    prior = window_efficiency(series[-2 * lookback : -lookback])
    if recent is None or prior is None or prior == 0:
        return "n/a"
    change = (recent - prior) / prior
    if change > 0.05:
        return f"improving ({change:+.0%})"
    if change < -0.05:
        return f"declining ({change:+.0%})"
    return "flat"


def _cell(x: float | None) -> str:
    return ratio(x) if x is not None else "n/a"


def render_report(
    config: Config,
    cycles: list[Cycle],
    performance: list[WeekPerformance],
    scores: list[WeekScore],
) -> str:
    cycle = cycles[-1]
    names = config.channel_names
    performance = sorted(performance, key=lambda r: (r.week, r.channel))
    by_channel = {c: [r for r in performance if r.channel == c] for c in names}
    weeks = sorted({r.week for r in performance if r.channel in names})
    out: list[str] = []
    add = out.append

    add(f"# Spend allocation: {config.run_name}")
    add("")
    add(
        f"Decided on data through the week of {cycle.cycle_week.isoformat()}."
        f" Budget applies to the week of **{cycle.target_week.isoformat()}**."
    )
    add("")
    add(
        f"Guardrails in force: total budget {money(config.total_budget)}/week;"
        f" learning minimums {', '.join(f'{c.label} {money(c.min_learning_budget)}' for c in config.channels)};"
        f" max swing {config.max_swing_pct:g}% per cycle; kill rule pauses a channel after"
        f" {config.kill_after_weeks} consecutive weeks below {ratio(config.kill_threshold)}"
        f" pipeline per dollar; efficiency pooled over the last {config.lookback_weeks} weeks."
    )
    add("")

    # Next week's budget.
    add("## Next week's budget")
    add("")
    add("| Channel | Status | Current | Proposed | Change | Pipeline per $ (lookback) |")
    add("|---|---|---:|---:|---:|---:|")
    decisions = {d.channel: d for d in cycle.decisions}
    for c in names:
        d = decisions[c]
        add(
            f"| {config.label(c)} | {_status(cycle, c)} | {money(d.previous)}"
            f" | **{money(d.proposed)}** | {_change(d)} | {_cell(cycle.efficiencies[c])} |"
        )
    if RESERVE_CHANNEL in decisions:
        d = decisions[RESERVE_CHANNEL]
        add(f"| Unallocated reserve | held | {money(d.previous)} | **{money(d.proposed)}** | {_change(d)} | n/a |")
    placed = sum(cycle.allocations.values())
    add(f"| **Total** | | | **{money(placed + cycle.reserve)}** | | |")
    add("")

    # This cycle's moves.
    add("## This cycle's moves")
    add("")
    for d in cycle.decisions:
        add(f"- **{_label(config, d.channel)}** ({d.action}): {d.reason}")
    add("")

    # Paused channels.
    add("## Paused channels")
    add("")
    paused = [d for cy in cycles for d in cy.decisions if d.action == PAUSE]
    if not paused:
        add("None. Every channel is live.")
    else:
        for d in paused:
            add(
                f"- **{config.label(d.channel)}**, paused in the {d.cycle_week.isoformat()} cycle"
                f" (budget for {d.target_week.isoformat()} onward). {d.reason}"
            )
    add("")

    # Allocator vs static.
    add("## Allocator vs static equal split")
    add("")
    static = static_split(config)
    add(
        "Static baseline: "
        + ", ".join(f"{config.label(c)} {money(static[c])}" for c in names)
        + " every week, nothing ever paused. Both plans are scored on the same fixtures:"
        " each channel's budget times its realized pipeline per dollar that week."
    )
    add("")
    if scores:
        add("| Week | Allocator pipeline | Static pipeline | Lift |")
        add("|---|---:|---:|---:|")
        for s in scores:
            pct = s.lift / s.static * 100 if s.static else 0.0
            add(
                f"| {s.week.isoformat()} | {money(s.allocator)} | {money(s.static)}"
                f" | {_signed_money(s.lift)} ({pct:+.1f}%) |"
            )
        alloc_total = sum(s.allocator for s in scores)
        static_total = sum(s.static for s in scores)
        lift = alloc_total - static_total
        pct = lift / static_total * 100 if static_total else 0.0
        add(
            f"| **Backtest total** | **{money(alloc_total)}** | **{money(static_total)}**"
            f" | **{_signed_money(lift)} ({pct:+.1f}%)** |"
        )
        add("")
        losing = [s for s in scores if s.lift < 0]
        if losing:
            add(
                "Weeks the allocator lost: "
                + ", ".join(s.week.isoformat() for s in losing)
                + ". A lookback window trails reality, so the allocator pays a short lag"
                " when a channel turns, before the trend shows up in pooled efficiency."
            )
            add("")
    else:
        add("No cycle has a realized target week yet, so there is nothing to backtest.")
        add("")
    nxt = project_next(config, cycle)
    nxt_pct = nxt.lift / nxt.static * 100 if nxt.static else 0.0
    add(
        f"Projection for {nxt.week.isoformat()} (lookback efficiency, not realized):"
        f" allocator {money(nxt.allocator)} vs static {money(nxt.static)},"
        f" {_signed_money(nxt.lift)} ({nxt_pct:+.1f}%)."
    )
    add("")

    # Efficiency trend.
    add("## Efficiency trend (pipeline per dollar, weekly)")
    add("")
    add("| Channel | " + " | ".join(w.isoformat() for w in weeks) + " | Trend | Cost per qualified lead (last week) |")
    add("|---|" + "---:|" * len(weeks) + "---|---:|")
    for c in names:
        rows = {r.week: r for r in by_channel[c]}
        cells = [_cell(rows[w].pipeline_per_dollar) if w in rows else "n/a" for w in weeks]
        last = by_channel[c][-1] if by_channel[c] else None
        if last is None or last.spend == 0:
            cpl = "n/a"
        elif last.qualified_leads == 0:
            cpl = "no leads"
        else:
            cpl = money(last.spend / last.qualified_leads)
        add(
            f"| {config.label(c)} | " + " | ".join(cells)
            + f" | {_trend(by_channel[c], config.lookback_weeks)} | {cpl} |"
        )
    add("")
    add(
        f"Trend compares pooled efficiency over the last {config.lookback_weeks} weeks with the"
        f" {config.lookback_weeks} weeks before that."
    )
    add("")

    # Full decision log.
    add("## Decision log, every cycle")
    add("")
    for cy in cycles:
        add(f"### Cycle {cy.cycle_week.isoformat()}: budget for {cy.target_week.isoformat()}")
        add("")
        add("| Channel | Action | Previous | Proposed | Change | Reason |")
        add("|---|---|---:|---:|---:|---|")
        for d in cy.decisions:
            add(
                f"| {_label(config, d.channel)} | {d.action} | {money(d.previous)}"
                f" | {money(d.proposed)} | {_change(d)} | {d.reason} |"
            )
        add("")

    add("## How to read this")
    add("")
    add(
        "- Efficiency is pipeline dollars per spend dollar, pooled over the lookback window."
        " It is correlation, not incrementality: it cannot tell you what pipeline would have"
        " arrived anyway."
    )
    add(
        "- Projected pipeline assumes linear returns at each week's realized efficiency."
        " Real channels saturate, so treat the lift as directional."
    )
    add(
        f"- Guardrail priority: kill rule, then learning minimum, then the {config.max_swing_pct:g}%"
        " swing limit, then the budget cap. Budget the guardrails will not let any channel absorb"
        " is held as reserve, never forced."
    )
    add("")
    return "\n".join(out)


def render_summary(config: Config, cycle: Cycle) -> str:
    """A few lines for the terminal after `allocate`."""
    lines = [f"budget for week of {cycle.target_week.isoformat()} ({config.run_name}):"]
    for c in config.channel_names:
        status = " (paused)" if c in cycle.paused else ""
        lines.append(f"  {config.label(c)}: {money(cycle.allocations[c])}{status}")
    if cycle.reserve > 0:
        lines.append(f"  Unallocated reserve: {money(cycle.reserve)}")
    return "\n".join(lines) + "\n"

