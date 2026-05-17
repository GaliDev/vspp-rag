from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
QUERIES_PATH = ROOT / "data" / "eval" / "queries.jsonl"
EMBED_DIR = ROOT / "data" / "embeddings"
VECTORS_PATH = EMBED_DIR / "vectors.npy"
INDEX_PATH = EMBED_DIR / "chunk_index.jsonl"
CHUNKS_PATH = ROOT / "data" / "chunks" / "chunks.jsonl"
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run retrieval eval queries against chunk embeddings.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    for path in (QUERIES_PATH, VECTORS_PATH, INDEX_PATH, CHUNKS_PATH):
        if not path.is_file():
            raise SystemExit(f"Missing required file: {path}")

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise SystemExit("pip install sentence-transformers") from exc

    queries = load_jsonl(QUERIES_PATH)
    index = load_jsonl(INDEX_PATH)
    chunks = load_jsonl(CHUNKS_PATH)
    vectors = np.load(VECTORS_PATH)
    chunk_by_id = {c["chunk_id"]: c for c in chunks}

    model = SentenceTransformer(args.model)
    hits = 0
    total = len(queries)

    print(f"Retrieval eval: {total} queries, top_k={args.top_k}\n")
    for q in queries:
        qid = q.get("id", "?")
        text = q["query"]
        expect = set(q.get("expect_external_ids") or [])
        qvec = model.encode([text], normalize_embeddings=True)[0]
        scores = vectors @ qvec
        top_idx = np.argsort(scores)[::-1][: args.top_k]
        got_ids = {index[i]["external_id"] for i in top_idx}
        ok = bool(expect & got_ids) if expect else True
        hits += int(ok)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {qid}: {text[:72]}")
        print(f"  expect any of: {sorted(expect)}")
        print(f"  got: {sorted(got_ids)}")
        best = top_idx[0]
        preview = chunk_by_id.get(index[best]["chunk_id"], {}).get("text", "")[:160].replace("\n", " ")
        print(f"  top hit ({index[best]['external_id']}): {preview}...\n")

    print(f"Summary: {hits}/{total} queries matched expected external_id in top-{args.top_k}")


if __name__ == "__main__":
    main()
