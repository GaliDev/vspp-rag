from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Conformance repo dominates BMFF semantic search; exclude when query targets normative ISOBMFF.
CONFORMANCE_EXTERNAL_ID = "MPEGGroup/FileFormatConformance"
ISOBMFF_EXTERNAL_ID = "MPEGGroup/isobmff"


@dataclass
class RetrievalFilters:
    authorities: frozenset[str] = field(default_factory=frozenset)
    external_ids: frozenset[str] = field(default_factory=frozenset)
    categories: frozenset[str] = field(default_factory=frozenset)
    sources: frozenset[str] = field(default_factory=frozenset)
    core_structural_only: bool = False
    exclude_external_ids: frozenset[str] = field(default_factory=frozenset)
    exclude_categories: frozenset[str] = field(default_factory=frozenset)

    def is_empty(self) -> bool:
        return (
            not self.authorities
            and not self.external_ids
            and not self.categories
            and not self.sources
            and not self.core_structural_only
            and not self.exclude_external_ids
            and not self.exclude_categories
        )


@dataclass
class RetrievalIndex:
    vectors: np.ndarray
    index_rows: list[dict[str, Any]]
    chunk_by_id: dict[str, dict[str, Any]]
    model_name: str


@dataclass
class SearchHit:
    index: int
    score: float
    chunk_id: str
    external_id: str | None
    authority: str | None
    title: str | None
    text: str
    char_start: int | None
    char_end: int | None


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_index(
    embed_dir: Path,
    chunks_path: Path,
    *,
    vectors_name: str = "vectors.npy",
    index_name: str = "chunk_index.jsonl",
) -> RetrievalIndex:
    vectors_path = embed_dir / vectors_name
    index_path = embed_dir / index_name
    meta_path = embed_dir / "meta.json"
    for path in (vectors_path, index_path, chunks_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    model_name = DEFAULT_MODEL
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        model_name = str(meta.get("model") or DEFAULT_MODEL)

    vectors = np.load(vectors_path)
    index_rows = load_jsonl(index_path)
    chunks = load_jsonl(chunks_path)
    chunk_by_id = {c["chunk_id"]: c for c in chunks if c.get("chunk_id")}
    if len(index_rows) != vectors.shape[0]:
        raise ValueError(
            f"Index rows ({len(index_rows)}) != vector rows ({vectors.shape[0]})"
        )
    return RetrievalIndex(
        vectors=vectors,
        index_rows=index_rows,
        chunk_by_id=chunk_by_id,
        model_name=model_name,
    )


def _authority_matches(row_authority: str, allowed: frozenset[str]) -> bool:
    if not allowed:
        return True
    ra = (row_authority or "").lower()
    for auth in allowed:
        a = auth.lower()
        if a == ra or a in ra or ra in a:
            return True
    return False


def _row_matches(row: dict[str, Any], filters: RetrievalFilters) -> bool:
    ext = str(row.get("external_id") or "")
    if filters.exclude_external_ids and ext in filters.exclude_external_ids:
        return False
    if filters.external_ids and ext not in filters.external_ids:
        return False
    auth = str(row.get("authority") or "")
    if filters.authorities and not _authority_matches(auth, filters.authorities):
        return False
    cat = str(row.get("category") or "")
    if filters.exclude_categories and cat in filters.exclude_categories:
        return False
    if filters.categories and cat not in filters.categories:
        return False
    src = str(row.get("source") or "")
    if filters.sources and src not in filters.sources:
        return False
    if filters.core_structural_only and not row.get("core_structural_syntax"):
        return False
    return True


def build_mask(index_rows: list[dict[str, Any]], filters: RetrievalFilters) -> np.ndarray:
    if filters.is_empty():
        return np.ones(len(index_rows), dtype=bool)
    return np.array([_row_matches(row, filters) for row in index_rows], dtype=bool)


def _query_has_any(query: str, patterns: list[str]) -> bool:
    low = query.lower()
    return any(re.search(p, low) for p in patterns)


def route_query(query: str, *, filter_hints: dict[str, Any] | None = None) -> RetrievalFilters:
    """Keyword router: narrow search space without an LLM."""
    hints = filter_hints or {}
    authorities: set[str] = set(hints.get("authorities") or [])
    external_ids: set[str] = set(hints.get("external_ids") or [])
    categories: set[str] = set(hints.get("categories") or [])
    sources: set[str] = set(hints.get("sources") or [])
    exclude: set[str] = set(hints.get("exclude_external_ids") or [])
    exclude_categories: set[str] = set(hints.get("exclude_categories") or [])
    core_only = bool(hints.get("core_structural_only"))

    if _query_has_any(
        query,
        [r"\bconformance\b", r"\bfixtures\b", r"\btest vectors\b", r"\bjson\b"],
    ):
        external_ids.add(CONFORMANCE_EXTERNAL_ID)
    elif _query_has_any(
        query,
        [r"\bmoof\b", r"\bmvex\b", r"\btrun\b", r"\bisobmff\b", r"\bbmff\b", r"\bftyp\b", r"\bmp4\b", r"\bmdat\b"],
    ):
        categories.add("Structural/System")
        exclude.add(CONFORMANCE_EXTERNAL_ID)
        if _query_has_any(query, [r"\bisobmff\b", r"\bbmff\b", r"\bmoof\b", r"\b14496-12\b", r"\bftyp\b"]):
            external_ids.add(ISOBMFF_EXTERNAL_ID)

    if _query_has_any(query, [r"\bav1\b", r"\bobu\b", r"\btile group\b", r"\bsequence header\b"]):
        external_ids.add("AOMediaCodec/av1-spec")

    if _query_has_any(
        query,
        [r"\bdash\b", r"\bmpd\b", r"\bsegment template\b", r"\bperiod element\b", r"\bts\s*103\s*285\b"],
    ):
        external_ids.add("etsi-dvb-dash-ts103168")

    if _query_has_any(query, [r"\bimsc\b", r"\bttml\b", r"\btext profile\b", r"\bstyling requirements\b"]):
        authorities.add("w3c")

    if _query_has_any(query, [r"\bdvb-s2\b", r"\bmodcod\b", r"\bfec frame\b", r"\ben\s*302\s*307\b"]):
        external_ids.add("etsi-dvb-s2-en302307")

    if _query_has_any(query, [r"\bpat\b", r"\bpmt\b", r"\bnit\b", r"\bpsi\b", r"\bsi\b", r"\bservice information\b"]):
        external_ids.add("etsi-en-300-468-dvb-si")

    if _query_has_any(query, [r"\bsubtitl", r"\ben\s*300\s*743\b", r"\bsegment display\b"]):
        external_ids.add("etsi-en-300-743-dvb-sub")

    if _query_has_any(query, [r"\bstructural\b", r"\bcontainer syntax\b", r"\bsystem-level\b"]):
        core_only = True

    if _query_has_any(
        query,
        [
            r"\bmk-vspp\b",
            r"\bado\b",
            r"\bazure devops\b",
            r"\bproject wiki\b",
            r"\binternal wiki\b",
            r"\bwiki page\b",
        ],
    ):
        sources.add("ado_wiki")
        categories.add("Internal")

    if _query_has_any(
        query,
        [r"\bconfluence\b", r"\bnggui\b", r"\bsetup developer environment\b", r"\bcode conventions\b"],
    ):
        sources.add("confluence")
        categories.add("Internal")

    return RetrievalFilters(
        authorities=frozenset(authorities),
        external_ids=frozenset(external_ids),
        categories=frozenset(categories),
        sources=frozenset(sources),
        core_structural_only=core_only,
        exclude_external_ids=frozenset(exclude),
        exclude_categories=frozenset(exclude_categories),
    )


def search(
    index: RetrievalIndex,
    query: str,
    *,
    top_k: int = 5,
    filters: RetrievalFilters | None = None,
    model: Any | None = None,
) -> list[SearchHit]:
    if top_k <= 0:
        return []

    filt = filters or RetrievalFilters()
    mask = build_mask(index.index_rows, filt)
    if not mask.any():
        return []

    if model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError("pip install sentence-transformers") from exc
        model = SentenceTransformer(index.model_name)

    qvec = model.encode([query], normalize_embeddings=True)[0]
    masked_idx = np.where(mask)[0]
    sub_vectors = index.vectors[masked_idx]
    scores = sub_vectors @ qvec
    order = np.argsort(scores)[::-1][:top_k]

    hits: list[SearchHit] = []
    for rank in order:
        i = int(masked_idx[rank])
        row = index.index_rows[i]
        chunk_id = str(row.get("chunk_id") or "")
        chunk = index.chunk_by_id.get(chunk_id, {})
        hits.append(
            SearchHit(
                index=i,
                score=float(scores[rank]),
                chunk_id=chunk_id,
                external_id=row.get("external_id"),
                authority=row.get("authority"),
                title=row.get("title"),
                text=str(chunk.get("text") or ""),
                char_start=chunk.get("char_start"),
                char_end=chunk.get("char_end"),
            )
        )
    return hits
