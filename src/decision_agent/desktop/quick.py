from __future__ import annotations

import json
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from decision_agent.config import DEFAULT_SETTINGS
from decision_agent.ranking import stable_order


def _vectorizer() -> TfidfVectorizer:
    return TfidfVectorizer(
        analyzer=DEFAULT_SETTINGS.tfidf_analyzer,
        ngram_range=(DEFAULT_SETTINGS.tfidf_ngram_min, DEFAULT_SETTINGS.tfidf_ngram_max),
    )


def rank_similar(query: str, texts: list[str], limit: int = 8, minimum: float = 0.08) -> list[tuple[int, float]]:
    """Indexes of texts most similar to the query by character n-grams. No model is called."""
    if not query.strip() or not texts:
        return []
    matrix = _vectorizer().fit_transform(texts + [query])
    sims = cosine_similarity(matrix[-1], matrix[:-1]).ravel()
    order = stable_order(sims, [f"{i:08d}" for i in range(len(texts))])[:limit]
    return [(int(i), float(sims[i])) for i in order if sims[i] >= minimum]


def preview(text: str, artifacts: list[dict], decisions: list[dict]) -> dict:
    """What a new decision sentence would probably touch, without saving or judging anything."""
    related = rank_similar(text, [a["text"] for a in artifacts])
    similar_decisions = rank_similar(text, [d["text"] for d in decisions], limit=3, minimum=0.15)
    return {
        "artifacts": [{**artifacts[i], "score": round(score, 2)} for i, score in related],
        "decisions": [{**decisions[i], "score": round(score, 2)} for i, score in similar_decisions],
    }


def search(query: str, findings: list[dict], topics: list[dict], files: list[str], notes: list[dict]) -> dict:
    """Plain substring search for looking something up in the middle of a meeting."""
    terms = [t for t in re.split(r"\s+", query.strip().lower()) if t]
    if not terms:
        return {"findings": [], "decisions": [], "files": [], "notes": []}

    def hit(*values: str) -> bool:
        blob = " ".join(values).lower()
        return all(t in blob for t in terms)

    return {
        "findings": [
            f for f in findings if hit(f["text"], f.get("evidence", ""), f.get("source_doc", ""), f.get("owner", ""))
        ][:30],
        "decisions": [t for t in topics if hit(t["latest"], t["topic"])][:20],
        "files": [p for p in files if hit(p)][:30],
        "notes": [n for n in notes if hit(n["text"])][:20],
    }


def topic_rows(raw_rows: list[dict]) -> list[dict]:
    out = []
    for row in raw_rows:
        try:
            summary = json.loads(row["summary"])
        except (ValueError, TypeError):
            summary = {}
        out.append(
            {
                "topic": row["topic"],
                "text": summary.get("latest", ""),
                "latest": summary.get("latest", ""),
                "date": summary.get("date", ""),
                "judge": row.get("judge", ""),
            }
        )
    return out
