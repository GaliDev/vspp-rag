from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
MANIFEST_PATH = ROOT / "discovery_manifest.json"
NORMALIZED_DIR = ROOT / "data" / "normalized"
RECORDS_PATH = NORMALIZED_DIR / "records.jsonl"

TEXT_EXTENSIONS = {
    ".css",
    ".html",
    ".htm",
    ".json",
    ".md",
    ".rng",
    ".srt",
    ".ttml",
    ".txt",
    ".vtt",
    ".xml",
    ".xsd",
}

SKIP_DIRS = {
    ".git",
    ".github",
    "__pycache__",
    "node_modules",
    "vendor",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_filename(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    return cleaned.strip("._") or "record"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collapse_blank_lines(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return collapse_blank_lines(text)


def read_text_file(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in {".html", ".htm"}:
        return html_to_text(raw)
    return collapse_blank_lines(raw)


def manifest_header(record: dict[str, Any]) -> str:
    fields = [
        ("title", record.get("title")),
        ("external_id", record.get("external_id")),
        ("source", record.get("source")),
        ("authority", record.get("authority")),
        ("category", record.get("category")),
        ("tier", record.get("tier")),
        ("remote_url", record.get("remote_url")),
    ]
    lines = ["# " + str(record.get("title") or record.get("external_id") or "Untitled")]
    for key, value in fields:
        if value:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def normalized_path_for(record: dict[str, Any], raw_path: Path | None) -> Path:
    external_id = safe_filename(str(record.get("external_id") or "record"))
    if raw_path and raw_path.exists():
        raw_parent = raw_path.parent
        authority_root = raw_parent.parent if raw_parent.name == "raw" else raw_parent
        return authority_root / "normalized" / f"{external_id}.txt"
    source = safe_filename(str(record.get("source") or "unknown"))
    return NORMALIZED_DIR / "orphaned" / f"{source}__{external_id}.txt"


def local_path(record: dict[str, Any], key: str = "local_path") -> Path | None:
    value = record.get(key)
    if not value:
        return None
    return ROOT / Path(str(value))


def metadata_path(record: dict[str, Any], key: str) -> Path | None:
    value = record.get("metadata", {}).get(key)
    if not value:
        return None
    return ROOT / Path(str(value))


def iter_repo_text_files(root: Path, max_files: int, max_file_bytes: int) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    skipped: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            skipped.append(f"{path}: stat failed: {exc}")
            continue
        if size > max_file_bytes:
            skipped.append(f"{path.relative_to(root)}: {size} bytes exceeds cap")
            continue
        files.append(path)
        if len(files) >= max_files:
            skipped.append(f"file cap reached at {max_files}")
            break
    return files, skipped


def normalize_page(record: dict[str, Any], raw_path: Path) -> tuple[str, dict[str, Any]]:
    text = html_to_text(raw_path.read_text(encoding="utf-8", errors="replace"))
    content = f"{manifest_header(record)}\n\n{text}\n"
    return content, {
        "normalizer": "html_text_v1",
        "source_files": [str(raw_path.relative_to(ROOT))],
    }


def normalize_text_file(record: dict[str, Any], raw_path: Path) -> tuple[str, dict[str, Any]]:
    text = read_text_file(raw_path)
    content = f"{manifest_header(record)}\n\n{text}\n"
    return content, {
        "normalizer": "single_text_v1",
        "source_files": [str(raw_path.relative_to(ROOT))],
    }


def normalize_repo(record: dict[str, Any], extracted_to: Path, max_files: int, max_file_bytes: int) -> tuple[str, dict[str, Any]]:
    files, skipped = iter_repo_text_files(extracted_to, max_files, max_file_bytes)
    sections = [manifest_header(record)]
    source_files: list[str] = []
    for path in files:
        rel = path.relative_to(extracted_to)
        try:
            text = read_text_file(path)
        except OSError as exc:
            skipped.append(f"{rel}: read failed: {exc}")
            continue
        if not text:
            continue
        source_files.append(str(path.relative_to(ROOT)))
        sections.append(f"## File: {rel.as_posix()}\n\n{text}")
    content = "\n\n".join(sections).strip() + "\n"
    return content, {
        "normalizer": "repo_text_bundle_v1",
        "source_files": source_files,
        "skipped_files": skipped,
    }


def normalize_record(record: dict[str, Any], max_files: int, max_file_bytes: int) -> dict[str, Any] | None:
    if record.get("status") != "ingested":
        return None

    raw_path = local_path(record)
    extracted_to = metadata_path(record, "extracted_to")
    if extracted_to and extracted_to.exists():
        content, meta = normalize_repo(record, extracted_to, max_files, max_file_bytes)
        if not meta.get("source_files"):
            return {
                "external_id": record.get("external_id"),
                "source": record.get("source"),
                "status": "skipped",
                "reason": "no_supported_text_files",
                "raw_path": record.get("local_path"),
                "extracted_to": str(extracted_to.relative_to(ROOT)),
                "normalized_at": utc_now_iso(),
                "skipped_files": meta.get("skipped_files", []),
            }
    elif raw_path and raw_path.exists() and raw_path.suffix.lower() in {".html", ".htm"}:
        content, meta = normalize_page(record, raw_path)
    elif raw_path and raw_path.exists() and raw_path.suffix.lower() in TEXT_EXTENSIONS:
        content, meta = normalize_text_file(record, raw_path)
    else:
        return {
            "external_id": record.get("external_id"),
            "source": record.get("source"),
            "status": "skipped",
            "reason": "no_supported_normalizer",
            "raw_path": str(raw_path.relative_to(ROOT)) if raw_path and raw_path.exists() else record.get("local_path"),
            "normalized_at": utc_now_iso(),
        }

    output_path = normalized_path_for(record, raw_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    return {
        "external_id": record.get("external_id"),
        "source": record.get("source"),
        "authority": record.get("authority"),
        "title": record.get("title"),
        "category": record.get("category"),
        "tier": record.get("tier"),
        "raw_path": record.get("local_path"),
        "normalized_path": str(output_path.relative_to(ROOT)),
        "content_type": "text/plain",
        "status": "normalized",
        "normalized_at": utc_now_iso(),
        "normalized_sha256": sha256_text(content),
        "raw_sha256": sha256_file(raw_path) if raw_path and raw_path.exists() else None,
        "char_count": len(content),
        **meta,
    }


def load_existing_records(path: Path) -> dict[tuple[str | None, str | None], dict[str, Any]]:
    if not path.exists():
        return {}
    out: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out[(row.get("source"), row.get("external_id"))] = row
    return out


def write_records(path: Path, records: dict[tuple[str | None, str | None], dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records.values(), key=lambda r: (str(r.get("source")), str(r.get("external_id"))))
    with path.open("w", encoding="utf-8") as f:
        for row in ordered:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize ingested standards artifacts into text for RAG indexing.")
    parser.add_argument("--source", default="all", help="Source filter (iso|etsi|dvb|github|w3c|ietf|3gpp|all)")
    parser.add_argument("--limit", type=int, default=None, help="Maximum ingested rows to process this run.")
    parser.add_argument("--max-files", type=int, default=200, help="Max text files to include from an extracted repo.")
    parser.add_argument("--max-file-kb", type=int, default=512, help="Max size per text file included from a repo.")
    args = parser.parse_args()

    if not MANIFEST_PATH.exists():
        raise SystemExit("discovery_manifest.json not found. Run discover.py first.")

    records = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    selected = [r for r in records if r.get("status") == "ingested"]
    if args.source != "all":
        selected = [r for r in selected if r.get("source") == args.source]
    if args.limit is not None:
        selected = selected[: args.limit]

    existing = load_existing_records(RECORDS_PATH)
    normalized = 0
    skipped = 0
    for record in selected:
        row = normalize_record(record, args.max_files, args.max_file_kb * 1024)
        if row is None:
            continue
        existing[(row.get("source"), row.get("external_id"))] = row
        if row.get("status") == "normalized":
            normalized += 1
        else:
            skipped += 1

    write_records(RECORDS_PATH, existing)
    print(
        f"Normalization complete: normalized={normalized}, skipped={skipped}, "
        f"index={RECORDS_PATH.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()
