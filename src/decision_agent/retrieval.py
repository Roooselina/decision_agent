"""Character TF-IDF similarity of one query against a list of texts, with cached counting.

tfidf_similarities(texts, query) returns the same cosine similarities as
cosine_similarity of TfidfVectorizer().fit_transform(texts + [query]) (smooth idf, l2 norm,
the query counted as a document), but the slow part, cutting every text into character
n-grams, is done once per list of texts and kept in a small cache. Each query then only
recomputes the idf, which depends on the query, so the ranking is the same as before.
"""

from __future__ import annotations

import hashlib
import threading
from collections import Counter, OrderedDict

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import CountVectorizer

from decision_agent.config import DEFAULT_SETTINGS

# Lists of texts kept counted (one per distinct list; a check uses one or two).
CACHE_SIZE = 8
_cache: OrderedDict[str, tuple] = OrderedDict()
_lock = threading.Lock()


def _counter() -> CountVectorizer:
    return CountVectorizer(
        analyzer=DEFAULT_SETTINGS.tfidf_analyzer,
        ngram_range=(DEFAULT_SETTINGS.tfidf_ngram_min, DEFAULT_SETTINGS.tfidf_ngram_max),
    )


def _counted(texts: list[str]) -> tuple:
    key = hashlib.sha1("\x1f".join(texts).encode("utf-8")).hexdigest()
    with _lock:
        found = _cache.get(key)
        if found is not None:
            _cache.move_to_end(key)
            return found
    vectorizer = _counter()
    try:
        counts = vectorizer.fit_transform(texts).tocsr()
    except ValueError:
        # Every text is empty or too short for the n-grams.
        counts = None
    entry = (vectorizer, counts, None if counts is None else np.bincount(counts.indices, minlength=counts.shape[1]))
    with _lock:
        _cache[key] = entry
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return entry


def tfidf_similarities(texts: list[str], query: str) -> np.ndarray:
    """Cosine similarity of the query to each text, as fit_transform(texts + [query]) gives it."""
    if not texts:
        return np.zeros(0)
    vectorizer, counts, df = _counted(texts)
    if counts is None:
        return np.zeros(len(texts))
    terms = Counter(vectorizer.build_analyzer()(query))
    vocab = vectorizer.vocabulary_
    q = np.zeros(counts.shape[1])
    extra = []
    for term, n in terms.items():
        index = vocab.get(term)
        if index is None:
            extra.append(n)
        else:
            q[index] = n
    n_docs = counts.shape[0] + 1
    idf = np.log((1 + n_docs) / (1 + df + (q > 0))) + 1.0
    weighted = counts.multiply(idf).tocsr()
    norms = np.sqrt(np.asarray(weighted.multiply(weighted).sum(axis=1)).ravel())
    norms[norms == 0] = 1.0
    qw = q * idf
    extra_idf = np.log((1 + n_docs) / 2.0) + 1.0
    q_norm = np.sqrt(float(qw @ qw) + sum((c * extra_idf) ** 2 for c in extra))
    if q_norm == 0:
        return np.zeros(len(texts))
    return np.asarray(weighted @ sparse.csr_matrix(qw).T.toarray()).ravel() / norms / q_norm


def clear_cache() -> None:
    with _lock:
        _cache.clear()
