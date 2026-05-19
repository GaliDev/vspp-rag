from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.core.io import load_manifest

ROOT = Path(__file__).parent
MANIFEST_PATH = ROOT / "discovery_manifest.json"
RECORDS_PATH = ROOT / "data" / "normalized" / "records.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ingested_keys(manifest: list[dict]) -> set[tuple[str | None, str | None]]:
    return {
        (r.get("source"), r.get("external_id"))
        for r in manifest
        if r.get("status") == "ingested" and r.get("external_id")
    }


def write_records(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda r: (str(r.get("source")), str(r.get("external_id"))))
    with path.open("w", encoding="utf-8") as f:
        for row in ordered:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def prune_records(
    records: list[dict],
    allowed: set[tuple[str | None, str | None]],
) -> tuple[list[dict], list[dict]]:
    kept: list[dict] = []
    removed: list[dict] = []
    for row in records:
        key = (row.get("source"), row.get("external_id"))
        if key in allowed:
            kept.append(row)
        else:
            removed.append(row)
    return kept, removed


def delete_orphan_normalized_files(removed: list[dict], *, dry_run: bool) -> list[Path]:
    deleted: list[Path] = []
    for row in removed:
        rel = row.get("normalized_path")
        if not rel:
            continue
        path = ROOT / str(rel)
        if not path.is_file():
            continue
        if dry_run:
            deleted.append(path)
        else:
            path.unlink()
            deleted.append(path)
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile normalized records.jsonl with discovery_manifest ingested rows.",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Remove records.jsonl rows not ingested in manifest (and optional orphan .txt files).",
    )
    parser.add_argument(
        "--delete-orphan-txt",
        action="store_true",
        help="With --prune, delete normalized .txt files for removed rows.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing.")
    args = parser.parse_args()

    if not MANIFEST_PATH.is_file():
        raise SystemExit(f"Missing {MANIFEST_PATH}. Run discover.py first.")

    manifest = load_manifest(MANIFEST_PATH)
    allowed = ingested_keys(manifest)
    records = load_jsonl(RECORDS_PATH)

    stale = [r for r in records if (r.get("source"), r.get("external_id")) not in allowed]
    missing_from_records = [
        r
        for r in manifest
        if r.get("status") == "ingested"
        and (r.get("source"), r.get("external_id")) not in {(x.get("source"), x.get("external_id")) for x in records}
    ]

    print(f"Manifest ingested: {len(allowed)}")
    print(f"records.jsonl rows: {len(records)}")
    print(f"Stale rows (not ingested in manifest): {len(stale)}")
    print(f"Ingested in manifest but absent from records.jsonl: {len(missing_from_records)}")

    if not args.prune:
        if stale:
            print("\nRun with --prune to remove stale rows from records.jsonl.")
        return

    kept, removed = prune_records(records, allowed)
    orphan_paths: list[Path] = []
    if args.delete_orphan_txt and removed:
        orphan_paths = delete_orphan_normalized_files(removed, dry_run=args.dry_run)

    if args.dry_run:
        print(f"\nDry run: would keep {len(kept)} rows, remove {len(removed)} rows.")
        if orphan_paths:
            print(f"Would delete {len(orphan_paths)} orphan normalized file(s).")
        return

    write_records(RECORDS_PATH, kept)
    print(f"\nPruned records.jsonl: kept={len(kept)}, removed={len(removed)}")
    if orphan_paths:
        print(f"Deleted {len(orphan_paths)} orphan normalized file(s).")


if __name__ == "__main__":
    main()
