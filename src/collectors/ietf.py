from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from src.core.artifacts import ietf_text_artifact_url
from src.core.models import DiscoveryRecord

IETF_API = "https://datatracker.ietf.org/api/v1/doc/document/"
IETF_DOC_PAGE = "https://datatracker.ietf.org/doc/{name}/"
HTTP_HEADERS = {"User-Agent": "VSPP-Standards-Vault/1.0 (+https://github.com/GaliDev/vspp-rag)"}


def _url_exists(url: str) -> bool:
    try:
        req = Request(url, headers=HTTP_HEADERS, method="HEAD")
        with urlopen(req, timeout=20) as resp:
            return resp.status < 400
    except (HTTPError, URLError, TimeoutError):
        return False


def _resolve_ietf_artifact(doc: dict) -> tuple[str | None, str]:
    """Return (artifact_url, file_type) preferring verified plain-text, else datatracker HTML."""
    name = doc.get("name", "")
    candidates: list[str] = []
    primary = ietf_text_artifact_url(doc)
    if primary:
        candidates.append(primary)
    rev = doc.get("rev")
    if rev and primary and not primary.endswith(f"-{rev}.txt"):
        candidates.append(f"https://www.ietf.org/archive/id/{name}-{rev}.txt")
    if name:
        candidates.append(f"https://www.ietf.org/archive/id/{name}.txt")
    for url in candidates:
        if _url_exists(url):
            return url, "txt"
    return IETF_DOC_PAGE.format(name=name), "html"


def _fetch_topic(topic: str) -> list[dict]:
    url = f"{IETF_API}?name__icontains={quote(topic)}&limit=10"
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
                    name = doc.get("name", "unknown")
                    artifact, file_type = _resolve_ietf_artifact(doc)
                    meta: dict = {"topic": topic, "datatracker_url": IETF_DOC_PAGE.format(name=name)}
                    extra_meta: dict = {"portal_url": IETF_DOC_PAGE.format(name=name)}
                    if file_type == "txt":
                        extra_meta["artifact_url"] = artifact
                    else:
                        extra_meta["note"] = "text_artifact_unavailable_using_datatracker_html"
                    records.append(
                        DiscoveryRecord(
                            source="ietf",
                            authority="IETF",
                            title=doc.get("title", f"IETF {name}"),
                            external_id=name,
                            version=doc.get("rev"),
                            published=doc.get("time"),
                            remote_url=artifact or IETF_DOC_PAGE.format(name=name),
                            file_type=file_type,
                            category="Transport",
                            tier="transport-level",
                            metadata={**meta, **extra_meta},
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
                            remote_url=IETF_API,
                            file_type="error",
                            category="Transport",
                            tier="transport-level",
                            metadata={"error": "request_failed"},
                        )
                    )
                await asyncio.sleep(1.5 * (attempt + 1))

    await asyncio.gather(*(gather_topic(topic) for topic in topics))
    return records
