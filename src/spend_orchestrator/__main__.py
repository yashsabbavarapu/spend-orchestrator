"""CLI: ingest weekly funnel performance, then allocate next week's budget with guardrails."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from spend_orchestrator.allocate import AllocationError, run_cycles
from spend_orchestrator.backtest import backtest
from spend_orchestrator.config import ConfigError, load_config
from spend_orchestrator.ingest import IngestError
from spend_orchestrator.report import render_report, render_summary
from spend_orchestrator.sources import CsvSource, Source
from spend_orchestrator.storage import Store

DEFAULT_DB = "spend_orchestrator.db"


def cmd_ingest(args: argparse.Namespace) -> int:
    source: Source = CsvSource(Path(args.performance))
    rows = source.performance()
    store = Store(Path(args.db))
    try:
        store.save_performance(rows)
        counts = store.counts()
    finally:
        store.close()
    print(
        f"ingested {len(rows)} channel-weeks into {args.db};"
        f" store now holds {counts['performance']} channel-weeks"
    )
    return 0


def cmd_allocate(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    store = Store(Path(args.db))
    try:
        performance = store.performance()
        if not performance:
            print(f"error: no performance data in {args.db}; run ingest first", file=sys.stderr)
            return 1
        cycles = run_cycles(config, performance)
        store.replace_allocations(config.run_name, cycles)
    finally:
        store.close()
    text = render_report(config, cycles, performance, backtest(config, cycles, performance))
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        sys.stdout.write(render_summary(config, cycles[-1]))
        print(f"wrote {len(cycles)} cycles of decisions to {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spend-orchestrator",
        description="Guardrailed budget allocator: every move carries its reason",
    )
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite file (default {DEFAULT_DB})")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="load weekly funnel performance from CSV (idempotent)")
    ingest.add_argument("--performance", required=True, help="CSV of week, channel, spend, ...")
    ingest.set_defaults(func=cmd_ingest)

    allocate = sub.add_parser("allocate", help="propose next week's budget split with reasons")
    allocate.add_argument("--config", required=True, help="YAML run configuration")
    allocate.add_argument("--output", help="write the Markdown report here (default: stdout)")
    allocate.set_defaults(func=cmd_allocate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code: int = args.func(args)
    except (ConfigError, IngestError, AllocationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
