"""The allocator: one budget split per cycle, every move explained.

Each cycle looks at the data up to and including `cycle_week` and proposes the
budget for the following week. Guardrails apply in a fixed priority order:

1. Kill rule. A live channel whose weekly pipeline-per-dollar sat below the
   kill threshold for `kill_after_weeks` consecutive reported weeks is paused
   at $0. This overrides the swing limit and the learning minimum, and a
   paused channel stays paused: resuming is a human call.
2. Learning minimum. Every live channel keeps at least its configured floor.
3. Swing limit. No live channel moves more than `max_swing_pct` up or down
   from last cycle's budget. Where the floor and the swing limit disagree, the
   floor wins.
4. Budget conservation. Spend never exceeds `total_budget`. Budget that live
   channels cannot absorb without breaking the swing limit is held as an
   explicit, logged reserve rather than forced onto a channel.

Inside those bounds, budget follows lookback pipeline-per-dollar: each live
channel gets `k * efficiency`, clamped to its bounds, with the single scalar
`k` solved exactly so the clamped total equals the budget.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

from spend_orchestrator.config import Config
from spend_orchestrator.models import (
    DECREASE,
    HOLD,
    INCREASE,
    PAUSE,
    PAUSED,
    RESERVE,
    RESERVE_CHANNEL,
    Cycle,
    Decision,
    WeekPerformance,
)

_CENT = 0.005


class AllocationError(ValueError):
    pass


def money(x: float) -> str:
    return f"${x:,.0f}"


def ratio(x: float) -> str:
    return f"${x:,.2f}"


def window_efficiency(rows: Iterable[WeekPerformance]) -> float | None:
    """Pipeline-per-dollar pooled over a window: sum(pipeline) / sum(spend)."""
    rows = list(rows)
    spend = sum(r.spend for r in rows)
    return sum(r.pipeline for r in rows) / spend if spend > 0 else None


def kill_streak(
    config: Config, history: list[WeekPerformance]
) -> list[WeekPerformance] | None:
    """The last `kill_after_weeks` reported weeks if every one is below threshold, else None.

    `history` is one channel's rows up to the cycle week, oldest first. A week
    with zero spend has no efficiency reading, so it breaks the streak.
    """
    recent = history[-config.kill_after_weeks :]
    if len(recent) < config.kill_after_weeks:
        return None
    for row in recent:
        ppd = row.pipeline_per_dollar
        if ppd is None or ppd >= config.kill_threshold:
            return None
    return recent


def solve_fill(
    total: float, weights: dict[str, float], lo: dict[str, float], hi: dict[str, float]
) -> dict[str, float]:
    """Find x_c = clamp(k * w_c, lo_c, hi_c) with sum(x) as close to `total` as bounds allow.

    sum(x) is nondecreasing and piecewise linear in k, with breakpoints at
    lo/w and hi/w, so k is found exactly by locating the segment that crosses
    `total` and interpolating. No iteration, no tolerance, fully deterministic.
    If even sum(hi) < total, every channel sits at its upper bound and the
    caller books the rest as reserve.
    """
    names = sorted(weights)

    def at(k: float) -> dict[str, float]:
        return {c: min(max(k * weights[c], lo[c]), hi[c]) for c in names}

    def f(k: float) -> float:
        return sum(at(k).values())

    points = sorted(
        {0.0}
        | {lo[c] / weights[c] for c in names if weights[c] > 0}
        | {hi[c] / weights[c] for c in names if weights[c] > 0}
    )
    if f(points[0]) >= total:
        return at(points[0])
    prev_k, prev_f = points[0], f(points[0])
    for k in points[1:]:
        fk = f(k)
        if fk >= total:
            k_star = prev_k + (total - prev_f) * (k - prev_k) / (fk - prev_f)
            return at(k_star)
        prev_k, prev_f = k, fk
    return at(points[-1])


def _pct(x: float) -> str:
    return f"{x:+.1f}%"


def _move_sentence(prev: float, new: float) -> tuple[str, str]:
    delta = round(new - prev, 2)
    if abs(delta) < 1:
        return HOLD, f"Held at {money(new)}."
    verb, action = ("Increased", INCREASE) if delta > 0 else ("Cut", DECREASE)
    pct = f" ({_pct(delta / prev * 100)})" if prev > 0 else ""
    return action, f"{verb} {money(abs(delta))}{pct} from {money(prev)} to {money(new)}."


def plan_cycle(
    config: Config,
    history: dict[str, list[WeekPerformance]],
    window: list[date],
    previous: dict[str, float],
    previous_reserve: float,
    paused_since: dict[str, date],
) -> Cycle:
    """Plan one cycle. `history` is each channel's rows up to the cycle week, oldest first.

    `paused_since` is updated in place with any channel this cycle pauses.
    """
    cycle_week = window[-1]
    target_week = cycle_week + timedelta(days=7)
    total = config.total_budget
    swing = config.max_swing_pct / 100
    names = config.channel_names
    in_window = set(window)
    span = (
        f"the last {len(window)} weeks ({window[0].isoformat()} to {cycle_week.isoformat()})"
        if len(window) > 1
        else f"the week of {cycle_week.isoformat()}"
    )

    eff = {c: window_efficiency(r for r in history[c] if r.week in in_window) for c in names}
    decisions: list[Decision] = []

    def log(channel: str, action: str, prev: float, new: float, reason: str) -> None:
        decisions.append(
            Decision(cycle_week, target_week, channel, action, prev, new, eff.get(channel), reason)
        )

    # 1. Kill rule, then the channels already paused in earlier cycles.
    for c in names:
        if c in paused_since:
            log(
                c,
                PAUSED,
                previous[c],
                0.0,
                f"Remains paused since the {paused_since[c].isoformat()} cycle (kill rule)."
                " v1 never auto-resumes a killed channel: a human decides when to retest it.",
            )
            continue
        streak = kill_streak(config, history[c])
        if streak is None:
            continue
        paused_since[c] = cycle_week
        weeks = ", ".join(
            f"{r.week.isoformat()}: {ratio(r.pipeline_per_dollar or 0.0)}" for r in streak
        )
        log(
            c,
            PAUSE,
            previous[c],
            0.0,
            f"Paused, cut {money(previous[c])} to $0. Weekly pipeline-per-dollar was below the"
            f" {ratio(config.kill_threshold)} kill threshold for {config.kill_after_weeks}"
            f" consecutive weeks ({weeks}). Its budget returns to the pool for live channels."
            " The kill rule overrides the swing limit and the learning minimum.",
        )

    live = [c for c in names if c not in paused_since]

    # 2 and 3. Bounds from the learning minimum and the swing limit.
    floor = {c: config.channel(c).min_learning_budget for c in live}
    lo: dict[str, float] = {}
    hi: dict[str, float] = {}
    weights: dict[str, float] = {}
    for c in live:
        e = eff[c]
        if e is None:  # no read: hold flat (but never below the floor)
            lo[c] = hi[c] = max(floor[c], previous[c])
            weights[c] = 0.0
        else:
            lo[c] = max(floor[c], previous[c] * (1 - swing))
            hi[c] = max(floor[c], previous[c] * (1 + swing))
            weights[c] = e
    relaxed = sum(lo.values()) > total + _CENT
    if relaxed:  # swing floors would overspend: fall back to learning minimums
        for c in live:
            lo[c] = floor[c]
            hi[c] = max(hi[c], floor[c])

    # 4. Efficiency-weighted fill inside the bounds; whatever cannot be placed is reserve.
    alloc = {c: round(v, 2) for c, v in solve_fill(total, weights, lo, hi).items()} if live else {}
    residue = round(total - sum(alloc.values()), 2)
    placed_all = sum(hi.values()) >= total - _CENT
    if alloc and placed_all and residue != 0 and abs(residue) < 1:
        # Cent-level rounding residue goes to the largest allocation (ties by name).
        top = max(sorted(alloc), key=lambda c: alloc[c])
        alloc[top] = round(alloc[top] + residue, 2)
        residue = 0.0
    reserve = max(residue, 0.0)

    # Plain-language reasons for every live channel.
    read = [c for c in live if eff[c] is not None]
    window_rows = [r for c in read for r in history[c] if r.week in in_window]
    blended = window_efficiency(window_rows)
    held_flat = sum(alloc[c] for c in live if eff[c] is None)
    weight_sum = sum(weights[c] for c in read)
    for c in live:
        prev, new, e = previous[c], alloc[c], eff[c]
        action, move = _move_sentence(prev, new)
        if e is None:
            log(
                c,
                action,
                prev,
                new,
                f"{move} No spend in {span}, so there is no efficiency read; held flat"
                " (never below its learning minimum) until data arrives.",
            )
            continue
        share = (total - held_flat) * weights[c] / weight_sum if weight_sum > 0 else 0.0
        blend = f", vs {ratio(blended)} blended across live channels" if blended is not None else ""
        evidence = f"Pipeline-per-dollar over {span} was {ratio(e)}{blend}."
        swing_txt = f"{config.max_swing_pct:g}% swing limit"
        if abs(new - floor[c]) < 1 and share < new - 1:
            why = (
                f"Held at its {money(floor[c])} learning minimum so it keeps enough volume to"
                f" learn; efficiency alone pointed to {money(share)}."
            )
        elif abs(new - hi[c]) < 1 and share > new + 1:
            why = f"Increase capped by the {swing_txt}; efficiency alone pointed to {money(share)}."
        elif abs(new - lo[c]) < 1 and share < new - 1:
            why = f"Cut capped by the {swing_txt}; efficiency alone pointed to {money(share)}."
        elif new > share + 1:
            why = (
                f"Above its pure efficiency share of {money(share)} because guardrails cap"
                " how much other channels can take or lose this cycle."
            )
        elif new < share - 1:
            why = (
                f"Below its pure efficiency share of {money(share)} because guardrails hold"
                " other channels up (learning minimums or swing limits on cuts)."
            )
        else:
            why = "Set to its efficiency-weighted share of the budget."
        if relaxed:
            why += (
                " Swing floors this cycle would have overspent the budget, so cuts were allowed"
                " down to learning minimums."
            )
        log(c, action, prev, new, f"{move} {evidence} {why}")

    if reserve > 0 or previous_reserve > 0:
        if reserve > 0:
            if live:
                why = (
                    f"Held {money(reserve)} unallocated: live channels cannot absorb it this"
                    f" cycle without breaking the {config.max_swing_pct:g}% swing limit. Swing"
                    " room compounds each cycle, so the reserve drains as channels prove out."
                )
            else:
                why = f"Held {money(reserve)} unallocated: every channel is paused."
        else:
            why = f"Reserve of {money(previous_reserve)} fully released to live channels."
        decisions.append(
            Decision(
                cycle_week, target_week, RESERVE_CHANNEL, RESERVE, previous_reserve, reserve, None, why
            )
        )

    allocations = {c: (alloc[c] if c in alloc else 0.0) for c in names}
    return Cycle(
        cycle_week=cycle_week,
        target_week=target_week,
        allocations=allocations,
        reserve=reserve,
        decisions=tuple(decisions),
        paused=frozenset(paused_since),
        efficiencies=eff,
    )


def run_cycles(config: Config, performance: list[WeekPerformance]) -> list[Cycle]:
    """Replay every cycle the data supports, oldest first. The last one is "this cycle".

    The first cycle starts from the configured starting budgets (equal split by
    default) and runs once `lookback_weeks` of data exist. Each later cycle
    starts from the previous cycle's proposal. Channels in the data that the
    config does not list are ignored.
    """
    names = set(config.channel_names)
    rows = sorted((r for r in performance if r.channel in names), key=lambda r: (r.week, r.channel))
    weeks = sorted({r.week for r in rows})
    if len(weeks) < config.lookback_weeks:
        raise AllocationError(
            f"need at least {config.lookback_weeks} weeks of performance data"
            f" (lookback_weeks), found {len(weeks)}"
        )
    previous = config.starting_allocation()
    reserve = round(max(config.total_budget - sum(previous.values()), 0.0), 2)
    paused_since: dict[str, date] = {}
    cycles: list[Cycle] = []
    for i in range(config.lookback_weeks - 1, len(weeks)):
        upto = weeks[i]
        history = {c: [r for r in rows if r.channel == c and r.week <= upto] for c in config.channel_names}
        window = weeks[i - config.lookback_weeks + 1 : i + 1]
        cycle = plan_cycle(config, history, window, previous, reserve, paused_since)
        cycles.append(cycle)
        previous, reserve = dict(cycle.allocations), cycle.reserve
    return cycles
