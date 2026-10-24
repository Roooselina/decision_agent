from pathlib import Path

import pytest

from decision_agent.llm.mock_provider import MockProvider
from decision_agent.workflow import ingest_project

DEMO_DIR = Path(__file__).resolve().parents[1] / "data" / "demo"


@pytest.fixture(scope="session")
def demo():
    """Ingested demo project plus the MockProvider that produced it."""
    provider = MockProvider()
    return provider, ingest_project(DEMO_DIR, provider)


@pytest.fixture(autouse=True)
def _isolated_app_home(tmp_path_factory, monkeypatch):
    """Keep settings, state, and logs of the desktop program out of the real user profile."""
    monkeypatch.setenv("DECISION_AGENT_HOME", str(tmp_path_factory.mktemp("app_home")))
