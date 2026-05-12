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

