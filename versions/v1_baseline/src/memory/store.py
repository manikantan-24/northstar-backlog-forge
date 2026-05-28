"""Shared memory store for agent handoff.

Two flavors of storage:

1. **Structured KV memory** — `get(key)` / `put(key, value)` for explicit
   agent handoff. Examples of keys: `topics`, `constraints`, `stories`,
   `epics`, `gaps`, `conflicts`, `duplicates`, `existing_tickets`.

2. **Vector memory** — `embed_many()` / `search_similar()` for semantic
   lookup. Used by the Gap Detector to find candidate JIRA/GitHub tickets
   that look semantically similar to a new story before LLM reranking.

The vector layer defaults to an in-memory numpy-backed implementation that
loads sentence-transformers lazily. ChromaDB can be plugged in later by
swapping the implementation behind the same interface — see
`docs/MEMORY_DESIGN.md`.
"""

from __future__ import annotations

from typing import Any

from logger_setup import get_logger

logger = get_logger(__name__)

_RETRIEVAL_THRESHOLD = 20  # Below this, skip embeddings and return everything.


class MemoryStore:
    """In-memory KV + lazy vector store for one orchestrator run."""

    def __init__(self) -> None:
        self._kv: dict[str, Any] = {}
        self._embedder = None  # Lazy-loaded sentence-transformer
        self._np = None
        self._ticket_vectors = None
        self._tickets_for_vectors: list[dict] = []

    # ----------------------------------------------------- KV interface

    def get(self, key: str, default: Any = None) -> Any:
        return self._kv.get(key, default)

    def put(self, key: str, value: Any) -> None:
        self._kv[key] = value

    def append(self, key: str, value: Any) -> None:
        """Append to a list at `key`. Creates the list if missing."""
        if key not in self._kv:
            self._kv[key] = []
        self._kv[key].append(value)

    # ----------------------------------------------------- Vector interface

    def index_tickets(self, tickets: list[dict]) -> bool:
        """Embed and index existing tickets for semantic search.

        Returns True if embeddings were built, False if the system fell back
        to no-embedding mode (small ticket set or sentence-transformers not
        installed).
        """
        self._tickets_for_vectors = list(tickets)
        if len(tickets) < _RETRIEVAL_THRESHOLD:
            logger.info("Only %d tickets — skipping embeddings (under threshold)", len(tickets))
            return False
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
        except ImportError:
            logger.warning("sentence-transformers not installed — falling back to no-embedding mode")
            return False

        self._np = np
        self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
        texts = [self._ticket_text(t) for t in tickets]
        self._ticket_vectors = self._embedder.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        logger.info("Indexed %d tickets for semantic search", len(tickets))
        return True

    def search_similar(self, query_text: str, top_k: int = 5) -> list[dict]:
        """Return top-K most similar tickets to query_text.

        If the index wasn't built (small ticket set), returns the full list.
        """
        if self._ticket_vectors is None:
            return list(self._tickets_for_vectors)
        query_vec = self._embedder.encode(
            [query_text],
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        sims = (query_vec @ self._ticket_vectors.T)[0]
        top_indices = self._np.argsort(-sims)[:top_k]
        return [
            dict(self._tickets_for_vectors[i], _similarity=float(sims[i]))
            for i in top_indices
        ]

    @staticmethod
    def _ticket_text(t: dict) -> str:
        title = (t.get("title") or "").strip()
        description = (t.get("description") or "").strip()
        if title and description:
            return f"{title}. {description}"
        return title or description
