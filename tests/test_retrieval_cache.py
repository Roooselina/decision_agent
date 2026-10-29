"""The cached TF-IDF gives the same similarities and the same ranking as refitting each time."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from decision_agent.config import DEFAULT_SETTINGS
from decision_agent.ranking import stable_order
from decision_agent.retrieval import clear_cache, tfidf_similarities

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import make_demo_capstone as demo


def old_similarities(texts: list[str], query: str) -> np.ndarray:
    matrix = TfidfVectorizer(
        analyzer=DEFAULT_SETTINGS.tfidf_analyzer,
        ngram_range=(DEFAULT_SETTINGS.tfidf_ngram_min, DEFAULT_SETTINGS.tfidf_ngram_max),
    ).fit_transform(texts + [query])
    return cosine_similarity(matrix[-1], matrix[:-1]).ravel()


@pytest.mark.parametrize("variant", ["capstone", "full"])
def test_same_numbers_and_same_order_as_refitting(tmp_path, variant):
    root = tmp_path / variant
    demo.write_folder(variant, root, pdf=False)
    built = demo.build_folder(root)
    texts = [a.text for a in built.artifacts]
    ids = [a.id for a in built.artifacts]
    clear_cache()
    for item in built.meetings[::3]:
        old = old_similarities(texts, item.text)
        new = tfidf_similarities(texts, item.text)
        assert np.max(np.abs(old - new)) < 1e-12
        assert stable_order(old, ids) == stable_order(new, ids)


def test_edge_cases_match_too():
    texts = ["로그인은 SSO", "", "a", "사진 최대 5장"]
    for query in ("", "x", "사진", "전혀 다른 문장"):
        assert np.allclose(tfidf_similarities(texts, query), old_similarities(texts, query) if query.strip() else 0)
    assert tfidf_similarities([], "q").size == 0
    assert np.all(tfidf_similarities(["", " "], "q") == 0)
