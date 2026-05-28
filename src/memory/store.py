"""Shared memory store for agent handoff.

Two flavors of storage:

1. **Structured KV memory** — `get(key)` / `put(key, value)` for explicit
   agent handoff. Examples of keys: `topics`, `constraints`, `stories`,
   `epics`, `gaps`, `conflicts`, `duplicates`, `existing_tickets`.

2. **Vector memory** — `index_tickets()` / `search_similar()` for semantic
   lookup. Used by the Gap Detector to find candidate JIRA/GitHub tickets
   that look semantically similar to a new story before LLM reranking.

The vector layer supports two backends:

  - **in-process** (default): numpy + sentence-transformers, no persistence.
    Right for unit tests, dry runs, and short-lived demo runs.
  - **persistent**: stores embeddings in a content-addressed file cache at
    `.cache/memory/<corpus_hash>.npz` (vectors) and `.json` (tickets), so
    re-indexing the same backlog across runs is a no-op disk read instead of
    a fresh model encode. Switch on by constructing the store with
    `persistent=True`, or by setting `MEMORY_PERSISTENT=1` in the env.

A simple .json file under `.cache/memory/kv/<key>.json` is also written for
each `put()` when persistence is on, so a follow-up run can hydrate the
last orchestrator's state for inspection / replay.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from logger_setup import get_logger

logger = get_logger(__name__)

_RETRIEVAL_THRESHOLD = 20  # Below this, skip embeddings and return everything.
_DEFAULT_CACHE_DIR = Path(".cache") / "memory"
_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class MemoryStore:
    """KV + vector store. Optionally persists to disk across runs."""

    def __init__(
        self,
        *,
        persistent: bool | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self._kv: dict[str, Any] = {}
        self._embedder = None  # Lazy-loaded sentence-transformer
        self._np = None
        self._ticket_vectors = None
        self._tickets_for_vectors: list[dict] = []

        if persistent is None:
            persistent = os.environ.get("MEMORY_PERSISTENT", "").lower() in ("1", "true", "yes")
        self._persistent = bool(persistent)
        self._cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        if self._persistent:
            (self._cache_dir / "kv").mkdir(parents=True, exist_ok=True)
            (self._cache_dir / "vectors").mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------- KV interface

    def get(self, key: str, default: Any = None) -> Any:
        return self._kv.get(key, default)

    def put(self, key: str, value: Any) -> None:
        self._kv[key] = value
        if self._persistent:
            self._persist_kv(key, value)

    def append(self, key: str, value: Any) -> None:
        """Append to a list at `key`. Creates the list if missing."""
        if key not in self._kv:
            self._kv[key] = []
        self._kv[key].append(value)
        if self._persistent:
            self._persist_kv(key, self._kv[key])

    # ----------------------------------------------------- KV persistence

    def _persist_kv(self, key: str, value: Any) -> None:
        path = self._cache_dir / "kv" / f"{key}.json"
        try:
            path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
        except (TypeError, OSError) as e:
            logger.warning("Could not persist KV key %r: %s", key, e)

    def hydrate_from_disk(self) -> int:
        """Reload KV state from `<cache_dir>/kv/*.json`. Returns count loaded.

        Useful when a follow-up process wants to inspect the last run's
        memory contents without re-running the pipeline. Has no effect when
        persistence wasn't enabled at the time of the previous run.
        """
        kv_dir = self._cache_dir / "kv"
        if not kv_dir.exists():
            return 0
        count = 0
        for f in kv_dir.glob("*.json"):
            try:
                self._kv[f.stem] = json.loads(f.read_text(encoding="utf-8"))
                count += 1
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Skipping KV cache %s: %s", f.name, e)
        return count

    # ----------------------------------------------------- Vector interface

    def index_tickets(self, tickets: list[dict]) -> bool:
        """Embed and index existing tickets for semantic search.

        Returns True if embeddings were built, False if the system fell back
        to no-embedding mode (small ticket set or sentence-transformers not
        installed). When `persistent=True` and a matching cache file exists,
        embeddings are loaded from disk instead of being recomputed.
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

        # Persistent path: try to load embeddings keyed by content hash.
        cache_hit = False
        if self._persistent:
            cache_hit = self._try_load_vectors(tickets, np)
            if cache_hit:
                logger.info("Hydrated %d embeddings from cache", len(tickets))
                return True

        self._embedder = SentenceTransformer(_EMBEDDING_MODEL)
        texts = [self._ticket_text(t) for t in tickets]
        self._ticket_vectors = self._embedder.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        if self._persistent:
            self._save_vectors(tickets)

        logger.info("Indexed %d tickets for semantic search", len(tickets))
        return True

    def search_similar(self, query_text: str, top_k: int = 5) -> list[dict]:
        """Return top-K most similar tickets to query_text.

        If the index wasn't built (small ticket set), returns the full list.
        Loads the embedder lazily — searches against a hydrated cache work
        even when the original `index_tickets()` call hit the cache.
        """
        if self._ticket_vectors is None:
            return list(self._tickets_for_vectors)
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer(_EMBEDDING_MODEL)
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

    # ----------------------------------------------------- vector cache

    def _corpus_hash(self, tickets: list[dict]) -> str:
        """Stable hash of the ticket corpus — invalidates the cache on change."""
        h = hashlib.sha256()
        h.update(_EMBEDDING_MODEL.encode())
        for t in tickets:
            h.update((self._ticket_text(t) + "\0").encode("utf-8"))
        return h.hexdigest()[:16]

    def _vector_cache_path(self, tickets: list[dict]) -> Path:
        return self._cache_dir / "vectors" / f"{self._corpus_hash(tickets)}.npz"

    def _try_load_vectors(self, tickets: list[dict], np) -> bool:
        path = self._vector_cache_path(tickets)
        if not path.exists():
            return False
        try:
            data = np.load(path)
            self._ticket_vectors = data["vectors"]
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load vector cache %s: %s", path, e)
            return False

    def _save_vectors(self, tickets: list[dict]) -> None:
        path = self._vector_cache_path(tickets)
        try:
            import numpy as np
            np.savez_compressed(path, vectors=self._ticket_vectors)
            logger.info("Wrote vector cache to %s", path)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to save vector cache: %s", e)

    @staticmethod
    def _ticket_text(t: dict) -> str:
        title = (t.get("title") or "").strip()
        description = (t.get("description") or "").strip()
        if title and description:
            return f"{title}. {description}"
        return title or description
