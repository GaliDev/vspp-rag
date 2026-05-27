from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from src.core.summarize import (
    DEFAULT_MODEL as DEFAULT_SUMMARY_MODEL,
    SummaryResult,
    Summarizer,
    get_summarizer,
    sha256_text,
    summarize_text,
)
from src.core.text_prep import content_with_header

ROOT = Path(__file__).parent
MANIFEST_PATH = ROOT / "discovery_manifest.json"
NORMALIZED_DIR = ROOT / "data" / "normalized"
RECORDS_PATH = NORMALIZED_DIR / "records.jsonl"

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

DOCX_EXTENSIONS = {".docx"}
PDF_EXTENSIONS = {".pdf"}

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
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return collapse_blank_lines(text)


def markdown_to_text(md: str) -> str:
    text = md.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"```[^\n]*\n(.*?)```", r"\1", text, flags=re.S)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return collapse_blank_lines(text)


def read_text_file(path: Path, *, source: str | None = None) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in {".html", ".htm"}:
        return html_to_text(raw)
    if path.suffix.lower() in {".rng", ".ttml", ".xml", ".xsd"}:
        soup = BeautifulSoup(raw, "xml")
        return collapse_blank_lines(soup.get_text("\n", strip=True))
    if path.suffix.lower() == ".md" and source == "ado_wiki":
        return markdown_to_text(raw)
    return collapse_blank_lines(raw)


def docx_xml_to_text(document_xml: bytes) -> str:
    root = ElementTree.fromstring(document_xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for para in root.findall(".//w:p", ns):
        text = "".join(node.text or "" for node in para.findall(".//w:t", ns)).strip()
        if text:
            paragraphs.append(text)
    return collapse_blank_lines("\n".join(paragraphs))


def docx_to_text(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as zf:
        with zf.open("word/document.xml") as f:
            return docx_xml_to_text(f.read())


def docx_bytes_to_text(blob: bytes) -> str:
    from io import BytesIO

    with zipfile.ZipFile(BytesIO(blob), "r") as zf:
        with zf.open("word/document.xml") as f:
            return docx_xml_to_text(f.read())


def is_pdf_file(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(5).startswith(b"%PDF")
    except OSError:
        return False


def looks_like_html_file(path: Path) -> bool:
    try:
        head = path.read_bytes()[:512].lower()
    except OSError:
        return False
    return b"<html" in head or b"<!doctype" in head


def pdf_to_text(path: Path, *, max_pages: int | None = None) -> tuple[str, dict[str, Any]]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError, PdfStreamError

    if not is_pdf_file(path):
        raise ValueError("not a valid PDF file (missing %PDF header)")

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ValueError(f"encrypted PDF: {exc}") from exc

    total = len(reader.pages)
    limit = total if max_pages is None else min(total, max_pages)
    parts: list[str] = []
    for idx in range(limit):
        try:
            page_text = reader.pages[idx].extract_text() or ""
        except PdfReadError as exc:
            raise ValueError(f"page {idx + 1}: {exc}") from exc
        if page_text.strip():
            parts.append(page_text.strip())

    meta: dict[str, Any] = {
        "pdf_page_count": total,
        "pdf_pages_extracted": limit,
    }
    if max_pages is not None and total > limit:
        meta["pdf_pages_truncated"] = True
    return collapse_blank_lines("\n\n".join(parts)), meta


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


def iter_docx_files(root: Path, max_files: int, max_file_bytes: int) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    skipped: list[str] = []
    for path in sorted(root.rglob("*.docx")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
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
    content = content_with_header(record, text)
    return content, {
        "normalizer": "html_text_v1",
        "source_files": [str(raw_path.relative_to(ROOT))],
    }


def normalize_text_file(record: dict[str, Any], raw_path: Path) -> tuple[str, dict[str, Any]]:
    text = read_text_file(raw_path, source=str(record.get("source") or ""))
    content = content_with_header(record, text)
    return content, {
        "normalizer": "single_text_v1",
        "source_files": [str(raw_path.relative_to(ROOT))],
    }


def normalize_docx_file(record: dict[str, Any], raw_path: Path) -> tuple[str, dict[str, Any]]:
    text = docx_to_text(raw_path)
    content = content_with_header(record, text)
    return content, {
        "normalizer": "docx_text_v1",
        "source_files": [str(raw_path.relative_to(ROOT))],
    }


def normalize_pdf_file(
    record: dict[str, Any],
    raw_path: Path,
    *,
    max_pages: int | None,
) -> tuple[str, dict[str, Any]]:
    text, pdf_meta = pdf_to_text(raw_path, max_pages=max_pages)
    if not text:
        raise ValueError("no extractable text in PDF")
    content = content_with_header(record, text)
    meta: dict[str, Any] = {
        "normalizer": "pdf_text_v1",
        "source_files": [str(raw_path.relative_to(ROOT))],
    }
    meta.update(pdf_meta)
    artifact = (record.get("metadata") or {}).get("artifact_url")
    if artifact:
        meta["artifact_url"] = artifact
    return content, meta


def normalize_docx_bundle(record: dict[str, Any], extracted_to: Path, max_files: int, max_file_bytes: int) -> tuple[str, dict[str, Any]]:
    files, skipped = iter_docx_files(extracted_to, max_files, max_file_bytes)
    sections = [content_with_header(record, "").strip()]
    source_files: list[str] = []
    for path in files:
        rel = path.relative_to(extracted_to)
        try:
            text = docx_to_text(path)
        except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            skipped.append(f"{rel}: docx read failed: {exc}")
            continue
        if not text:
            continue
        source_files.append(str(path.relative_to(ROOT)))
        sections.append(f"## DOCX: {rel.as_posix()}\n\n{text}")
    content = "\n\n".join(sections).strip() + "\n"
    return content, {
        "normalizer": "docx_bundle_text_v1",
        "source_files": source_files,
        "skipped_files": skipped,
    }


def normalize_docx_zip(record: dict[str, Any], zip_path: Path, max_files: int, max_file_bytes: int) -> tuple[str, dict[str, Any]]:
    sections = [content_with_header(record, "").strip()]
    source_files: list[str] = []
    skipped: list[str] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        docx_names = sorted(n for n in zf.namelist() if n.lower().endswith(".docx"))
        for name in docx_names[:max_files]:
            info = zf.getinfo(name)
            if info.file_size > max_file_bytes:
                skipped.append(f"{name}: {info.file_size} bytes exceeds cap")
                continue
            try:
                text = docx_bytes_to_text(zf.read(name))
            except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
                skipped.append(f"{name}: docx read failed: {exc}")
                continue
            if not text:
                continue
            source_files.append(f"{zip_path.relative_to(ROOT)}!{name}")
            sections.append(f"## DOCX: {name}\n\n{text}")
        if len(docx_names) > max_files:
            skipped.append(f"file cap reached at {max_files}")
    content = "\n\n".join(sections).strip() + "\n"
    return content, {
        "normalizer": "zip_docx_bundle_text_v1",
        "source_files": source_files,
        "skipped_files": skipped,
    }


def normalize_repo(record: dict[str, Any], extracted_to: Path, max_files: int, max_file_bytes: int) -> tuple[str, dict[str, Any]]:
    files, skipped = iter_repo_text_files(extracted_to, max_files, max_file_bytes)
    sections = [content_with_header(record, "").strip()]
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


def normalize_record(
    record: dict[str, Any],
    max_files: int,
    max_file_bytes: int,
    *,
    max_pdf_pages: int | None = None,
) -> dict[str, Any] | None:
    if record.get("status") != "ingested":
        return None

    raw_path = local_path(record)
    extracted_to = metadata_path(record, "extracted_to")
    if extracted_to and extracted_to.exists():
        content, meta = normalize_repo(record, extracted_to, max_files, max_file_bytes)
        if not meta.get("source_files"):
            content, meta = normalize_docx_bundle(record, extracted_to, max_files, max_file_bytes)
        if not meta.get("source_files") and raw_path and raw_path.exists() and raw_path.suffix.lower() == ".zip":
            content, meta = normalize_docx_zip(record, raw_path, max_files, max_file_bytes)
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
    elif raw_path and raw_path.exists() and raw_path.suffix.lower() in PDF_EXTENSIONS:
        if looks_like_html_file(raw_path):
            content, meta = normalize_page(record, raw_path)
            meta["normalizer"] = "html_text_v1"
            meta["note"] = "ingest_path_ended_in_pdf_but_content_is_html"
        else:
            try:
                content, meta = normalize_pdf_file(record, raw_path, max_pages=max_pdf_pages)
            except (OSError, ValueError) as exc:
                return {
                    "external_id": record.get("external_id"),
                    "source": record.get("source"),
                    "status": "skipped",
                    "reason": f"pdf_extract_failed: {exc}",
                    "raw_path": str(raw_path.relative_to(ROOT)),
                    "normalized_at": utc_now_iso(),
                }
            except Exception as exc:
                exc_name = type(exc).__module__ + "." + type(exc).__name__
                return {
                    "external_id": record.get("external_id"),
                    "source": record.get("source"),
                    "status": "skipped",
                    "reason": f"pdf_extract_failed: {exc_name}: {exc}",
                    "raw_path": str(raw_path.relative_to(ROOT)),
                    "normalized_at": utc_now_iso(),
                }
    elif raw_path and raw_path.exists() and raw_path.suffix.lower() in DOCX_EXTENSIONS:
        content, meta = normalize_docx_file(record, raw_path)
    elif raw_path and raw_path.exists() and raw_path.suffix.lower() == ".zip":
        content, meta = normalize_docx_zip(record, raw_path, max_files, max_file_bytes)
        if not meta.get("source_files"):
            return {
                "external_id": record.get("external_id"),
                "source": record.get("source"),
                "status": "skipped",
                "reason": "no_supported_text_files",
                "raw_path": str(raw_path.relative_to(ROOT)),
                "normalized_at": utc_now_iso(),
                "skipped_files": meta.get("skipped_files", []),
            }
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

    meta_record = record.get("metadata") or {}
    row: dict[str, Any] = {
        "external_id": record.get("external_id"),
        "source": record.get("source"),
        "authority": record.get("authority"),
        "title": record.get("title"),
        "category": record.get("category"),
        "tier": record.get("tier"),
        "core_structural_syntax": bool(meta_record.get("core_structural_syntax")),
        "ingest_kind": meta_record.get("ingest_kind"),
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
    for key in INTERNAL_CHUNK_META_KEYS:
        if key in meta_record:
            row[key] = meta_record[key]
    return row


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


def write_manifest_atomic(path: Path, manifest_rows: list[dict[str, Any]]) -> None:
    tmp_path = path.with_suffix(".json.tmp")
    payload = json.dumps(manifest_rows, indent=2, ensure_ascii=False) + "\n"
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, path)


def doc_summary_payload(result: SummaryResult, *, summarized_at: str) -> dict[str, Any]:
    return {
        "text": result.text,
        "model": result.model,
        "method": result.method,
        "input_chars": result.input_chars,
        "input_sha256": result.input_sha256,
        "summarized_at": summarized_at,
    }


def maybe_summarize(
    manifest_row: dict[str, Any],
    normalized_text: str,
    summarizer: Summarizer,
    *,
    force: bool = False,
    max_input_chars: int = 60_000,
    chunk_chars: int = 6_000,
    target_sentences: int = 3,
) -> dict[str, Any] | None:
    """Return doc_summary dict for manifest, or None if cache hit."""
    text_hash = sha256_text(normalized_text)
    meta = manifest_row.setdefault("metadata", {})
    existing = meta.get("doc_summary")
    if (
        not force
        and isinstance(existing, dict)
        and existing.get("input_sha256") == text_hash
        and existing.get("text")
    ):
        return None

    print(
        f"Summarizing {manifest_row.get('external_id')} "
        f"({len(normalized_text)} chars)..."
    )
    result = summarize_text(
        normalized_text,
        title=str(manifest_row.get("title") or "") or None,
        authority=str(manifest_row.get("authority") or "") or None,
        model_id=summarizer.model_id,
        max_input_chars=max_input_chars,
        chunk_chars=chunk_chars,
        target_sentences=target_sentences,
        summarizer=summarizer,
    )
    payload = doc_summary_payload(result, summarized_at=utc_now_iso())
    meta["doc_summary"] = payload
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize ingested standards artifacts into text for RAG indexing.")
    parser.add_argument(
        "--source",
        default="all",
        help="Source filter (iso|etsi|dvb|github|w3c|ietf|3gpp|ado_wiki|confluence|all)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Maximum ingested rows to process this run.")
    parser.add_argument("--max-files", type=int, default=200, help="Max text files to include from an extracted repo.")
    parser.add_argument("--max-file-kb", type=int, default=512, help="Max size per text file included from a repo.")
    parser.add_argument(
        "--max-pdf-pages",
        type=int,
        default=800,
        help="Max PDF pages to extract per file (0 = no limit).",
    )
    parser.add_argument(
        "--summarize",
        action="store_true",
        help="After normalization, summarize each doc with a local LLM and write metadata.doc_summary to the manifest.",
    )
    parser.add_argument(
        "--re-summarize",
        action="store_true",
        help="Force re-summarize even when input_sha256 matches an existing doc_summary.",
    )
    parser.add_argument(
        "--summary-model",
        default=DEFAULT_SUMMARY_MODEL,
        help=f"Hugging Face model id for --summarize (default: {DEFAULT_SUMMARY_MODEL}).",
    )
    parser.add_argument(
        "--summary-max-input-chars",
        type=int,
        default=60_000,
        help="Above this length, use map-reduce summarization (default: 60000).",
    )
    parser.add_argument(
        "--summary-target-sentences",
        type=int,
        default=3,
        help="Target sentence count for the final doc summary (default: 3).",
    )
    args = parser.parse_args()

    if not MANIFEST_PATH.exists():
        raise SystemExit("discovery_manifest.json not found. Run discover.py first.")

    records = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    selected = [r for r in records if r.get("status") == "ingested"]
    if args.source != "all":
        selected = [r for r in selected if r.get("source") == args.source]
    if args.limit is not None:
        selected = selected[: args.limit]

    max_pdf_pages = None if args.max_pdf_pages <= 0 else args.max_pdf_pages

    existing = load_existing_records(RECORDS_PATH)
    normalized = 0
    skipped = 0
    summarized = 0
    summary_skipped_cache = 0
    manifest_dirty = False
    summarizer: Summarizer | None = None
    if args.summarize:
        summarizer = get_summarizer(args.summary_model)

    for record in selected:
        row = normalize_record(
            record,
            args.max_files,
            args.max_file_kb * 1024,
            max_pdf_pages=max_pdf_pages,
        )
        if row is None:
            continue
        existing[(row.get("source"), row.get("external_id"))] = row
        if row.get("status") == "normalized":
            normalized += 1
            if args.summarize and summarizer is not None:
                norm_rel = row.get("normalized_path")
                if norm_rel:
                    norm_path = ROOT / str(norm_rel)
                    if norm_path.is_file():
                        normalized_text = norm_path.read_text(encoding="utf-8", errors="replace")
                        summary_payload = maybe_summarize(
                            record,
                            normalized_text,
                            summarizer,
                            force=args.re_summarize,
                            max_input_chars=args.summary_max_input_chars,
                            chunk_chars=6_000,
                            target_sentences=args.summary_target_sentences,
                        )
                        if summary_payload is not None:
                            summarized += 1
                            manifest_dirty = True
                        else:
                            summary_skipped_cache += 1
        else:
            skipped += 1

    write_records(RECORDS_PATH, existing)
    if manifest_dirty:
        write_manifest_atomic(MANIFEST_PATH, records)
    print(
        f"Normalization complete: normalized={normalized}, skipped={skipped}, "
        f"index={RECORDS_PATH.relative_to(ROOT)}"
    )
    if args.summarize:
        print(
            f"Summaries: written={summarized}, cache_skipped={summary_skipped_cache}, "
            f"manifest_updated={manifest_dirty}"
        )


if __name__ == "__main__":
    main()
