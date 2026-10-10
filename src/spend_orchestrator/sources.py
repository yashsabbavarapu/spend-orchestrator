"""The ingest boundary. v1 ships the CSV source; ad-platform and attribution sources are v2 stubs.

Anything that can produce WeekPerformance rows plugs in here, and storage,
the allocator, and the report never change.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from spend_orchestrator.ingest import read_performance
from spend_orchestrator.models import WeekPerformance


class Source(Protocol):
    def performance(self) -> list[WeekPerformance]: ...


class CsvSource:
    def __init__(self, path: Path) -> None:
        self._path = path

    def performance(self) -> list[WeekPerformance]:
        return read_performance(self._path)


class GoogleAdsSource:
    """v2: weekly campaign spend, impressions, and clicks from the Google Ads API."""

    def performance(self) -> list[WeekPerformance]:
        raise NotImplementedError("Google Ads ingestion is on the v2 roadmap")


class MetaAdsSource:
    """v2: weekly ad set spend and delivery from the Meta Marketing API."""

    def performance(self) -> list[WeekPerformance]:
        raise NotImplementedError("Meta Ads ingestion is on the v2 roadmap")


class LinkedInAdsSource:
    """v2: weekly campaign group spend and delivery from the LinkedIn Marketing API."""

    def performance(self) -> list[WeekPerformance]:
        raise NotImplementedError("LinkedIn Ads ingestion is on the v2 roadmap")


class PipelineAttributionSource:
    """v2: pipeline per channel from a pipeline-attribution SQLite store instead of
    platform-reported pipeline, so the allocator spends on CRM-measured credit."""

    def __init__(self, db: Path, model: str = "position") -> None:
        self._db = db
        self._model = model

    def performance(self) -> list[WeekPerformance]:
        raise NotImplementedError("pipeline-attribution integration is on the v2 roadmap")
