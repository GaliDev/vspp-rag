from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterator

from src.core.text_prep import strip_manifest_preamble

ROOT = Path(__file__).parent
RECORDS_PATH = ROOT / "data" / "normalized" / "records.jsonl"
CHUNKS_DIR = ROOT / "data" / "chunks"
CHUNKS_PATH = CHUNKS_DIR / "chunks.jsonl"

INTERNAL_CHUNK_META_KEYS = (
    "ado_org",
    "ado_project",
    "wiki_path",
    "wiki_id",
    "wiki_name",
    "space_key",
    "page_id",
    "content_version",
)


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


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def paragraph_aware_chunks(
    text: str,
    chunk_chars: int,
    overlap_chars: int,
) -> Iterator[tuple[int, int, str]]:
    """Pack paragraphs up to chunk_chars; overlap carries trailing paragraphs."""
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    paras = _split_paragraphs(text)
    if not paras:
        yield from naive_char_chunks(text, chunk_chars, overlap_chars)
        return
    if any(len(p) > chunk_chars for p in paras):
        yield from naive_char_chunks(text, chunk_chars, overlap_chars)
        return

    packed: list[str] = []
    buf: list[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        packed.append("\n\n".join(buf))
        buf = []
        buf_len = 0

    for para in paras:
        add = len(para) + (2 if buf else 0)
        if buf and buf_len + add > chunk_chars:
            flush()
        buf.append(para)
        buf_len += add
    flush()

    if len(packed) <= 1:
        for start, end, chunk in naive_char_chunks(text, chunk_chars, overlap_chars):
            yield start, end, chunk
        return

    overlap = max(0, min(overlap_chars, chunk_chars - 1))
    merged: list[str] = [packed[0]]
    for i in range(1, len(packed)):
        prev_paras = _split_paragraphs(packed[i - 1])
        prefix: list[str] = []
        plen = 0
        for para in reversed(prev_paras):
            add = len(para) + (2 if prefix else 0)
            if prefix and plen + add > overlap:
                break
            prefix.insert(0, para)
            plen += add
        body = packed[i]
        merged.append("\n\n".join(prefix + [body]) if prefix else body)

    cursor = 0
    for chunk in merged:
        probe = chunk[: min(120, len(chunk))]
        start = text.find(probe, cursor) if probe else cursor
        if start < 0:
            start = cursor
        end = start + len(chunk)
        yield start, end, chunk
        cursor = max(0, end - overlap)


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
    text = strip_manifest_preamble(path.read_text(encoding="utf-8", errors="replace"))
    source = str(record.get("source") or "")
    ext = str(record.get("external_id") or "")
    out: list[dict[str, Any]] = []
    for idx, (start, end, chunk_text) in enumerate(
        paragraph_aware_chunks(text, chunk_chars, overlap_chars)
    ):
        chunk_id = f"{source}:{ext}:{idx:05d}"
        chunk_row: dict[str, Any] = {
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
            "core_structural_syntax": record.get("core_structural_syntax"),
            "ingest_kind": record.get("ingest_kind"),
            "normalized_path": norm_rel,
            "chunker": "paragraph_pack_v1",
            "chunk_chars": chunk_chars,
            "overlap_chars": overlap_chars,
        }
        for key in INTERNAL_CHUNK_META_KEYS:
            if key in record:
                chunk_row[key] = record[key]
        out.append(chunk_row)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Paragraph-aware chunking over normalized .txt (from records.jsonl).",
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
