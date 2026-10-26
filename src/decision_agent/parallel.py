from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from decision_agent.config import DEFAULT_SETTINGS

T = TypeVar("T")
R = TypeVar("R")


def run_parallel(
    function: Callable[[T], R], items: Sequence[T], workers: int | None = None
) -> list[R]:
    """Apply function to every item on a thread pool and return results in input order.

    Only network-bound work (LLM calls) should run here. Callers read shared state such as the
    SQLite store before calling and write it after, in the calling thread. The first exception is
    re-raised after pending work is cancelled, so a budget error stops the whole batch.
    """
    count = workers if workers is not None else DEFAULT_SETTINGS.concurrency
    if count <= 1 or len(items) <= 1:
        return [function(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(count, len(items)), thread_name_prefix="llm") as pool:
        futures = [pool.submit(function, item) for item in items]
        try:
            return [future.result() for future in futures]
        except BaseException:
            for future in futures:
                future.cancel()
            raise
