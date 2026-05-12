from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DiscoveryRecord:
    source: str
    authority: str
    title: str
    external_id: str
    version: str | None
    published: str | None
    remote_url: str
    file_type: str
    category: str = "Transport"
    tier: str = "transport-level"
    publication_status: str | None = None
    status: str = "discovered"
    discovered_at: str = field(default_factory=utc_now_iso)
    ingested_at: str | None = None
    local_path: str | None = None
    sha256: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
