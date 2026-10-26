from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from decision_agent.extraction import extract_decisions
from decision_agent.llm.base import LLMProvider
from decision_agent.models import Artifact, Decision, DecisionRelation, MeetingItem, ProjectConfig
from decision_agent.parsing.loader import load_project
from decision_agent.relations import classify_and_store
from decision_agent.storage import SQLiteStore


@dataclass
class IngestedProject:
    """Result of the ingest stage that Agent and Pipeline share."""

    store: SQLiteStore
    meetings: list[MeetingItem]
    artifacts: list[Artifact]
    config: ProjectConfig
    aliases: dict[str, Any]
    decisions: list[Decision] = field(default_factory=list)
    relations: list[DecisionRelation] = field(default_factory=list)


def ingest_items(
    meetings: list[MeetingItem],
    artifacts: list[Artifact],
    config: ProjectConfig,
    aliases: dict[str, Any],
    provider: LLMProvider,
    on_progress: Callable[[int, int], None] | None = None,
) -> IngestedProject:
    """Extract decisions, classify relations, and store everything in a fresh store.

    This stage is identical for the Agent and the fixed Pipeline, so any difference between
    them comes from orchestration after ingestion.
    """
    store = SQLiteStore()
    decisions = extract_decisions(meetings, provider, config, aliases, on_progress=on_progress)
    for decision in decisions:
        store.save_decision(decision)
    for artifact in artifacts:
        store.save_artifact(artifact)
    relations = classify_and_store(decisions, provider, store)
    return IngestedProject(
        store=store,
        meetings=meetings,
        artifacts=artifacts,
        config=config,
        aliases=aliases,
        decisions=decisions,
        relations=relations,
    )


def ingest_project(project_dir: str | Path, provider: LLMProvider) -> IngestedProject:
    """Parse the fixed-layout project folder, then run the shared ingest stage."""
    meetings, artifacts, cfg = load_project(project_dir)
    return ingest_items(meetings, artifacts, cfg["project"], cfg["aliases"], provider)
