from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.core.retrieval import RetrievalFilters, load_index, route_query, search

ROOT = Path(__file__).parent
QUERIES_PATH = ROOT / "data" / "eval" / "queries.jsonl"
EMBED_DIR = ROOT / "data" / "embeddings"
CHUNKS_PATH = ROOT / "data" / "chunks" / "chunks.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def filters_for_mode(mode: str, query_row: dict) -> RetrievalFilters | None:
    if mode == "baseline":
        return None
    if mode == "structural":
        return RetrievalFilters(core_structural_only=True)
    if mode == "router":
        return route_query(
            query_row["query"],
            filter_hints=query_row.get("filter_hints"),
        )
    raise ValueError(f"Unknown mode: {mode}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run retrieval eval queries against chunk embeddings.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--mode",
        choices=("baseline", "router", "structural"),
        default="router",
        help="baseline=all chunks; router=keyword filters; structural=core_structural_syntax only",
    )
    parser.add_argument("--embed-dir", type=Path, default=EMBED_DIR)
    parser.add_argument("--chunks", type=Path, default=CHUNKS_PATH)
    args = parser.parse_args()

    for path in (QUERIES_PATH, args.embed_dir / "vectors.npy", args.embed_dir / "chunk_index.jsonl", args.chunks):
        if not path.is_file():
            raise SystemExit(f"Missing required file: {path}")

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise SystemExit("pip install sentence-transformers") from exc

    queries = load_jsonl(QUERIES_PATH)
    index = load_index(args.embed_dir, args.chunks)
    model = SentenceTransformer(index.model_name)

    hits_count = 0
    total = len(queries)

    print(f"Retrieval eval: {total} queries, top_k={args.top_k}, mode={args.mode}\n")
    for q in queries:
        qid = q.get("id", "?")
        text = q["query"]
        expect = set(q.get("expect_external_ids") or [])
        filters = filters_for_mode(args.mode, q)
        results = search(index, text, top_k=args.top_k, filters=filters, model=model)
        got_ids = {h.external_id for h in results if h.external_id}
        ok = bool(expect & got_ids) if expect else True
        hits_count += int(ok)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {qid}: {text[:72]}")
        print(f"  expect any of: {sorted(expect)}")
        print(f"  got: {sorted(got_ids)}")
        if filters and not filters.is_empty():
            print(f"  filters: {filters}")
        if results:
            best = results[0]
            preview = best.text[:160].replace("\n", " ")
            print(f"  top hit ({best.external_id}): {preview}...\n")
        else:
            print("  top hit: (none)\n")

    print(f"Summary: {hits_count}/{total} queries matched expected external_id in top-{args.top_k}")


if __name__ == "__main__":
    main()
