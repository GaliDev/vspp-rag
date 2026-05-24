from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests

from src.core.models import DiscoveryRecord

logger = logging.getLogger(__name__)

INTERNAL_CATEGORY = "Internal"
SYSTEM_TIER = "system-level"
DEFAULT_BASE_URL = "http://10.65.130.11:8090"
DEFAULT_SPACES = ("NGGUI", "VP", "PM")


@dataclass(frozen=True)
class ConfluenceConfig:
    base_url: str
    user: str
    password: str
    spaces: tuple[str, ...]


def load_confluence_config() -> ConfluenceConfig | None:
    user = os.environ.get("CONFLUENCE_USER", "").strip()
    password = os.environ.get("CONFLUENCE_PASSWORD", "").strip()
    if not user or not password:
        return None
    base_url = os.environ.get("CONFLUENCE_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    raw_spaces = os.environ.get("CONFLUENCE_SPACES", ",".join(DEFAULT_SPACES))
    spaces = tuple(s.strip() for s in raw_spaces.split(",") if s.strip())
    if not spaces:
        spaces = DEFAULT_SPACES
    return ConfluenceConfig(
        base_url=base_url or DEFAULT_BASE_URL,
        user=user,
        password=password,
        spaces=spaces,
    )


def external_id(space_key: str, page_id: str) -> str:
    return f"confluence:{space_key}:{page_id}"


class ConfluenceClient:
    def __init__(self, config: ConfluenceConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.auth = (config.user, config.password)
        self.session.headers.update(
            {"Accept": "application/json", "User-Agent": "VSPP-Standards-Vault/1.0"}
        )

    def _url(self, suffix: str) -> str:
        return f"{self.config.base_url}/rest/api/{suffix.lstrip('/')}"

    def get_json(self, suffix: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(self._url(suffix), params=params, timeout=60)
        if response.status_code == 429:
            raise RuntimeError("Confluence rate limited (429)")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Confluence response was not a JSON object")
        return payload

    def search_pages(self, space_key: str, *, start: int = 0, limit: int = 50) -> dict[str, Any]:
        return self.get_json(
            "content/search",
            params={
                "cql": f"type=page AND space={space_key}",
                "limit": limit,
                "start": start,
                "expand": "version,space",
            },
        )

    def get_page(self, page_id: str, *, include_body: bool = False) -> dict[str, Any]:
        expand = "version,space,ancestors"
        if include_body:
            expand = "body.storage," + expand
        return self.get_json(f"content/{page_id}", params={"expand": expand})


def _remote_url(base_url: str, page: dict[str, Any]) -> str:
    links = page.get("_links") or {}
    webui = str(links.get("webui") or "")
    if webui:
        return urljoin(base_url + "/", webui.lstrip("/"))
    return str(links.get("self") or "")


def _version_number(page: dict[str, Any]) -> int | None:
    version = page.get("version") or {}
    number = version.get("number")
    if isinstance(number, int):
        return number
    try:
        return int(str(number))
    except (TypeError, ValueError):
        return None


def _discovery_record(base_url: str, page: dict[str, Any]) -> DiscoveryRecord:
    space = page.get("space") or {}
    space_key = str(space.get("key") or "")
    page_id = str(page.get("id") or "")
    version = page.get("version") or {}
    version_number = _version_number(page)
    return DiscoveryRecord(
        source="confluence",
        authority=f"Confluence/{space_key}",
        title=str(page.get("title") or f"Confluence page {page_id}"),
        external_id=external_id(space_key, page_id),
        version=str(version_number) if version_number is not None else None,
        published=version.get("when"),
        remote_url=_remote_url(base_url, page),
        file_type="html",
        category=INTERNAL_CATEGORY,
        tier=SYSTEM_TIER,
        metadata={
            "space_key": space_key,
            "page_id": page_id,
            "content_version": version_number,
        },
    )


def _record_from_manifest(old: dict[str, Any]) -> DiscoveryRecord:
    fields = DiscoveryRecord.__dataclass_fields__
    kwargs = {k: old[k] for k in fields if k in old}
    kwargs.setdefault("metadata", dict(old.get("metadata") or {}))
    return DiscoveryRecord(**kwargs)


def _unchanged(existing: dict[str, Any], version_number: int | None) -> bool:
    if version_number is None:
        return False
    old_meta = existing.get("metadata") or {}
    return old_meta.get("content_version") == version_number


async def discover_confluence(
    existing_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[DiscoveryRecord]:
    config = load_confluence_config()
    if config is None:
        logger.warning("CONFLUENCE_USER/PASSWORD not set; skipping confluence discovery")
        return []

    existing = existing_by_id or {}
    client = ConfluenceClient(config)
    records: list[DiscoveryRecord] = []

    async def discover_space(space_key: str) -> None:
        start = 0
        limit = 50
        while True:
            try:
                payload = await asyncio.to_thread(
                    client.search_pages,
                    space_key,
                    start=start,
                    limit=limit,
                )
            except requests.HTTPError as exc:
                logger.error("Confluence search failed for %s: %s", space_key, exc)
                return
            except Exception as exc:
                logger.error("Confluence search failed for %s: %s", space_key, exc)
                return

            results = list(payload.get("results") or [])
            for page in results:
                if not isinstance(page, dict):
                    continue
                page_id = str(page.get("id") or "")
                eid = external_id(space_key, page_id)
                old = existing.get(eid)
                version_number = _version_number(page)
                if old and _unchanged(old, version_number):
                    records.append(_record_from_manifest(old))
                else:
                    records.append(_discovery_record(config.base_url, page))

            if not results or len(results) < limit:
                break
            start += limit

    await asyncio.gather(*(discover_space(space_key) for space_key in config.spaces))
    logger.info("confluence discovery: %d pages", len(records))
    return records


def fetch_page_storage(record: dict[str, Any]) -> tuple[str, int | None]:
    """Fetch Confluence storage XHTML for ingest. Requires Confluence credentials."""
    config = load_confluence_config()
    if config is None:
        raise RuntimeError("CONFLUENCE_USER/PASSWORD not set")
    meta = record.get("metadata") or {}
    page_id = str(meta.get("page_id") or "")
    if not page_id:
        raise ValueError("manifest row missing page_id metadata")

    client = ConfluenceClient(config)
    page = client.get_page(page_id, include_body=True)
    storage = (page.get("body") or {}).get("storage", {}).get("value")
    if storage is None:
        raise ValueError(f"no storage body returned for {record.get('external_id')}")
    return str(storage), _version_number(page)
