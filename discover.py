from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import logging

from src.collectors import (
    discover_3gpp,
    discover_ado_wiki,
    discover_confluence,
    discover_github,
    discover_ietf,
    discover_structural_system,
    discover_webdrafts,
)
from src.core.catalog import write_pm_catalog
from src.core.io import load_manifest, merge_discovery_preserving_ingest, save_manifest


ROOT = Path(__file__).parent
MANIFEST_PATH = ROOT / "discovery_manifest.json"
CATALOG_PATH = ROOT / "PM_Catalog.md"


async def run_discovery(existing_by_id: dict[str, dict] | None = None) -> list[dict]:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    results = await asyncio.gather(
        discover_ietf(),
        discover_3gpp(),
        discover_github(),
        discover_webdrafts(),
        discover_structural_system(),
        discover_ado_wiki(existing_by_id),
        discover_confluence(existing_by_id),
    )
    records = []
    for bucket in results:
        for record in bucket:
            payload = record.to_dict()
            payload["status"] = "discovered"
            records.append(payload)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Run standards discovery phase.")
    parser.parse_args()
    existing = load_manifest(MANIFEST_PATH) if MANIFEST_PATH.exists() else []
    existing_by_id = {str(r.get("external_id")): r for r in existing if r.get("external_id")}
    records = asyncio.run(run_discovery(existing_by_id))
    if existing:
        records = merge_discovery_preserving_ingest(records, existing)
    save_manifest(MANIFEST_PATH, records)
    write_pm_catalog(records, CATALOG_PATH)
    unique_ids = {r.get("external_id") for r in records if r.get("external_id")}
    core_n = sum(1 for r in records if r.get("metadata", {}).get("core_structural_syntax"))
    ado_n = sum(1 for r in records if r.get("source") == "ado_wiki")
    confluence_n = sum(1 for r in records if r.get("source") == "confluence")
    print(f"Discovery completed: {len(records)} records written to {MANIFEST_PATH}")
    print(f"Unique standards (by external_id): {len(unique_ids)}")
    print(f"Core Structural Syntax entries: {core_n}")
    print(f"ADO wiki pages: {ado_n}")
    print(f"Confluence pages: {confluence_n}")
    print(f"Catalog generated at {CATALOG_PATH}")


if __name__ == "__main__":
    main()

