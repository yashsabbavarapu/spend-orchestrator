"""CSV ingest, idempotent SQLite storage, and the CLI end to end."""
from __future__ import annotations

import io
import shutil
from datetime import date
from pathlib import Path

import pytest

from conftest import DEMO_CONFIG, FIXTURE
from spend_orchestrator.__main__ import main
from spend_orchestrator.ingest import IngestError, read_performance
from spend_orchestrator.storage import Store

HEADER = "week,channel,spend,impressions,clicks,qualified_leads,pipeline_dollars\n"


def test_fixture_shape(demo_perf: list[object]) -> None:
    perf = read_performance(FIXTURE)
    assert len(perf) == 24  # 3 channels x 8 weeks
    assert {p.channel for p in perf} == {"linkedin_abm", "paid_search", "niche_community"}
    first = perf[0]
    assert first.week == date(2026, 8, 3)


def test_malformed_rows_are_skipped_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "f.csv"
    path.write_text(
        HEADER
        + "2026-01-05,Paid Search,100,10,1,1,300\n"
        + "not-a-date,paid_search,100,10,1,1,300\n"
        + "2026-01-12,paid_search,-5,10,1,1,300\n"
        + "2026-01-12,paid_search,abc,10,1,1,300\n",
        encoding="utf-8",
    )
    warn = io.StringIO()
    rows = read_performance(path, warn=warn)
    assert len(rows) == 1 and rows[0].channel == "paid_search"
    assert rows[0].pipeline_per_dollar == pytest.approx(3.0)
    assert warn.getvalue().count("skipped malformed row") == 3


def test_duplicate_rows_keep_the_last(tmp_path: Path) -> None:
    path = tmp_path / "f.csv"
    path.write_text(
        HEADER + "2026-01-05,a,100,10,1,1,300\n" + "2026-01-05,a,100,10,1,1,500\n", encoding="utf-8"
    )
    warn = io.StringIO()
    rows = read_performance(path, warn=warn)
    assert len(rows) == 1 and rows[0].pipeline == 500
    assert "duplicate" in warn.getvalue()


def test_missing_columns_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "f.csv"
    path.write_text("week,channel,spend\n2026-01-05,a,1\n", encoding="utf-8")
    with pytest.raises(IngestError, match="missing columns"):
        read_performance(path)


def test_reingest_is_idempotent(tmp_path: Path) -> None:
    store = Store(tmp_path / "db.sqlite")
    rows = read_performance(FIXTURE)
    store.save_performance(rows)
    store.save_performance(rows)
    assert store.counts()["performance"] == 24
    assert store.performance() == rows
    store.close()


def test_cli_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = str(tmp_path / "so.db")
    out = tmp_path / "allocations.md"
    assert main(["--db", db, "ingest", "--performance", str(FIXTURE)]) == 0
    assert main(["--db", db, "ingest", "--performance", str(FIXTURE)]) == 0  # idempotent
    assert "store now holds 24 channel-weeks" in capsys.readouterr().out
    args = ["--db", db, "allocate", "--config", str(DEMO_CONFIG), "--output", str(out)]
    assert main(args) == 0
    summary = capsys.readouterr().out
    assert "Niche community sponsorship: $0 (paused)" in summary
    report = out.read_text(encoding="utf-8")
    for heading in (
        "## Next week's budget",
        "## This cycle's moves",
        "## Paused channels",
        "## Allocator vs static equal split",
        "## Efficiency trend",
        "## Decision log, every cycle",
    ):
        assert heading in report
    assert chr(0x2014) not in report  # house style: no em dashes

    # Allocating twice gives byte-identical output and does not grow the table.
    store = Store(Path(db))
    first_count = store.counts()["allocations"]
    decisions = store.decisions()
    store.close()
    assert decisions and all(d.reason for d in decisions)
    assert main(args) == 0
    assert out.read_text(encoding="utf-8") == report
    store = Store(Path(db))
    assert store.counts()["allocations"] == first_count
    store.close()


def test_cli_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = str(tmp_path / "empty.db")
    assert main(["--db", db, "allocate", "--config", str(DEMO_CONFIG)]) == 1
    assert "run ingest first" in capsys.readouterr().err
    assert main(["--db", db, "ingest", "--performance", str(tmp_path / "missing.csv")]) == 1
    assert main(["--db", db, "allocate", "--config", str(tmp_path / "missing.yaml")]) == 1


def test_cli_runs_from_a_copied_workspace(tmp_path: Path) -> None:
    # The demo needs nothing but the CSV and the YAML.
    shutil.copy(FIXTURE, tmp_path / "funnel.csv")
    shutil.copy(DEMO_CONFIG, tmp_path / "config.yaml")
    db = str(tmp_path / "x.db")
    assert main(["--db", db, "ingest", "--performance", str(tmp_path / "funnel.csv")]) == 0
    assert main(["--db", db, "allocate", "--config", str(tmp_path / "config.yaml"), "--output", str(tmp_path / "a.md")]) == 0


def test_v2_sources_are_explicit_stubs(tmp_path: Path) -> None:
    from spend_orchestrator.sources import (
        CsvSource,
        GoogleAdsSource,
        LinkedInAdsSource,
        MetaAdsSource,
        PipelineAttributionSource,
        Source,
    )

    csv_source: Source = CsvSource(FIXTURE)
    assert len(csv_source.performance()) == 24
    stubs: list[Source] = [
        GoogleAdsSource(),
        MetaAdsSource(),
        LinkedInAdsSource(),
        PipelineAttributionSource(tmp_path / "attr.db"),
    ]
    for stub in stubs:
        with pytest.raises(NotImplementedError, match="v2 roadmap"):
            stub.performance()
