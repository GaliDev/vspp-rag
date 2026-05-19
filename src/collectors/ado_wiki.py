from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from src.core.models import DiscoveryRecord

logger = logging.getLogger(__name__)

API_VERSION = "7.1"
INTERNAL_CATEGORY = "Internal"
SYSTEM_TIER = "system-level"
DEFAULT_ORG = "tm-vspp"
DEFAULT_PROJECTS = ("MK-VSPP",)


@dataclass(frozen=True)
class AdoWikiConfig:
    org: str
    pat: str
    projects: tuple[str, ...]


def load_ado_config() -> AdoWikiConfig | None:
    pat = os.environ.get("ADO_PAT", "").strip()
    if not pat:
        return None
    org = os.environ.get("ADO_ORG", DEFAULT_ORG).strip() or DEFAULT_ORG
    raw_projects = os.environ.get("ADO_WIKI_PROJECTS", ",".join(DEFAULT_PROJECTS))
    projects = tuple(p.strip() for p in raw_projects.split(",") if p.strip())
    if not projects:
        projects = DEFAULT_PROJECTS
    return AdoWikiConfig(org=org, pat=pat, projects=projects)


def external_id(project: str, wiki_id: str, path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"ado:{project}:{wiki_id}:{normalized_path}"


def page_title_from_path(path: str) -> str:
    parts = [p for p in path.strip("/").split("/") if p]
    return parts[-1] if parts else "Wiki Home"


class AdoWikiClient:
    def __init__(self, config: AdoWikiConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.auth = ("", config.pat)
        self.session.headers.update(
            {"Accept": "application/json", "User-Agent": "VSPP-Standards-Vault/1.0"}
        )

    def _url(self, project: str, suffix: str) -> str:
        base = f"https://dev.azure.com/{self.config.org}/{project}/_apis/wiki"
        return f"{base}/{suffix}"

    def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        response = self.session.get(url, params=params, timeout=60)
        if response.status_code == 429:
            raise RuntimeError("ADO rate limited (429)")
        response.raise_for_status()
        return response.json()

    def list_wikis(self, project: str) -> list[dict[str, Any]]:
        url = self._url(project, "wikis")
        payload = self.get_json(url, params={"api-version": API_VERSION})
        return list(payload.get("value") or [])

    def page_tree(self, project: str, wiki_id: str) -> dict[str, Any]:
        url = self._url(project, f"wikis/{quote(str(wiki_id), safe='')}/pages")
        return self.get_json(
            url,
            params={"recursionLevel": "full", "api-version": API_VERSION},
        )

    def get_page(
        self,
        project: str,
        wiki_id: str,
        path: str,
        *,
        include_content: bool = False,
    ) -> tuple[dict[str, Any], str | None]:
        url = self._url(project, f"wikis/{quote(str(wiki_id), safe='')}/pages")
        params: dict[str, Any] = {"path": path, "api-version": API_VERSION}
        if include_content:
            params["includeContent"] = "true"
        response = self.session.get(url, params=params, timeout=60)
        if response.status_code == 429:
            raise RuntimeError("ADO rate limited (429)")
        response.raise_for_status()
        etag = response.headers.get("ETag") or response.headers.get("Etag")
        return response.json(), etag


def flatten_page_paths(node: dict[str, Any] | list[Any]) -> list[str]:
    paths: list[str] = []

    def walk(item: dict[str, Any]) -> None:
        path = item.get("path")
        if isinstance(path, str) and path:
            paths.append(path)
        for child in item.get("subPages") or []:
            if isinstance(child, dict):
                walk(child)

    if isinstance(node, list):
        for entry in node:
            if isinstance(entry, dict):
                walk(entry)
    elif isinstance(node, dict):
        walk(node)
        for entry in node.get("pages") or node.get("value") or []:
            if isinstance(entry, dict):
                walk(entry)
    return paths


def _wiki_identifier(wiki: dict[str, Any]) -> str:
    return str(wiki.get("id") or wiki.get("name") or "wiki")


def _page_remote_url(org: str, project: str, path: str) -> str:
    slug = path.strip("/")
    if not slug:
        return f"https://dev.azure.com/{org}/{project}/_wiki/wikis"
    encoded = "/".join(quote(part, safe="") for part in slug.split("/"))
    return f"https://dev.azure.com/{org}/{project}/_wiki/wikis?wiki=Project%20Wiki&pagePath=/{encoded}"


def _discovery_record(
    *,
    org: str,
    project: str,
    wiki: dict[str, Any],
    page: dict[str, Any],
    path: str,
    etag: str | None,
) -> DiscoveryRecord:
    wiki_id = _wiki_identifier(wiki)
    wiki_name = str(wiki.get("name") or wiki_id)
    title = str(page.get("pageTitle") or page.get("title") or page_title_from_path(path))
    remote = str(page.get("remoteUrl") or page.get("url") or _page_remote_url(org, project, path))
    published = page.get("lastModified") or page.get("lastModifiedDate")
    if published is not None:
        published = str(published)
    version = str(page.get("version") or etag) if (page.get("version") or etag) else None
    eid = external_id(project, wiki_id, path)
    return DiscoveryRecord(
        source="ado_wiki",
        authority=f"ADO/{project}",
        title=title,
        external_id=eid,
        version=version,
        published=published,
        remote_url=remote,
        file_type="markdown",
        category=INTERNAL_CATEGORY,
        tier=SYSTEM_TIER,
        metadata={
            "ado_org": org,
            "ado_project": project,
            "wiki_id": wiki_id,
            "wiki_name": wiki_name,
            "wiki_path": path if path.startswith("/") else f"/{path}",
            "ado_wiki_etag": etag,
            "ado_page_id": page.get("id"),
        },
    )


def _unchanged(existing: dict[str, Any], etag: str | None) -> bool:
    if not etag:
        return False
    old_meta = existing.get("metadata") or {}
    return str(old_meta.get("ado_wiki_etag") or "") == etag


def _record_from_manifest(old: dict[str, Any]) -> DiscoveryRecord:
    fields = DiscoveryRecord.__dataclass_fields__
    kwargs = {k: old[k] for k in fields if k in old}
    kwargs.setdefault("metadata", dict(old.get("metadata") or {}))
    return DiscoveryRecord(**kwargs)


async def discover_ado_wiki(
    existing_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[DiscoveryRecord]:
    config = load_ado_config()
    if config is None:
        logger.warning("ADO_PAT not set; skipping ado_wiki discovery")
        return []

    existing = existing_by_id or {}
    client = AdoWikiClient(config)
    records: list[DiscoveryRecord] = []
    sem = asyncio.Semaphore(8)

    async def discover_page(
        project: str,
        wiki: dict[str, Any],
        path: str,
    ) -> None:
        wiki_id = _wiki_identifier(wiki)
        eid = external_id(project, wiki_id, path)
        old = existing.get(eid)
        async with sem:
            try:
                page, etag = await asyncio.to_thread(
                    client.get_page, project, wiki_id, path, include_content=False
                )
            except requests.HTTPError as exc:
                logger.warning("ADO page metadata failed %s: %s", eid, exc)
                return
            except Exception as exc:
                logger.warning("ADO page metadata failed %s: %s", eid, exc)
                return

        if old and _unchanged(old, etag):
            records.append(_record_from_manifest(old))
            return

        records.append(
            _discovery_record(
                org=config.org,
                project=project,
                wiki=wiki,
                page=page,
                path=path,
                etag=etag,
            )
        )

    async def discover_project(project: str) -> None:
        try:
            wikis = await asyncio.to_thread(client.list_wikis, project)
        except requests.HTTPError as exc:
            logger.error("ADO list wikis failed for %s: %s", project, exc)
            return
        except Exception as exc:
            logger.error("ADO list wikis failed for %s: %s", project, exc)
            return

        page_jobs: list[tuple[dict[str, Any], str]] = []
        for wiki in wikis:
            wiki_id = _wiki_identifier(wiki)
            try:
                tree = await asyncio.to_thread(client.page_tree, project, wiki_id)
            except requests.HTTPError as exc:
                logger.warning("ADO page tree failed %s/%s: %s", project, wiki_id, exc)
                continue
            paths = flatten_page_paths(tree)
            if not paths:
                paths = ["/"]
            for path in paths:
                page_jobs.append((wiki, path))

        await asyncio.gather(*(discover_page(project, wiki, path) for wiki, path in page_jobs))

    await asyncio.gather(*(discover_project(project) for project in config.projects))
    logger.info("ado_wiki discovery: %d pages", len(records))
    return records


def fetch_page_markdown(record: dict[str, Any]) -> tuple[str, str | None]:
    """Fetch wiki page markdown for ingest. Requires ADO_PAT in environment."""
    config = load_ado_config()
    if config is None:
        raise RuntimeError("ADO_PAT not set")
    meta = record.get("metadata") or {}
    project = str(meta.get("ado_project") or "")
    wiki_id = str(meta.get("wiki_id") or "")
    path = str(meta.get("wiki_path") or "/")
    if not project or not wiki_id:
        raise ValueError("manifest row missing ado_project or wiki_id metadata")

    client = AdoWikiClient(config)
    page, etag = client.get_page(project, wiki_id, path, include_content=True)
    content = page.get("content")
    if content is None:
        raise ValueError(f"no content returned for {record.get('external_id')}")
    return str(content), etag
