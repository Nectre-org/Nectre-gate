"""Rank registered endpoints against an agent's question.

BM25 always runs and needs no model. If `fastembed` is installed and
NECTREGATE_EMBEDDINGS is not "0", dense embeddings are added and the two
rankings are merged with reciprocal rank fusion.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Any

from rank_bm25 import BM25Okapi

from .models import Endpoint, Source
from .registry import Registry

_STOP = set(
    "a an and are as at be by can do does for from get give how i in is it me my of on or show "
    "tell that the this to us was what when where which who why will with you your please find".split()
)


def tokenize(text: str) -> list[str]:
    tokens = []
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        if tok in _STOP or len(tok) < 2:
            continue
        if len(tok) > 4 and tok.endswith("ies"):
            tok = tok[:-3] + "y"
        elif len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        tokens.append(tok)
    return tokens


def card_text(source: Source, ep: Endpoint) -> str:
    """The text a question is matched against. Endpoint words count double."""
    endpoint_part = " ".join(
        [ep.summary, ep.description, " ".join(ep.examples), " ".join(f"{p.name} {p.description}" for p in ep.params)]
    )
    source_part = " ".join([source.name, source.description, " ".join(source.tags)])
    return f"{endpoint_part} {endpoint_part} {source_part}"


@dataclass
class Match:
    key: str
    score: float
    source: Source
    endpoint: Endpoint

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.key,
            "score": round(self.score, 4),
            "source": self.source.name,
            "summary": self.endpoint.summary,
            "required_params": [p.name for p in self.endpoint.params if p.required],
            "freshness": self.source.freshness,
            "trust": self.source.trust,
        }


class _Embedder:
    def __init__(self) -> None:
        from fastembed import TextEmbedding  # optional dependency

        self.model = TextEmbedding(os.environ.get("NECTREGATE_EMBED_MODEL", "BAAI/bge-small-en-v1.5"))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(v) for v in self.model.embed(texts)]


def _load_embedder() -> _Embedder | None:
    if os.environ.get("NECTREGATE_EMBEDDINGS", "1") == "0":
        return None
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return None
    try:
        return _Embedder()
    except Exception as e:  # e.g. model download blocked
        print(f"nectre-gate: embeddings unavailable ({type(e).__name__}); using BM25 only", file=sys.stderr)
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class Router:
    RRF_K = 60

    def __init__(self, registry: Registry, use_embeddings: bool | None = None):
        self.registry = registry
        self._built_version: object = None
        self._embedder = _load_embedder() if use_embeddings is not False else None
        self._items: list[tuple[str, Source, Endpoint]] = []

    @property
    def mode(self) -> str:
        return "hybrid (bm25 + embeddings)" if self._embedder else "bm25"

    def _build(self) -> None:
        if self._built_version == self.registry.version and self._items:
            return
        self._items = [(f"{s.id}.{e.id}", s, e) for s in self.registry.all() for e in s.endpoints]
        texts = [card_text(s, e) for _, s, e in self._items]
        self._bm25 = BM25Okapi([tokenize(t) or ["_"] for t in texts]) if texts else None
        self._vectors = self._embedder.embed(texts) if (self._embedder and texts) else None
        self._built_version = self.registry.version

    def search(self, question: str, limit: int = 5) -> list[Match]:
        self._build()
        if not self._items:
            return []
        n = len(self._items)
        bm25_scores = list(self._bm25.get_scores(tokenize(question))) if self._bm25 else [0.0] * n
        rankings = [sorted(range(n), key=lambda i: -bm25_scores[i])]
        if self._vectors is not None:
            q = self._embedder.embed([question])[0]
            dense = [_cosine(q, v) for v in self._vectors]
            rankings.append(sorted(range(n), key=lambda i: -dense[i]))
            fused = [0.0] * n
            for ranking in rankings:
                for rank, i in enumerate(ranking):
                    fused[i] += 1.0 / (self.RRF_K + rank + 1)
            scores = fused
        else:
            if max(bm25_scores) <= 0:
                return []
            scores = bm25_scores
        order = sorted(range(n), key=lambda i: -scores[i])[:limit]
        return [Match(self._items[i][0], scores[i], self._items[i][1], self._items[i][2]) for i in order]
