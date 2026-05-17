from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

# Lower number = higher ingest priority.
FILE_TYPE_PRIORITY: dict[str, int] = {
    "zip": 0,
    "repository": 1,
    "pdf": 2,
    "txt": 3,
    "docx": 4,
    "html": 10,
    "standard-page": 11,
    "portal": 12,
    "error": 99,
}

ARTIFACT_FILE_TYPES = frozenset({"zip", "repository", "pdf", "txt", "docx"})
PAGE_FILE_TYPES = frozenset({"portal", "html", "standard-page"})


def ingest_priority(record: dict[str, Any]) -> int:
    meta = record.get("metadata") or {}
    if meta.get("access") == "paywalled":
        return 100
    if meta.get("artifact_url"):
        return FILE_TYPE_PRIORITY.get(str(record.get("file_type") or ""), 5) - 1
    return FILE_TYPE_PRIORITY.get(str(record.get("file_type") or ""), 50)


def is_artifact_record(record: dict[str, Any]) -> bool:
    ft = record.get("file_type")
    if ft in ARTIFACT_FILE_TYPES:
        return True
    return bool((record.get("metadata") or {}).get("artifact_url"))


def download_url(record: dict[str, Any]) -> str:
    meta = record.get("metadata") or {}
    return str(meta.get("artifact_url") or record.get("remote_url") or "")


def extract_pdf_urls_regex(html: str, base_url: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in re.finditer(r"""['"]([^'"]+\.pdf(?:\?[^'"]*)?)['"]""", html, re.I):
        full = urljoin(base_url, match.group(1))
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


def extract_deliver_version_links(html: str, base_url: str, *, limit: int = 12) -> list[str]:
    """ETSI-style deliver folders (version subdirs without .pdf in the parent listing)."""
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href in ("../", "./"):
            continue
        full = urljoin(base_url, href)
        if full in seen:
            continue
        path = urlparse(full).path
        if "/deliver/" not in path.lower():
            continue
        if re.search(r"\d{2}[._]\d{2}[._]\d{2}", path) or re.search(r"/\d{2}\.\d{2}\.\d{2}_\d+/", path):
            seen.add(full)
            out.append(full)
        if len(out) >= limit:
            break
    return out


def collect_pdf_candidates(html: str, base_url: str) -> list[str]:
    pdfs = extract_pdf_links(html, base_url)
    if not pdfs:
        pdfs = extract_pdf_urls_regex(html, base_url)
    return pdfs


def resolve_pdf_from_html(
    html: str,
    page_url: str,
    fetch_html: Any,
    *,
    max_subdir_fetches: int = 10,
) -> str | None:
    """Find a PDF on this page or one level of ETSI deliver version subdirectories."""
    pdfs = collect_pdf_candidates(html, page_url)
    if pdfs:
        return pick_best_pdf(pdfs)

    if "/deliver/" not in page_url.lower():
        return None

    for subdir in extract_deliver_version_links(html, page_url, limit=max_subdir_fetches):
        sub_html = fetch_html(subdir)
        if not sub_html:
            continue
        sub_pdfs = collect_pdf_candidates(sub_html, subdir)
        if sub_pdfs:
            return pick_best_pdf(sub_pdfs)
    return None


def resolve_pdf_from_urls(
    seed_urls: list[str],
    fetch_html: Any,
    *,
    max_subdir_fetches: int = 10,
) -> str | None:
    seen: set[str] = set()
    for url in seed_urls:
        if not url or url in seen:
            continue
        seen.add(url)
        html = fetch_html(url)
        if not html:
            continue
        found = resolve_pdf_from_html(html, url, fetch_html, max_subdir_fetches=max_subdir_fetches)
        if found:
            return found
    return None


def should_skip_runtime_pdf_resolution(record: dict[str, Any]) -> bool:
    """W3C TR pages should be ingested as HTML, not liaison PDFs from related specs."""
    source = str(record.get("source") or "").lower()
    remote = str(record.get("remote_url") or "").lower()
    return source == "w3c" and "/tr/" in remote


def pdf_url_matches_record(url: str, record: dict[str, Any]) -> bool:
    low = url.lower()
    ext = str(record.get("external_id") or "").lower()
    source = str(record.get("source") or "").lower()
    remote = str(record.get("remote_url") or "").lower()

    if should_skip_runtime_pdf_resolution(record):
        return False

    if ext.startswith("w3c-tr-imsc") or (source == "w3c" and ext.startswith("w3c-tr-")):
        if "w3.org" in low and ("/tr/" in low or "imsc" in low or "ttml" in low):
            return True
        if "imsc" in low or "ttml" in low:
            return True
        return False

    if ext.startswith("etsi-dvb-dash"):
        return any(token in low for token in ("103168", "103285", "dash", "etsi_ts"))

    if "en302307" in ext or ext.startswith("etsi-dvb-s2"):
        return any(token in low for token in ("302307", "en_302307", "s2", "dvb-s2"))

    if "ebu.ch" in low and source == "w3c":
        return False

    return True


def authority_group_key(record: dict[str, Any]) -> str:
    auth = (record.get("authority") or record.get("source") or "").strip()
    meta = record.get("metadata") or {}
    if meta.get("core_structural_syntax"):
        ext = str(record.get("external_id") or "").strip()
        if ext:
            return f"{auth}::{ext}"
    return auth


def liaison_urls(record: dict[str, Any]) -> list[str]:
    raw = (record.get("metadata") or {}).get("liaison_or_attachment_links") or []
    if isinstance(raw, str):
        return [raw]
    return [str(u) for u in raw if u]


def extract_pdf_links(html: str, base_url: str, *, limit: int = 20) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        full = urljoin(base_url, href)
        if ".pdf" not in full.lower():
            continue
        if full not in seen:
            seen.add(full)
            out.append(full)
        if len(out) >= limit:
            break
    return out


def pick_best_pdf(urls: list[str]) -> str | None:
    """Prefer latest-looking ETSI-style version paths, then longest path."""
    if not urls:
        return None

    def sort_key(u: str) -> tuple[int, str]:
        path = urlparse(u).path
        versionish = re.findall(r"\d{2}\.\d{2}\.\d{2}|\d{2}_\d{2}", path)
        return (len(versionish), path)

    return sorted(urls, key=sort_key, reverse=True)[0]


def ietf_text_artifact_url(doc: dict[str, Any]) -> str | None:
    name = doc.get("name")
    if not name:
        return None
    rfc_number = doc.get("rfc_number")
    if rfc_number:
        return f"https://www.rfc-editor.org/rfc/rfc{int(rfc_number)}.txt"
    rev = doc.get("rev")
    if rev:
        return f"https://www.ietf.org/archive/id/{name}-{rev}.txt"
    return f"https://www.ietf.org/archive/id/{name}-00.txt"


def _tiebreak_key(record: dict[str, Any]) -> tuple[int, str]:
    meta = record.get("metadata") or {}
    core = 0 if meta.get("core_structural_syntax") else 1
    ext = str(record.get("external_id") or "")
    # Prefer primary ISOBMFF repo over conformance-only repo for ISO/IEC.
    if ext == "MPEGGroup/isobmff":
        core = -1
    # Prefer IMSC / DASH seeds over generic portal rows for the same authority.
    if ext.startswith("w3c-tr-imsc") or ext.startswith("etsi-dvb-dash"):
        core = -1
    remote = str(record.get("remote_url") or "").lower()
    if "/deliver/" in remote:
        core = min(core, -2)
    if "technologies" in remote and record.get("source") == "etsi":
        core = max(core, 5)
    return (core, ext)


def _eligible_for_authority_pick(record: dict[str, Any], *, include_pages: bool) -> bool:
    if record.get("status") == "ingested":
        return False
    if record.get("file_type") == "error":
        return False
    meta = record.get("metadata") or {}
    if meta.get("access") == "paywalled":
        return False
    if is_artifact_record(record):
        return True
    return include_pages and record.get("file_type") in PAGE_FILE_TYPES


def _page_fallback_pool(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        r
        for r in rows
        if r.get("status") != "ingested"
        and r.get("file_type") != "error"
        and (r.get("metadata") or {}).get("access") != "paywalled"
        and r.get("file_type") in PAGE_FILE_TYPES
    ]


def pick_best_per_authority(
    records: list[dict[str, Any]],
    *,
    include_pages: bool = False,
    page_fallback: bool = True,
) -> list[dict[str, Any]]:
    """One row per authority: best artifact; optional portal HTML when no artifact exists."""
    by_auth: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for record in records:
        auth_key = authority_group_key(record)
        if not auth_key:
            continue
        if auth_key not in by_auth:
            order.append(auth_key)
        by_auth.setdefault(auth_key, []).append(record)

    out: list[dict[str, Any]] = []
    for auth_key in order:
        rows = by_auth[auth_key]
        pool = [r for r in rows if _eligible_for_authority_pick(r, include_pages=include_pages)]
        if not pool and (include_pages or page_fallback):
            pool = _page_fallback_pool(rows)
        if not pool:
            continue
        best = pool[0]
        best_pri = ingest_priority(best)
        for record in pool[1:]:
            pri = ingest_priority(record)
            if pri < best_pri or (pri == best_pri and _tiebreak_key(record) < _tiebreak_key(best)):
                best = record
                best_pri = pri
        out.append(best)
    return out
