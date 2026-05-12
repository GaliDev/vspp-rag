from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src.core.models import DiscoveryRecord

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "VSPP-Standards-Vault/1.0 (+https://example.invalid)"}
STRUCTURAL = "Structural/System"
SYSTEM_TIER = "system-level"

# PM-highlighted: container/TS, DVB-DASH normative track, legacy captions, DVB-Sub.
CORE_STRUCTURAL_IDS = frozenset(
    {
        "iso-iec-13818-1",
        "iso-iec-14496-12",
        "iso-iec-14496-15",
        "etsi-en-300-743-dvb-sub",
        "cta-cea-608",
        "cta-cea-708",
        "etsi-dvb-dash-ts103168",
        "dvb-bluebook-a168",
    }
)

# ISO store refs (detail pages are static HTML; search UI is script-driven).
ISO_SEEDS: list[tuple[str, str, int]] = [
    ("iso-iec-14496-12", "ISO/IEC 14496-12 (ISOBMFF / base media file format)", 83102),
    ("iso-iec-14496-14", "ISO/IEC 14496-14 (MP4 file format)", 79110),
    ("iso-iec-14496-15", "ISO/IEC 14496-15 (NAL unit carriage in ISOBMFF)", 89118),
    ("iso-iec-13818-1", "ISO/IEC 13818-1 (MPEG-2 / MPEG-TS Systems)", 91403),
]

# ETSI: primary deliver roots + search fallback query strings.
ETSI_TARGETS: list[dict[str, Any]] = [
    {
        "external_id": "etsi-dvb-dash-ts103168",
        "title_hint": "DVB-DASH (TS 103 168 / A168 family)",
        "urls": [
            "https://www.etsi.org/deliver/etsi_ts/103100_103199/103285/",
            "https://www.etsi.org/deliver/etsi_ts/103100_103199/103168/",
            "https://www.etsi.org/deliver/etsi_ts/103100_103199/103168_01/",
        ],
        "search_queries": ["TS 103 168 DVB", "DVB-DASH TS 103 285"],
        "core_structural_syntax": True,
    },
    {
        "external_id": "etsi-en-300-743-dvb-sub",
        "title_hint": "DVB Subtitling (EN 300 743)",
        "urls": [
            "https://www.etsi.org/deliver/etsi_en/300700_300799/300743/",
            "https://www.etsi.org/deliver/etsi_en/300700_300799/300743_01/",
        ],
        "search_queries": ["EN 300 743", "DVB subtitling EN 300 743"],
        "core_structural_syntax": True,
    },
    {
        "external_id": "etsi-en-300-468-dvb-si",
        "title_hint": "DVB-SI (EN 300 468)",
        "urls": [
            "https://www.etsi.org/deliver/etsi_en/300400_300499/300468/",
        ],
        "search_queries": ["EN 300 468", "DVB-SI 300 468"],
    },
    {
        "external_id": "etsi-dvb-s2-en302307",
        "title_hint": "DVB-S2 (EN 302 307 family)",
        "urls": [
            "https://www.etsi.org/deliver/etsi_en/302300_302399/302307/",
        ],
        "search_queries": ["EN 302 307", "DVB-S2"],
    },
]

W3C_IMSC_TR = [
    ("w3c-tr-imsc11", "TTML Profiles for Internet Media Subtitles and Captions 1.1 (IMSC1.1)", "https://www.w3.org/TR/ttml-imsc1.1/"),
    ("w3c-tr-imsc13", "TTML Profiles for Internet Media Subtitles and Captions 1.3 (IMSC1.3)", "https://www.w3.org/TR/ttml-imsc1.3/"),
]

SMPTE_LISTING = "https://www.smpte.org/standards"
CTA_STANDARDS_COLLECTION = "https://shop.cta.tech/collections/standards"

HIDDEN_LINK_HINTS = (
    "liaison",
    "attachment",
    "annex",
    "amendment",
    "corrigendum",
    "supplement",
    ".pdf",
    ".zip",
    "/deliver/",
    "download",
    "obp/ui",
)


def _fetch(url: str, timeout: int = 28) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code >= 400:
            return None
        return r.text
    except requests.RequestException as exc:
        logger.debug("fetch failed %s: %s", url, exc)
        return None


def _collect_hidden_links(soup: BeautifulSoup, base_url: str, limit: int = 40) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        full = urljoin(base_url, href)
        low = full.lower()
        if any(h in low for h in HIDDEN_LINK_HINTS):
            if full not in seen:
                seen.add(full)
                out.append(full)
        if len(out) >= limit:
            break
    if out:
        logger.info("structural: liaison/attachment-style links on %s -> %d items", base_url, len(out))
    return out


def _iso_stage_from_dl(soup: BeautifulSoup) -> str | None:
    for dt in soup.find_all("dt"):
        lab = dt.get_text(" ", strip=True).lower()
        if "stage" in lab:
            dd = dt.find_next_sibling("dd")
            if dd:
                return dd.get_text(" ", strip=True)[:200]
    return None


def _guess_portal_status(text: str) -> str | None:
    t = text.lower()
    if "withdrawn" in t:
        return "Withdrawn"
    if "under revision" in t or "under systematic review" in t:
        return "Under revision"
    if "published" in t and "not published" not in t:
        return "Published"
    if "draft" in t or "working draft" in t:
        return "Draft / WD"
    if "superseded" in t:
        return "Superseded"
    return None


def _parse_iso_page(html: str, url: str, external_id: str, seed_title: str) -> DiscoveryRecord:
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else seed_title
    body = soup.get_text(" ", strip=True)
    pub_date = None
    for label in soup.find_all(string=re.compile(r"Publication date", re.I)):
        parent = label.parent
        if parent:
            chunk = parent.get_text(" ", strip=True)
            m = re.search(r"(\d{1,2}\s+\w+\s+\d{4})", chunk)
            if m:
                pub_date = m.group(1)
                break
    if not pub_date:
        m2 = re.search(r"Publication date\s*[:\s]+(\d{1,2}\s+\w+\s+\d{4})", body, re.I)
        if m2:
            pub_date = m2.group(1)
    pub_status = _iso_stage_from_dl(soup)
    if not pub_status:
        if pub_date:
            pub_status = "Published"
        else:
            pub_status = _guess_portal_status(body[:900])
    hidden = _collect_hidden_links(soup, url)
    meta: dict[str, Any] = {
        "discovery_profile": "structural_system",
        "liaison_or_attachment_links": hidden,
    }
    if external_id in CORE_STRUCTURAL_IDS:
        meta["core_structural_syntax"] = True
    return DiscoveryRecord(
        source="iso",
        authority="ISO/IEC",
        title=title or seed_title,
        external_id=external_id,
        version=None,
        published=pub_date,
        remote_url=url,
        file_type="standard-page",
        category=STRUCTURAL,
        tier=SYSTEM_TIER,
        publication_status=pub_status,
        metadata=meta,
    )


def _discover_iso() -> list[DiscoveryRecord]:
    records: list[DiscoveryRecord] = []
    for ext_id, seed_title, ref in ISO_SEEDS:
        url = f"https://www.iso.org/standard/{ref}.html"
        html = _fetch(url)
        if not html:
            em: dict[str, Any] = {"discovery_profile": "structural_system", "error": "fetch_failed"}
            if ext_id in CORE_STRUCTURAL_IDS:
                em["core_structural_syntax"] = True
            records.append(
                DiscoveryRecord(
                    source="iso",
                    authority="ISO/IEC",
                    title=f"{seed_title} (fetch failed)",
                    external_id=ext_id,
                    version=None,
                    published=None,
                    remote_url=url,
                    file_type="error",
                    category=STRUCTURAL,
                    tier=SYSTEM_TIER,
                    publication_status=None,
                    metadata=em,
                )
            )
            continue
        records.append(_parse_iso_page(html, url, ext_id, seed_title))
    return records


def _parse_generic_portal(
    html: str,
    url: str,
    *,
    source: str,
    authority: str,
    external_id: str,
    title_hint: str,
    metadata_extra: dict[str, Any] | None = None,
) -> DiscoveryRecord:
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.find("h1") or soup.find("title")
    title = title_el.get_text(" ", strip=True) if title_el else title_hint
    body = soup.get_text(" ", strip=True)
    pub_status = _guess_portal_status(body)
    pub_date = None
    m = re.search(r"(\d{1,2}\s+[A-Za-z]+\s+\d{4})", body)
    if m:
        pub_date = m.group(1)
    hidden = _collect_hidden_links(soup, url)
    meta: dict[str, Any] = {
        "discovery_profile": "structural_system",
        "liaison_or_attachment_links": hidden,
    }
    if metadata_extra:
        meta.update(metadata_extra)
    if external_id in CORE_STRUCTURAL_IDS and "core_structural_syntax" not in meta:
        meta["core_structural_syntax"] = True
    return DiscoveryRecord(
        source=source,
        authority=authority,
        title=f"{title_hint}: {title}" if title_hint not in title else title,
        external_id=external_id,
        version=None,
        published=pub_date,
        remote_url=url,
        file_type="standard-page",
        category=STRUCTURAL,
        tier=SYSTEM_TIER,
        publication_status=pub_status,
        metadata=meta,
    )


def _etsi_search_first_hit(query: str) -> str | None:
    q = requests.utils.quote(query)
    url = f"https://www.etsi.org/search/site/{q}"
    html = _fetch(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a[href*='/deliver/'], a[href*='/standard/'], a[href*='/technologies/']"):
        href = a.get("href")
        if not href:
            continue
        full = urljoin(url, href)
        if urlparse(full).netloc.endswith("etsi.org"):
            return full
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/deliver/" in href:
            return urljoin(url, href)
    return None


def _discover_etsi() -> list[DiscoveryRecord]:
    records: list[DiscoveryRecord] = []
    for spec in ETSI_TARGETS:
        ext_id = spec["external_id"]
        hint = spec["title_hint"]
        landed_url: str | None = None
        html: str | None = None
        for u in spec["urls"]:
            h = _fetch(u)
            if h:
                landed_url, html = u, h
                break
        if not html:
            for q in spec["search_queries"]:
                hit = _etsi_search_first_hit(q)
                if hit:
                    h = _fetch(hit)
                    if h:
                        landed_url, html = hit, h
                        break
            if not html:
                fallback = "https://www.etsi.org/technologies#DVB"
                fm: dict[str, Any] = {
                    "discovery_profile": "structural_system",
                    "search_queries": spec["search_queries"],
                    "attempted_urls": spec["urls"],
                }
                if spec.get("core_structural_syntax"):
                    fm["core_structural_syntax"] = True
                records.append(
                    DiscoveryRecord(
                        source="etsi",
                        authority="ETSI/DVB",
                        title=f"{hint} (no deliver page resolved; portal only)",
                        external_id=ext_id,
                        version=None,
                        published=None,
                        remote_url=fallback,
                        file_type="portal",
                        category=STRUCTURAL,
                        tier=SYSTEM_TIER,
                        publication_status=None,
                        metadata=fm,
                    )
                )
                continue
        em_etsi: dict[str, Any] = {}
        if spec.get("core_structural_syntax"):
            em_etsi["core_structural_syntax"] = True
        records.append(
            _parse_generic_portal(
                html,
                landed_url or "",
                source="etsi",
                authority="ETSI/DVB",
                external_id=ext_id,
                title_hint=hint,
                metadata_extra=em_etsi or None,
            )
        )
    return records


def _parse_w3c_tr(html: str, url: str, external_id: str, label: str) -> DiscoveryRecord:
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.find("h1", class_="title") or soup.find("h1")
    title = title_el.get_text(" ", strip=True) if title_el else label
    status_el = soup.find("h2", {"property": "dcterms:type"}) or soup.find("h2", class_=re.compile("subtitle", re.I))
    pub_status = status_el.get_text(" ", strip=True) if status_el else None
    if not pub_status:
        pub_status = _guess_portal_status(soup.get_text(" ", strip=True)[:2000])
    date_el = soup.find("time")
    published = date_el.get_text(strip=True) if date_el else None
    hidden = _collect_hidden_links(soup, url)
    return DiscoveryRecord(
        source="w3c",
        authority="W3C",
        title=title,
        external_id=external_id,
        version=None,
        published=published,
        remote_url=url,
        file_type="standard-page",
        category=STRUCTURAL,
        tier=SYSTEM_TIER,
        publication_status=pub_status,
        metadata={"discovery_profile": "structural_system", "liaison_or_attachment_links": hidden},
    )


def _discover_w3c_imsc() -> list[DiscoveryRecord]:
    out: list[DiscoveryRecord] = []
    for ext_id, label, url in W3C_IMSC_TR:
        html = _fetch(url)
        if not html:
            out.append(
                DiscoveryRecord(
                    source="w3c",
                    authority="W3C",
                    title=f"{label} (fetch failed)",
                    external_id=ext_id,
                    version=None,
                    published=None,
                    remote_url=url,
                    file_type="error",
                    category=STRUCTURAL,
                    tier=SYSTEM_TIER,
                    metadata={"discovery_profile": "structural_system"},
                )
            )
            continue
        out.append(_parse_w3c_tr(html, url, ext_id, label))
    return out


def _discover_smpte() -> list[DiscoveryRecord]:
    listing_html = _fetch(SMPTE_LISTING)
    targets: dict[str, tuple[str, str]] = {
        "2110": ("smpte-st-2110", "SMPTE ST 2110 (professional media over IP)"),
        "2022": ("smpte-st-2022", "SMPTE ST 2022 (video over IP)"),
    }
    resolved: dict[str, str] = {}
    if listing_html:
        soup = BeautifulSoup(listing_html, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            text = a.get_text(" ", strip=True).lower()
            full = urljoin(SMPTE_LISTING, a["href"])
            if "2110" not in resolved and (
                "st-2110" in href or "st2110" in href or " 2110" in text or text.startswith("st 2110")
            ):
                resolved["2110"] = full
            if "2022" not in resolved and (
                "st-2022" in href or "st2022" in href or "st 2022" in text or "2022-6" in href
            ):
                if "2022-7" not in href:
                    resolved["2022"] = full

    out: list[DiscoveryRecord] = []
    for key, (ext_id, hint) in targets.items():
        url = resolved.get(key)
        if not url:
            out.append(
                DiscoveryRecord(
                    source="smpte",
                    authority="SMPTE",
                    title=f"{hint} (no detail link from listing)",
                    external_id=ext_id,
                    version=None,
                    published=None,
                    remote_url=SMPTE_LISTING,
                    file_type="portal",
                    category=STRUCTURAL,
                    tier=SYSTEM_TIER,
                    publication_status=None,
                    metadata={"discovery_profile": "structural_system", "listing": SMPTE_LISTING},
                )
            )
            continue
        page = _fetch(url)
        if not page:
            out.append(
                DiscoveryRecord(
                    source="smpte",
                    authority="SMPTE",
                    title=f"{hint} (detail fetch failed)",
                    external_id=ext_id,
                    version=None,
                    published=None,
                    remote_url=url,
                    file_type="error",
                    category=STRUCTURAL,
                    tier=SYSTEM_TIER,
                    metadata={"discovery_profile": "structural_system"},
                )
            )
            continue
        out.append(
            _parse_generic_portal(
                page, url, source="smpte", authority="SMPTE", external_id=ext_id, title_hint=hint
            )
        )
    return out


def _discover_dvb_bluebooks() -> list[DiscoveryRecord]:
    """Crawl dvb.org listing pages for BlueBook / A168 / DVB-DASH related links (metadata only)."""
    pattern = re.compile(r"blue\s*book|bluebook|a168|dvb[-\s]?dash|mpeg[-\s]?dash", re.I)
    seen: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for seed in (
        "https://dvb.org/specifications/",
        "https://dvb.org/specifications/standards-bluebooks/",
        "https://dvb.org/resources/",
    ):
        html = _fetch(seed)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith("#"):
                continue
            full = urljoin(seed, href)
            host = urlparse(full).netloc.lower()
            if host and "dvb.org" not in host:
                continue
            text = a.get_text(" ", strip=True)
            if pattern.search(f"{text} {full}"):
                if full not in seen:
                    seen.add(full)
                    candidates.append((full, text))

    records: list[DiscoveryRecord] = []
    a168_used = False
    idx = 0
    for url, label in candidates[:35]:
        idx += 1
        is_a168 = bool(re.search(r"a168|\ba\s*168\b", f"{label} {url}", re.I))
        if is_a168 and not a168_used:
            ext_id = "dvb-bluebook-a168"
            a168_used = True
        else:
            ext_id = f"dvb-bluebook-{idx:03d}"
        extra: dict[str, Any] = {"dvb_crawler": "bluebook_suite"}
        if ext_id == "dvb-bluebook-a168":
            extra["core_structural_syntax"] = True
        page = _fetch(url)
        if not page:
            records.append(
                DiscoveryRecord(
                    source="dvb",
                    authority="DVB",
                    title=f"DVB BlueBook crawl (fetch failed): {label[:100] or url}",
                    external_id=ext_id,
                    version=None,
                    published=None,
                    remote_url=url,
                    file_type="error",
                    category=STRUCTURAL,
                    tier=SYSTEM_TIER,
                    metadata=extra,
                )
            )
            continue
        records.append(
            _parse_generic_portal(
                page,
                url,
                source="dvb",
                authority="DVB",
                external_id=ext_id,
                title_hint=f"DVB BlueBook / related: {label[:60] or 'link'}",
                metadata_extra=extra,
            )
        )
    if not records:
        records.append(
            DiscoveryRecord(
                source="dvb",
                authority="DVB",
                title="DVB BlueBooks (no crawl hits from dvb.org listings)",
                external_id="dvb-bluebook-portal",
                version=None,
                published=None,
                remote_url="https://dvb.org/specifications/standards-bluebooks/",
                file_type="portal",
                category=STRUCTURAL,
                tier=SYSTEM_TIER,
                metadata={"dvb_crawler": "bluebook_suite", "note": "listing_empty"},
            )
        )
    return records


def _discover_cta() -> list[DiscoveryRecord]:
    html = _fetch(CTA_STANDARDS_COLLECTION)
    want = {
        "cta-cea-608": "CEA-608 (Line 21 closed captions)",
        "cta-cea-708": "CEA-708 (Digital closed captions)",
    }
    found: dict[str, str] = {k: "" for k in want}
    if html:
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            text = a.get_text(" ", strip=True).lower()
            blob = f"{href} {text}"
            if not found["cta-cea-608"] and "608" in blob and any(
                x in blob for x in ("line 21", "line-21", "cea-608", "closed caption", "captions")
            ):
                found["cta-cea-608"] = urljoin(CTA_STANDARDS_COLLECTION, a["href"])
            if not found["cta-cea-708"] and "708" in blob and any(
                x in blob for x in ("digital", "cea-708", "closed caption", "captions")
            ):
                found["cta-cea-708"] = urljoin(CTA_STANDARDS_COLLECTION, a["href"])

    out: list[DiscoveryRecord] = []
    for ext_id, hint in want.items():
        url = found[ext_id] or CTA_STANDARDS_COLLECTION
        page = _fetch(url) if url else None
        if not page:
            out.append(
                DiscoveryRecord(
                    source="cta",
                    authority="CTA/CEA",
                    title=f"{hint} (fetch failed)",
                    external_id=ext_id,
                    version=None,
                    published=None,
                    remote_url=url,
                    file_type="error",
                    category=STRUCTURAL,
                    tier=SYSTEM_TIER,
                    metadata={
                        "discovery_profile": "structural_system",
                        "collection": CTA_STANDARDS_COLLECTION,
                        "core_structural_syntax": True,
                        "compliance_track": "legacy_captions",
                    },
                )
            )
            continue
        out.append(
            _parse_generic_portal(
                page,
                url,
                source="cta",
                authority="CTA/CEA",
                external_id=ext_id,
                title_hint=hint,
                metadata_extra={
                    "core_structural_syntax": True,
                    "compliance_track": "legacy_captions",
                },
            )
        )
    return out


def _github_imsc_repo() -> DiscoveryRecord:
    url = "https://api.github.com/repos/w3c/imsc"
    try:
        r = requests.get(url, headers={**HEADERS, "Accept": "application/vnd.github+json"}, timeout=20)
        r.raise_for_status()
        data = json.loads(r.text)
    except requests.RequestException as exc:
        return DiscoveryRecord(
            source="github",
            authority="GitHub/w3c",
            title="w3c/imsc (API error)",
            external_id="error-w3c-imsc",
            version=None,
            published=None,
            remote_url="https://github.com/w3c/imsc",
            file_type="error",
            category=STRUCTURAL,
            tier=SYSTEM_TIER,
            metadata={"error": str(exc), "discovery_profile": "structural_system"},
        )
    hidden: list[str] = []
    return DiscoveryRecord(
        source="github",
        authority="GitHub/w3c",
        title=data.get("description") or "IMSC (TTML profiles for subtitles/captions)",
        external_id="w3c/imsc",
        version=data.get("default_branch"),
        published=data.get("updated_at"),
        remote_url=data.get("html_url", "https://github.com/w3c/imsc"),
        file_type="repository",
        category=STRUCTURAL,
        tier=SYSTEM_TIER,
        publication_status=None,
        metadata={"stars": data.get("stargazers_count", 0), "discovery_profile": "structural_system", "liaison_or_attachment_links": hidden},
    )


def _discover_github_imsc() -> list[DiscoveryRecord]:
    return [_github_imsc_repo()]


async def discover_structural_system() -> list[DiscoveryRecord]:
    """Structural / broadcast / professional-ingest standards (metadata only)."""
    tasks = [
        asyncio.to_thread(_discover_iso),
        asyncio.to_thread(_discover_etsi),
        asyncio.to_thread(_discover_w3c_imsc),
        asyncio.to_thread(_discover_smpte),
        asyncio.to_thread(_discover_cta),
        asyncio.to_thread(_discover_dvb_bluebooks),
        asyncio.to_thread(_discover_github_imsc),
    ]
    buckets = await asyncio.gather(*tasks)
    records: list[DiscoveryRecord] = []
    for b in buckets:
        records.extend(b)
    return records
