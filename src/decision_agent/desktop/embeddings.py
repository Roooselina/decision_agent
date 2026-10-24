from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable

import numpy as np

from decision_agent.connectors.http import Http, HttpError, UrllibHttp
from decision_agent.desktop.state import StateDB
from decision_agent.models import Artifact

log = logging.getLogger(__name__)

BATCH = 32
MAX_EXTRA = 8
MIN_SIMILARITY = 0.45


class OllamaEmbedder:
    """Sentence vectors from an Ollama embedding model, cached in the state database."""

    def __init__(self, base_url: str, model: str, db: StateDB, http: Http | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.db = db
        self.http = http or UrllibHttp()

    def _key(self, text: str) -> str:
        return "emb:" + hashlib.sha256(f"{self.model}\x00{text}".encode()).hexdigest()

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: dict[int, list[float]] = {}
        missing: list[int] = []
        for index, text in enumerate(texts):
            cached = self.db.cache_get(self._key(text))
            if cached is not None:
                vectors[index] = json.loads(cached)
            else:
                missing.append(index)
        for start in range(0, len(missing), BATCH):
            chunk = missing[start : start + BATCH]
            response = self.http.request(
                "POST",
                f"{self.base_url}/api/embed",
                json_body={"model": self.model, "input": [texts[i] for i in chunk]},
                timeout=300.0,
            )
            if not response.ok:
                raise HttpError(f"임베딩 서버 오류 ({response.status})")
            result = response.json().get("embeddings") or []
            if len(result) != len(chunk):
                raise HttpError("임베딩 응답 개수가 맞지 않습니다.")
            for index, vector in zip(chunk, result, strict=True):
                vectors[index] = vector
                self.db.cache_put(self._key(texts[index]), json.dumps(vector))
        return [vectors[i] for i in range(len(texts))]


def make_extra_ranker(embedder: OllamaEmbedder) -> Callable[[str, list[Artifact]], list[Artifact]]:
    """Adds artifacts that mean the same thing as the decision but share few characters with it.

    This only supplements the character TF-IDF candidates; if the embedding server fails the
    analysis continues with TF-IDF alone.
    """

    def rank(query: str, artifacts: list[Artifact]) -> list[Artifact]:
        if not artifacts:
            return []
        try:
            vectors = np.array(embedder.embed([query] + [a.text for a in artifacts]), dtype=float)
        except (HttpError, ValueError, KeyError, TypeError) as exc:
            log.warning("embedding retrieval skipped: %s", exc)
            return []
        norms = np.linalg.norm(vectors, axis=1)
        norms[norms == 0] = 1.0
        unit = vectors / norms[:, None]
        sims = unit[1:] @ unit[0]
        order = np.argsort(-sims)
        return [artifacts[i] for i in order[:MAX_EXTRA] if sims[i] >= MIN_SIMILARITY]

    return rank
