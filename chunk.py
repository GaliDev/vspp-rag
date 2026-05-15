from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).parent
RECORDS_PATH = ROOT / "data" / "normalized" / "records.jsonl"
CHUNKS_DIR = ROOT / "data" / "chunks"
CHUNKS_PATH = CHUNKS_DIR / "chunks.jsonl"


def naive_char_chunks(text: str, chunk_chars: int, overlap_chars: int) -> Iterator[tuple[int, int, str]]:
    """Fixed-size windows with stride (chunk_chars - overlap_chars)."""
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    overlap = max(0, min(overlap_chars, chunk_chars - 1))
    step = chunk_chars - overlap
    n = len(text)
    start = 0
    while start < n:
        end = min(start + chunk_chars, n)
        yield start, end, text[start:end]
        if end >= n:
            break
        start += step


def load_normalized_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") == "normalized" and row.get("normalized_path"):
            rows.append(row)
    return rows


def chunk_record(
    record: dict[str, Any],
    chunk_chars: int,
    overlap_chars: int,
) -> list[dict[str, Any]]:
    norm_rel = str(record["normalized_path"])
    path = ROOT / norm_rel
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    source = str(record.get("source") or "")
    ext = str(record.get("external_id") or "")
    out: list[dict[str, Any]] = []
    for idx, (start, end, chunk_text) in enumerate(
        naive_char_chunks(text, chunk_chars, overlap_chars)
    ):
        chunk_id = f"{source}:{ext}:{idx:05d}"
        out.append(
            {
                "chunk_id": chunk_id,
                "chunk_index": idx,
                "char_start": start,
                "char_end": end,
                "text": chunk_text,
                "source": record.get("source"),
                "external_id": record.get("external_id"),
                "authority": record.get("authority"),
                "title": record.get("title"),
                "category": record.get("category"),
                "tier": record.get("tier"),
                "normalized_path": norm_rel,
                "chunker": "naive_chars_v1",
                "chunk_chars": chunk_chars,
                "overlap_chars": overlap_chars,
            }
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Naive fixed-character chunking over normalized .txt (from records.jsonl).",
    )
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=2000,
        help="Maximum characters per chunk (Unicode scalar sequence length).",
    )
    parser.add_argument(
        "--overlap-chars",
        type=int,
        default=200,
        help="Overlap between consecutive windows.",
    )
    parser.add_argument(
        "--source",
        default="all",
        help="Filter by manifest source (or all).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max normalized records to process (stable order).",
    )
    args = parser.parse_args()

    rows = load_normalized_rows(RECORDS_PATH)
    if not rows:
        raise SystemExit(f"No normalized rows at {RECORDS_PATH}. Run normalize.py first.")

    if args.source != "all":
        rows = [r for r in rows if r.get("source") == args.source]
    if args.limit is not None:
        rows = rows[: args.limit]

    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    total_chunks = 0
    missing_files = 0
    with CHUNKS_PATH.open("w", encoding="utf-8") as f:
        for record in rows:
            path = ROOT / str(record["normalized_path"])
            if not path.is_file():
                missing_files += 1
                continue
            for row in chunk_record(record, args.chunk_chars, args.overlap_chars):
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                total_chunks += 1

    print(
        f"Chunking complete: records={len(rows)}, chunks={total_chunks}, "
        f"missing_normalized_files={missing_files}, output={CHUNKS_PATH.relative_to(ROOT)} "
        f"(chunk_chars={args.chunk_chars}, overlap_chars={args.overlap_chars})."
    )


if __name__ == "__main__":
    main()
