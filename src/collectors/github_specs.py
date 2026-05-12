from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.core.models import DiscoveryRecord


REPOS = [
    ("w3c", "webvtt"),
    ("w3c", "ttml1"),
    ("AOMediaCodec", "av1-spec"),
]


def _repo_api(owner: str, repo: str) -> dict:
    req = Request(
        f"https://api.github.com/repos/{owner}/{repo}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "VSPP-Standards-Vault"},
    )
    with urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


async def discover_github() -> list[DiscoveryRecord]:
    records: list[DiscoveryRecord] = []

    async def one(owner: str, repo: str) -> None:
        try:
            data = await asyncio.to_thread(_repo_api, owner, repo)
            records.append(
                DiscoveryRecord(
                    source="github",
                    authority=f"GitHub/{owner}",
                    title=data.get("description") or f"{owner}/{repo}",
                    external_id=f"{owner}/{repo}",
                    version=data.get("default_branch"),
                    published=data.get("updated_at"),
                    remote_url=data.get("html_url", f"https://github.com/{owner}/{repo}"),
                    file_type="repository",
                    category="Transport",
                    tier="transport-level",
                    metadata={"stars": data.get("stargazers_count", 0)},
                )
            )
        except (HTTPError, URLError, TimeoutError) as exc:
            records.append(
                DiscoveryRecord(
                    source="github",
                    authority=f"GitHub/{owner}",
                    title=f"Discovery error {owner}/{repo}",
                    external_id=f"error-{owner}-{repo}",
                    version=None,
                    published=None,
                    remote_url=f"https://github.com/{owner}/{repo}",
                    file_type="error",
                    category="Transport",
                    tier="transport-level",
                    metadata={"error": str(exc)},
                )
            )

    await asyncio.gather(*(one(owner, repo) for owner, repo in REPOS))
    return records

