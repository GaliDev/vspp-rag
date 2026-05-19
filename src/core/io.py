from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import DiscoveryRecord


def load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, records: Iterable[DiscoveryRecord | dict]) -> None:
    payload = [r.to_dict() if isinstance(r, DiscoveryRecord) else r for r in records]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _discovery_fingerprint(record: dict) -> tuple:
    meta = record.get("metadata") or {}
    base = (
        record.get("remote_url"),
        record.get("file_type"),
        meta.get("artifact_url"),
    )
    if record.get("source") == "ado_wiki":
        return base + (meta.get("ado_wiki_etag"),)
    return base


def merge_discovery_preserving_ingest(
    new_records: list[dict],
    existing_records: list[dict],
) -> list[dict]:
    """Re-discovery updates metadata; preserve ingest when the resolved artifact is unchanged."""
    old_by_id = {str(r.get("external_id")): r for r in existing_records if r.get("external_id")}
    ingest_keys = ("status", "ingested_at", "local_path", "sha256")
    ingest_meta_keys = (
        "ingest_kind",
        "artifact_url",
        "resolved_at_ingest",
        "ingest_archive_url",
        "extracted_to",
        "extract_unzip_error",
        "ingest_error",
        "ado_wiki_etag",
    )
    merged: list[dict] = []
    for rec in new_records:
        eid = str(rec.get("external_id") or "")
        old = old_by_id.get(eid)
        if old and old.get("status") == "ingested" and _discovery_fingerprint(rec) == _discovery_fingerprint(old):
            for key in ingest_keys:
                if key in old:
                    rec[key] = old[key]
            new_meta = dict(rec.get("metadata") or {})
            old_meta = old.get("metadata") or {}
            for key in ingest_meta_keys:
                if key in old_meta:
                    new_meta[key] = old_meta[key]
            rec["metadata"] = new_meta
        merged.append(rec)
    return merged

