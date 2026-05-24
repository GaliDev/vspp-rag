from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from src.collectors.ado_wiki import fetch_page_markdown
from src.collectors.confluence import fetch_page_storage
from src.core.artifacts import (
    PAGE_FILE_TYPES,
    download_url,
    liaison_urls,
    pdf_url_matches_record,
    pick_best_per_authority,
    resolve_pdf_from_urls,
    should_skip_runtime_pdf_resolution,
)

ROOT = Path(__file__).parent
MANIFEST_PATH = ROOT / "discovery_manifest.json"
DATA_DIR = ROOT / "data"

CHUNK = 1024 * 1024
HTTP_HEADERS = {"User-Agent": "VSPP-Standards-Vault/1.0 (+https://github.com/GaliDev/vspp-rag)"}


@dataclass
class IngestOptions:
    """Lightweight guards: cap download size and number of new ingestions per run."""

    max_bytes: int | None = None
    limit: int | None = None
    include_pages: bool = False
    one_per_authority: bool = False
    prefer_artifacts: bool = True
    page_fallback: bool = True
    resolve_pdfs: bool = True
    reingest_ids: frozenset[str] = frozenset()
    only_external_ids: frozenset[str] = frozenset()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def fetch_page_text(url: str) -> str | None:
    try:
        req = Request(url, headers=HTTP_HEADERS)
        with urlopen(req, timeout=28) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _clear_ingest_state(record: dict) -> None:
    record["status"] = "discovered"
    for key in ("ingested_at", "local_path", "sha256"):
        record.pop(key, None)
    meta = record.setdefault("metadata", {})
    for key in (
        "ingest_kind",
        "artifact_url",
        "resolved_at_ingest",
        "ingest_error",
        "ingest_archive_url",
        "extracted_to",
        "extract_unzip_error",
    ):
        meta.pop(key, None)


def _resolve_runtime_pdf(record: dict, opts: IngestOptions) -> str | None:
    if not opts.resolve_pdfs or should_skip_runtime_pdf_resolution(record):
        return None
    meta = record.get("metadata") or {}
    artifact = meta.get("artifact_url")
    if artifact and pdf_url_matches_record(str(artifact), record):
        return str(artifact)
    seeds: list[str] = []
    portal = meta.get("portal_url") or record.get("remote_url")
    if portal:
        seeds.append(str(portal))
    deliver_first = sorted(
        liaison_urls(record),
        key=lambda u: (0 if "/deliver/" in u.lower() else 1, u),
    )
    for link in deliver_first:
        if not pdf_url_matches_record(link, record):
            continue
        if link not in seeds:
            seeds.append(link)
    return resolve_pdf_from_urls(seeds, fetch_page_text)


def _head_content_length(url: str) -> int | None:
    try:
        req = Request(url, headers=HTTP_HEADERS, method="HEAD")
        with urlopen(req, timeout=30) as resp:
            cl = resp.headers.get("Content-Length")
            return int(cl) if cl else None
    except Exception:
        return None


def download_file(remote_url: str, destination: Path, max_bytes: int | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(remote_url)
    if parsed.scheme in ("http", "https") and max_bytes is not None:
        cl = _head_content_length(remote_url)
        if cl is not None and cl > max_bytes:
            raise ValueError(f"Content-Length {cl} exceeds cap {max_bytes}")

    request = Request(remote_url, headers=HTTP_HEADERS) if parsed.scheme in ("http", "https") else remote_url
    with urlopen(request, timeout=120) as response, destination.open("wb") as out:
        total = 0
        while True:
            buf = response.read(CHUNK)
            if not buf:
                break
            total += len(buf)
            if max_bytes is not None and total > max_bytes:
                destination.unlink(missing_ok=True)
                raise ValueError(f"download exceeded max_bytes={max_bytes}")
            out.write(buf)
    return destination


def safe_unzip(
    zip_path: Path,
    dest_dir: Path,
    *,
    max_members: int = 4000,
    max_uncompressed_total: int = 800_000_000,
) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = dest_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > max_members:
            raise ValueError(f"zip member count {len(infos)} exceeds cap {max_members}")
        total_size = sum(i.file_size for i in infos)
        if total_size > max_uncompressed_total:
            raise ValueError("zip uncompressed total exceeds cap")
        for zi in infos:
            target = (dest_dir / zi.filename).resolve()
            try:
                target.relative_to(base)
            except ValueError as exc:
                raise ValueError(f"unsafe zip path: {zi.filename}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(zi, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _github_archive_url(record: dict) -> str | None:
    if record.get("source") != "github" or record.get("file_type") != "repository":
        return None
    ext = record.get("external_id") or ""
    if ext.startswith("error-") or "/" not in ext:
        return None
    owner, repo = ext.split("/", 1)
    if not owner or not repo:
        return None
    branch = record.get("version") or "main"
    branch = branch.replace("/", "_")
    return f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"


def _should_skip_file_type(
    record: dict,
    include_pages: bool = False,
    *,
    page_fallback: bool = False,
) -> bool:
    ft = record.get("file_type")
    meta = record.get("metadata") or {}
    if ft == "error":
        return True
    if meta.get("access") == "paywalled" and ft in PAGE_FILE_TYPES:
        return True
    if ft in PAGE_FILE_TYPES:
        return not include_pages and not page_fallback
    if ft == "repository" and record.get("source") == "github":
        return False
    if ft == "repository":
        return True
    return False


def ingest_local_artifact(record: dict, local_path: Path, options: IngestOptions | None = None) -> dict:
    """Copy a local PDF/zip/html file into raw storage for paywalled or admin-provided specs."""
    opts = options or IngestOptions()
    path = Path(local_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    authority_dir = record["authority"].lower().replace("/", "_")
    raw_dir = DATA_DIR / authority_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower() or ".bin"
    target_path = raw_dir / safe_filename(f"{record['external_id']}{suffix}")
    shutil.copy2(path, target_path)

    metadata = record.setdefault("metadata", {})
    metadata.pop("ingest_error", None)
    record["status"] = "ingested"
    record["ingested_at"] = utc_now_iso()
    record["local_path"] = str(target_path.relative_to(ROOT))
    record["sha256"] = sha256_file(target_path)
    metadata["ingest_kind"] = "local_upload"
    metadata["local_artifact_source"] = str(path)

    if suffix == ".pdf":
        metadata["artifact_url"] = f"file://{path}"
        if not target_path.read_bytes()[:5].startswith(b"%PDF"):
            raise ValueError(f"local file is not a PDF: {path}")
    elif suffix == ".zip":
        extract_dir = target_path.parent / target_path.stem
        try:
            safe_unzip(target_path, extract_dir)
            metadata["extracted_to"] = str(extract_dir.relative_to(ROOT))
        except Exception as exc:
            metadata["extract_unzip_error"] = str(exc)

    return record


def ingest_ado_wiki_record(record: dict) -> dict:
    content, etag = fetch_page_markdown(record)
    authority_dir = record["authority"].lower().replace("/", "_")
    raw_dir = DATA_DIR / authority_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    target_path = raw_dir / safe_filename(f"{record['external_id']}.md")
    target_path.write_text(content, encoding="utf-8")

    metadata = record.setdefault("metadata", {})
    metadata.pop("ingest_error", None)
    if etag:
        metadata["ado_wiki_etag"] = etag
    record["status"] = "ingested"
    record["ingested_at"] = utc_now_iso()
    record["local_path"] = str(target_path.relative_to(ROOT))
    record["sha256"] = sha256_file(target_path)
    metadata["ingest_kind"] = "ado_wiki_markdown"
    return record


def ingest_confluence_record(record: dict) -> dict:
    content, version_number = fetch_page_storage(record)
    authority_dir = record["authority"].lower().replace("/", "_")
    raw_dir = DATA_DIR / authority_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    target_path = raw_dir / safe_filename(f"{record['external_id']}.html")
    target_path.write_text(content, encoding="utf-8")

    metadata = record.setdefault("metadata", {})
    metadata.pop("ingest_error", None)
    if version_number is not None:
        metadata["content_version"] = version_number
        record["version"] = str(version_number)
    record["status"] = "ingested"
    record["ingested_at"] = utc_now_iso()
    record["local_path"] = str(target_path.relative_to(ROOT))
    record["sha256"] = sha256_file(target_path)
    metadata["ingest_kind"] = "confluence_storage_html"
    return record


def ingest_record(record: dict, options: IngestOptions | None = None) -> dict:
    opts = options or IngestOptions()
    source = record["source"]
    authority_dir = record["authority"].lower().replace("/", "_")
    raw_dir = DATA_DIR / authority_dir / "raw"

    if source == "ado_wiki" and record.get("file_type") == "markdown":
        return ingest_ado_wiki_record(record)
    if source == "confluence" and record.get("file_type") == "html":
        return ingest_confluence_record(record)

    ext = str(record.get("external_id") or "")
    allow_page_fallback = opts.page_fallback and (
        opts.one_per_authority or ext in opts.reingest_ids
    )
    if _should_skip_file_type(
        record,
        opts.include_pages,
        page_fallback=allow_page_fallback,
    ):
        return record

    gh_url = _github_archive_url(record)
    if gh_url:
        ext = record["external_id"].replace("/", "_")
        branch = (record.get("version") or "main").replace("/", "_")
        target_path = raw_dir / safe_filename(f"{ext}_{branch}_archive.zip")
        downloaded_path = download_file(gh_url, target_path, opts.max_bytes)
        metadata = record.setdefault("metadata", {})
        metadata.pop("ingest_error", None)
        record["status"] = "ingested"
        record["ingested_at"] = utc_now_iso()
        record["local_path"] = str(downloaded_path.relative_to(ROOT))
        record["sha256"] = sha256_file(downloaded_path)
        metadata["ingest_archive_url"] = gh_url
        metadata["ingest_kind"] = "repository_archive"
        extract_dir = downloaded_path.parent / (downloaded_path.stem + "_extracted")
        try:
            safe_unzip(downloaded_path, extract_dir)
            metadata["extracted_to"] = str(extract_dir.relative_to(ROOT))
        except Exception as exc:
            metadata["extract_unzip_error"] = str(exc)
        return record

    runtime_pdf = _resolve_runtime_pdf(record, opts)
    if runtime_pdf:
        target_path = raw_dir / safe_filename(f"{record['external_id']}.pdf")
        downloaded_path = download_file(runtime_pdf, target_path, opts.max_bytes)
        if not downloaded_path.read_bytes()[:5].startswith(b"%PDF"):
            downloaded_path.unlink(missing_ok=True)
            raise ValueError(f"resolved URL is not a PDF: {runtime_pdf}")
        metadata = record.setdefault("metadata", {})
        metadata.pop("ingest_error", None)
        record["status"] = "ingested"
        record["ingested_at"] = utc_now_iso()
        record["local_path"] = str(downloaded_path.relative_to(ROOT))
        record["sha256"] = sha256_file(downloaded_path)
        metadata["ingest_kind"] = "pdf_artifact"
        metadata["artifact_url"] = runtime_pdf
        metadata["resolved_at_ingest"] = True
        return record

    remote = download_url(record)

    if record.get("file_type") in PAGE_FILE_TYPES:
        target_path = raw_dir / safe_filename(f"{record['external_id']}.html")
        downloaded_path = download_file(remote, target_path, opts.max_bytes)
        metadata = record.setdefault("metadata", {})
        metadata.pop("ingest_error", None)
        record["status"] = "ingested"
        record["ingested_at"] = utc_now_iso()
        record["local_path"] = str(downloaded_path.relative_to(ROOT))
        record["sha256"] = sha256_file(downloaded_path)
        metadata["ingest_kind"] = "page_snapshot"
        metadata["original_file_type"] = record.get("file_type")
        return record

    if record.get("file_type") == "txt":
        target_path = raw_dir / safe_filename(f"{record['external_id']}.txt")
        downloaded_path = download_file(remote, target_path, opts.max_bytes)
        metadata = record.setdefault("metadata", {})
        metadata.pop("ingest_error", None)
        record["status"] = "ingested"
        record["ingested_at"] = utc_now_iso()
        record["local_path"] = str(downloaded_path.relative_to(ROOT))
        record["sha256"] = sha256_file(downloaded_path)
        metadata["ingest_kind"] = "text_artifact"
        return record

    parsed = urlparse(remote)
    filename = safe_filename(Path(parsed.path).name or f"{record['external_id']}.bin")
    target_path = raw_dir / filename

    downloaded_path = download_file(remote, target_path, opts.max_bytes)
    record.setdefault("metadata", {}).pop("ingest_error", None)
    record["status"] = "ingested"
    record["ingested_at"] = utc_now_iso()
    record["local_path"] = str(downloaded_path.relative_to(ROOT))
    record["sha256"] = sha256_file(downloaded_path)

    if downloaded_path.suffix.lower() == ".zip":
        extract_dir = downloaded_path.parent / downloaded_path.stem
        try:
            safe_unzip(downloaded_path, extract_dir)
            docx_files = [str(p.relative_to(ROOT)) for p in extract_dir.rglob("*.docx")]
            if docx_files:
                record.setdefault("metadata", {})["docx_files"] = docx_files
            if source == "3gpp" or source == "github":
                record.setdefault("metadata", {})["extracted_to"] = str(extract_dir.relative_to(ROOT))
        except Exception as exc:
            record.setdefault("metadata", {})["extract_unzip_error"] = str(exc)

    return record


def _ingestible(record: dict, options: IngestOptions) -> bool:
    if str(record.get("external_id") or "") in options.reingest_ids:
        return True
    if record.get("status") == "ingested":
        return False
    if _should_skip_file_type(
        record,
        options.include_pages,
        page_fallback=options.page_fallback and options.one_per_authority,
    ):
        return False
    if _github_archive_url(record):
        return True
    return True


async def ingest(
    records: list[dict],
    source: str | None,
    options: IngestOptions | None = None,
) -> list[dict]:
    opts = options or IngestOptions()
    selected = records
    if source and source != "all":
        selected = [r for r in records if r["source"] == source]

    if opts.only_external_ids:
        selected = [r for r in selected if str(r.get("external_id") or "") in opts.only_external_ids]
        to_process = list(selected)
        if opts.limit is not None:
            to_process = to_process[: opts.limit]
    elif opts.one_per_authority:
        to_process = pick_best_per_authority(
            selected,
            include_pages=opts.include_pages,
            page_fallback=opts.page_fallback,
        )
        if opts.reingest_ids:
            seen = {str(r.get("external_id")) for r in to_process}
            for record in selected:
                ext = str(record.get("external_id") or "")
                if ext in opts.reingest_ids and ext not in seen:
                    to_process.append(record)
                    seen.add(ext)
        if opts.limit is not None:
            to_process = to_process[: opts.limit]
    else:
        candidates = [r for r in selected if _ingestible(r, opts)]
        to_process = list(candidates)
        if opts.reingest_ids:
            seen = {str(r.get("external_id")) for r in to_process}
            for record in selected:
                ext = str(record.get("external_id") or "")
                if ext in opts.reingest_ids and ext not in seen:
                    to_process.append(record)
                    seen.add(ext)
        if opts.limit is not None:
            to_process = to_process[: opts.limit]

    sem = asyncio.Semaphore(4)

    reingest = opts.reingest_ids

    async def one(record: dict) -> None:
        async with sem:
            ext = str(record.get("external_id") or "")
            if ext in reingest:
                _clear_ingest_state(record)
            elif record.get("status") == "ingested":
                return
            try:
                updated = await asyncio.to_thread(ingest_record, record, opts)
                record.update(updated)
            except Exception as exc:
                record.setdefault("metadata", {})["ingest_error"] = str(exc)

    await asyncio.gather(*(one(r) for r in to_process))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Targeted ingestion for discovered records.")
    parser.add_argument(
        "--source",
        default="all",
        help="Source filter (ietf|3gpp|github|etsi|iso|dvb|cta|w3c|ado_wiki|confluence|all)",
    )
    parser.add_argument("--all", action="store_true", help="Ingest all sources")
    parser.add_argument(
        "--max-mb",
        type=float,
        default=None,
        help="Max download size per file (MB). Uses HEAD Content-Length when available; streaming enforces cap.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of newly ingestible rows to process this run (stable order within filter).",
    )
    parser.add_argument(
        "--one-per-authority",
        action="store_true",
        help="Ingest at most one new row per manifest authority. Prefers artifacts (pdf/txt/zip/repository); "
        "falls back to portal HTML when no artifact exists (disable with --no-page-fallback).",
    )
    parser.add_argument(
        "--include-pages",
        dest="include_pages",
        action="store_true",
        help="Treat portal/html pages as equal candidates (not only fallback when no artifact).",
    )
    parser.add_argument(
        "--no-page-fallback",
        action="store_true",
        help="With --one-per-authority, do not ingest portal HTML when no artifact exists.",
    )
    parser.add_argument(
        "--no-resolve-pdfs",
        action="store_true",
        help="Skip runtime PDF resolution (ETSI deliver crawl) at ingest time.",
    )
    parser.add_argument(
        "--reingest",
        nargs="+",
        metavar="EXTERNAL_ID",
        help="Reset ingest state and re-download for these manifest external_ids.",
    )
    parser.add_argument(
        "--local-artifact",
        type=Path,
        default=None,
        help="Copy a local PDF/zip file into raw storage (requires --external-id).",
    )
    parser.add_argument(
        "--external-id",
        default=None,
        help="Manifest external_id: ingest only this row, or pair with --local-artifact.",
    )
    args = parser.parse_args()

    include_pages = args.include_pages

    if not MANIFEST_PATH.exists():
        raise SystemExit("discovery_manifest.json not found. Run discover.py first.")

    if args.local_artifact is not None:
        if not args.external_id:
            raise SystemExit("--local-artifact requires --external-id")
        records = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        match = next((r for r in records if r.get("external_id") == args.external_id), None)
        if not match:
            raise SystemExit(f"No manifest row with external_id={args.external_id!r}")
        max_bytes = int(args.max_mb * 1024 * 1024) if args.max_mb is not None else None
        opts = IngestOptions(max_bytes=max_bytes)
        ingest_local_artifact(match, args.local_artifact, opts)
        MANIFEST_PATH.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Local artifact ingested for {args.external_id} -> {match.get('local_path')}")
        return

    max_bytes = int(args.max_mb * 1024 * 1024) if args.max_mb is not None else None
    only_external_ids: frozenset[str] = frozenset()
    if args.external_id:
        only_external_ids = frozenset([args.external_id])
    options = IngestOptions(
        max_bytes=max_bytes,
        limit=args.limit,
        include_pages=include_pages,
        one_per_authority=args.one_per_authority,
        prefer_artifacts=True,
        page_fallback=not args.no_page_fallback,
        resolve_pdfs=not args.no_resolve_pdfs,
        reingest_ids=frozenset(args.reingest or ()),
        only_external_ids=only_external_ids,
    )

    records = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    source = "all" if args.all else args.source
    records = asyncio.run(ingest(records, source, options))
    MANIFEST_PATH.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    target_msg = f", external_id={args.external_id!r}" if args.external_id else ""
    print(
        f"Ingestion complete for source={source}{target_msg} "
        f"(limit={args.limit}, max_mb={args.max_mb}, include_pages={include_pages}, "
        f"one_per_authority={args.one_per_authority}, page_fallback={not args.no_page_fallback}, "
        f"resolve_pdfs={not args.no_resolve_pdfs})."
    )


if __name__ == "__main__":
    main()
