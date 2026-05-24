from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
CHUNKS_PATH = ROOT / "data" / "chunks" / "chunks.jsonl"
EMBED_DIR = ROOT / "data" / "embeddings"
INDEX_PATH = EMBED_DIR / "chunk_index.jsonl"
VECTORS_PATH = EMBED_DIR / "vectors.npy"
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def load_chunks(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed chunks.jsonl for retrieval.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="sentence-transformers model id")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()

    if not CHUNKS_PATH.is_file():
        raise SystemExit(f"Missing {CHUNKS_PATH}. Run chunk.py first.")

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise SystemExit(
            "sentence-transformers is required. Install with: pip install sentence-transformers"
        ) from exc

    chunks = load_chunks(CHUNKS_PATH)
    if not chunks:
        raise SystemExit("No chunks to embed.")

    texts = [str(c.get("text") or "") for c in chunks]
    model = SentenceTransformer(args.model)
    vectors = model.encode(
        texts,
        batch_size=args.batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    vectors = np.asarray(vectors, dtype=np.float32)

    EMBED_DIR.mkdir(parents=True, exist_ok=True)
    np.save(VECTORS_PATH, vectors)
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        for i, chunk in enumerate(chunks):
            row = {
                "index": i,
                "chunk_id": chunk.get("chunk_id"),
                "source": chunk.get("source"),
                "external_id": chunk.get("external_id"),
                "authority": chunk.get("authority"),
                "title": chunk.get("title"),
                "category": chunk.get("category"),
                "tier": chunk.get("tier"),
                "core_structural_syntax": chunk.get("core_structural_syntax"),
                "ado_org": chunk.get("ado_org"),
                "ado_project": chunk.get("ado_project"),
                "wiki_path": chunk.get("wiki_path"),
                "space_key": chunk.get("space_key"),
                "page_id": chunk.get("page_id"),
                "content_version": chunk.get("content_version"),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    meta = {
        "model": args.model,
        "dimensions": int(vectors.shape[1]),
        "chunk_count": len(chunks),
        "vectors_path": str(VECTORS_PATH.relative_to(ROOT)),
        "index_path": str(INDEX_PATH.relative_to(ROOT)),
    }
    (EMBED_DIR / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(
        f"Embeddings written: chunks={len(chunks)}, dims={vectors.shape[1]}, "
        f"vectors={VECTORS_PATH.relative_to(ROOT)}, index={INDEX_PATH.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()
