from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

from src.core.models import DiscoveryRecord

IETF_BASE = "https://datatracker.ietf.org/api/v1/doc/document/"


def _fetch_topic(topic: str) -> list[dict]:
    url = f"{IETF_BASE}?name__icontains={quote(topic)}&limit=10"
    with urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8")).get("objects", [])


async def discover_ietf() -> list[DiscoveryRecord]:
    topics = ["hls", "dash", "quic", "rtp", "webrtc"]
    records: list[DiscoveryRecord] = []

    async def gather_topic(topic: str) -> None:
        retries = 2
        for attempt in range(retries + 1):
            try:
                objects = await asyncio.to_thread(_fetch_topic, topic)
                for doc in objects:
                    records.append(
                        DiscoveryRecord(
                            source="ietf",
                            authority="IETF",
                            title=doc.get("title", f"IETF {doc.get('name', 'unknown')}"),
                            external_id=doc.get("name", "unknown"),
                            version=doc.get("rev"),
                            published=doc.get("time"),
                            remote_url=f"https://datatracker.ietf.org/doc/{doc.get('name', '')}/",
                            file_type="html",
                            category="Transport",
                            tier="transport-level",
                            metadata={"topic": topic},
                        )
                    )
                return
            except (HTTPError, URLError, TimeoutError):
                if attempt == retries:
                    records.append(
                        DiscoveryRecord(
                            source="ietf",
                            authority="IETF",
                            title=f"Discovery error for topic '{topic}'",
                            external_id=f"error-{topic}",
                            version=None,
                            published=None,
                            remote_url=IETF_BASE,
                            file_type="error",
                            category="Transport",
                            tier="transport-level",
                            metadata={"error": "request_failed"},
                        )
                    )
                await asyncio.sleep(1.5 * (attempt + 1))

    await asyncio.gather(*(gather_topic(topic) for topic in topics))
    return records

