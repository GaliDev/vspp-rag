from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).parent
MANIFEST_PATH = ROOT / "discovery_manifest.json"
DATA_DIR = ROOT / "data"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def download_file(remote_url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(remote_url, timeout=60) as response, destination.open("wb") as out:
        shutil.copyfileobj(response, out)
    return destination


def ingest_record(record: dict) -> dict:
    source = record["source"]
    authority_dir = record["authority"].lower().replace("/", "_")
    raw_dir = DATA_DIR / authority_dir / "raw"
    parsed = urlparse(record["remote_url"])
    filename = safe_filename(Path(parsed.path).name or f"{record['external_id']}.bin")
    target_path = raw_dir / filename

    if record["file_type"] in {"portal", "repository", "html", "error", "standard-page"}:
        return record

    downloaded_path = download_file(record["remote_url"], target_path)
    record["status"] = "ingested"
    record["ingested_at"] = utc_now_iso()
    record["local_path"] = str(downloaded_path.relative_to(ROOT))
    record["sha256"] = sha256_file(downloaded_path)

    if source == "3gpp" and downloaded_path.suffix.lower() == ".zip":
        extract_dir = downloaded_path.parent / downloaded_path.stem
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(downloaded_path, "r") as archive:
            archive.extractall(extract_dir)
        docx_files = [str(p.relative_to(ROOT)) for p in extract_dir.rglob("*.docx")]
        if docx_files:
            record["metadata"]["docx_files"] = docx_files

    return record


async def ingest(records: list[dict], source: str | None) -> list[dict]:
    selected = records
    if source and source != "all":
        selected = [r for r in records if r["source"] == source]

    sem = asyncio.Semaphore(4)

    async def one(record: dict) -> None:
        async with sem:
            if record["status"] == "ingested":
                return
            try:
                updated = await asyncio.to_thread(ingest_record, record)
                record.update(updated)
            except Exception as exc:
                record["metadata"]["ingest_error"] = str(exc)

    await asyncio.gather(*(one(r) for r in selected))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Targeted ingestion for discovered records.")
    parser.add_argument("--source", default="all", help="Source to ingest (ietf|3gpp|github|etsi|iso|all)")
    parser.add_argument("--all", action="store_true", help="Ingest all sources")
    args = parser.parse_args()

    if not MANIFEST_PATH.exists():
        raise SystemExit("discovery_manifest.json not found. Run discover.py first.")

    records = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    source = "all" if args.all else args.source
    records = asyncio.run(ingest(records, source))
    MANIFEST_PATH.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Ingestion complete for source={source}.")


if __name__ == "__main__":
    main()

