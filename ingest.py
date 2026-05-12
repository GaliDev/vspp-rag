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


PAGE_FILE_TYPES = {"portal", "html", "standard-page"}


def _should_skip_file_type(record: dict, include_pages: bool = False) -> bool:
    ft = record.get("file_type")
    if ft == "error":
        return True
    if ft in PAGE_FILE_TYPES:
        return not include_pages
    if ft == "repository" and record.get("source") == "github":
        return False
    if ft == "repository":
        return True
    return False


def ingest_record(record: dict, options: IngestOptions | None = None) -> dict:
    opts = options or IngestOptions()
    source = record["source"]
    authority_dir = record["authority"].lower().replace("/", "_")
    raw_dir = DATA_DIR / authority_dir / "raw"

    if _should_skip_file_type(record, opts.include_pages):
        return record

    if record.get("file_type") in PAGE_FILE_TYPES:
        target_path = raw_dir / safe_filename(f"{record['external_id']}.html")
        downloaded_path = download_file(record["remote_url"], target_path, opts.max_bytes)
        metadata = record.setdefault("metadata", {})
        metadata.pop("ingest_error", None)
        record["status"] = "ingested"
        record["ingested_at"] = utc_now_iso()
        record["local_path"] = str(downloaded_path.relative_to(ROOT))
        record["sha256"] = sha256_file(downloaded_path)
        metadata["ingest_kind"] = "page_snapshot"
        metadata["original_file_type"] = record.get("file_type")
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
        extract_dir = downloaded_path.parent / (downloaded_path.stem + "_extracted")
        try:
            safe_unzip(downloaded_path, extract_dir)
            metadata["extracted_to"] = str(extract_dir.relative_to(ROOT))
        except Exception as exc:
            metadata["extract_unzip_error"] = str(exc)
        return record

    parsed = urlparse(record["remote_url"])
    filename = safe_filename(Path(parsed.path).name or f"{record['external_id']}.bin")
    target_path = raw_dir / filename

    downloaded_path = download_file(record["remote_url"], target_path, opts.max_bytes)
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
    if record.get("status") == "ingested":
        return False
    if _should_skip_file_type(record, options.include_pages):
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

    to_process: list[dict] = []
    for r in selected:
        if not _ingestible(r, opts):
            continue
        to_process.append(r)
        if opts.limit is not None and len(to_process) >= opts.limit:
            break

    sem = asyncio.Semaphore(4)

    async def one(record: dict) -> None:
        async with sem:
            if record.get("status") == "ingested":
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
    parser.add_argument("--source", default="all", help="Source filter (ietf|3gpp|github|etsi|iso|dvb|cta|w3c|all)")
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
        "--include-pages",
        action="store_true",
        help="Also ingest lightweight HTML snapshots for html, portal, and standard-page records.",
    )
    args = parser.parse_args()

    if not MANIFEST_PATH.exists():
        raise SystemExit("discovery_manifest.json not found. Run discover.py first.")

    max_bytes = int(args.max_mb * 1024 * 1024) if args.max_mb is not None else None
    options = IngestOptions(max_bytes=max_bytes, limit=args.limit, include_pages=args.include_pages)

    records = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    source = "all" if args.all else args.source
    records = asyncio.run(ingest(records, source, options))
    MANIFEST_PATH.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"Ingestion complete for source={source} "
        f"(limit={args.limit}, max_mb={args.max_mb}, include_pages={args.include_pages})."
    )


if __name__ == "__main__":
    main()
