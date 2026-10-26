import json

from decision_agent.config import Settings
from decision_agent.llm.mock_provider import MockProvider
from decision_agent.models import ConsistencyLabel, ConsistencyResult
from decision_agent.score import consistency_score
from decision_agent.uncertainty import estimate_uncertainty


def result(label):
    return ConsistencyResult(
        artifact_id="x", label=label, confidence=1, evidence="e", expected_action="a"
    )


def test_score_edges():
    assert consistency_score([]) == 100
    assert consistency_score([result(ConsistencyLabel.UNCERTAIN)]) == 50
    assert consistency_score([result(ConsistencyLabel.CONSISTENT)] * 2) == 100
    assert (
        consistency_score(
            [result(ConsistencyLabel.CONSISTENT), result(ConsistencyLabel.INCONSISTENT)]
        )
        == 50
    )


class VarianceProvider(MockProvider):
    """Returns CONSISTENT for the first samples and INCONSISTENT afterwards."""

    def __init__(self, consistent_samples):
        super().__init__()
        self.consistent_samples = consistent_samples
        self.sample_count = 0

    def _consistency(self, user, temperature):
        self.sample_count += 1
        label = "CONSISTENT" if self.sample_count <= self.consistent_samples else "INCONSISTENT"
        return json.dumps({"label": label, "evidence": "e", "expected_action": "a"})


def test_agreement_threshold_boundary(demo):
    _, project = demo
    artifact = next(a for a in project.artifacts if a.id == "TASK_02")
    settings = Settings(n=5, tau_conf=0.8, adaptive_min_samples=0)
    # 4 of 5 agree -> agreement 0.8, not below tau_conf, so the label stays.
    four = estimate_uncertainty(
        project.store, "email_auth", artifact, "2026-12-31", VarianceProvider(4), settings
    )
    assert four.label == ConsistencyLabel.CONSISTENT
    assert four.confidence == 0.8
    # 3 of 5 agree -> agreement 0.6 < tau_conf, so the result becomes UNCERTAIN.
    three = estimate_uncertainty(
        project.store, "email_auth", artifact, "2026-12-31", VarianceProvider(3), settings
    )
    assert three.label == ConsistencyLabel.UNCERTAIN
    assert three.confidence == 0.6
