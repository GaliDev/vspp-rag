from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path


def _status_with_discovered_at(row: dict) -> str:
    status = row.get("status") or "-"
    discovered = row.get("discovered_at")
    if not discovered:
        return status
    try:
        normalized = discovered.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        date_part = dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_part = discovered[:10] if isinstance(discovered, str) and len(discovered) >= 10 else str(discovered)
    if status == "ingested":
        return f"{status} (discovered {date_part})"
    return f"{status} ({date_part})"


def _tier(row: dict) -> str:
    return row.get("tier") or "transport-level"


def _category(row: dict) -> str:
    return row.get("category") or "Transport"


def _is_core_structural_syntax(row: dict) -> bool:
    return bool(row.get("metadata", {}).get("core_structural_syntax"))


def _table_row(row: dict, source: str) -> str:
    status_cell = _status_with_discovered_at(row)
    pub = row.get("publication_status") or row.get("published") or "-"
    return (
        f"| {source} | {_category(row)} | {_tier(row)} | {row['authority']} | {row['external_id']} | "
        f"{row['title']} | {row.get('version') or '-'} | {pub} | {status_cell} | {row['remote_url']} |"
    )


def write_pm_catalog(records: list[dict], output_path: Path) -> None:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in records:
        grouped[row["source"]].append(row)

    by_cat: dict[str, int] = defaultdict(int)
    by_tier: dict[str, int] = defaultdict(int)
    for row in records:
        by_cat[_category(row)] += 1
        by_tier[_tier(row)] += 1

    core_rows = [r for r in records if _is_core_structural_syntax(r)]
    system_rows = [r for r in records if _tier(r) == "system-level" and not _is_core_structural_syntax(r)]
    transport_rows = [r for r in records if _tier(r) != "system-level"]

    def sort_key(r: dict) -> tuple:
        return (r.get("source", ""), r.get("external_id", ""))

    core_rows.sort(key=sort_key)
    system_rows.sort(key=sort_key)
    transport_rows.sort(key=sort_key)

    lines: list[str] = []
    lines.append("# PM Catalog - VSPP Standards Vault")
    lines.append("")
    lines.append("## Summary by source")
    lines.append("")
    lines.append("| Source (type) | Items |")
    lines.append("|---|---:|")
    total = 0
    for source in sorted(grouped):
        count = len(grouped[source])
        total += count
        lines.append(f"| {source} | {count} |")
    lines.append(f"| **Total** | **{total}** |")
    lines.append("")
    lines.append("## Summary by category")
    lines.append("")
    lines.append("| Category | Items |")
    lines.append("|---|---:|")
    for cat in sorted(by_cat):
        lines.append(f"| {cat} | {by_cat[cat]} |")
    lines.append(f"| **Total** | **{total}** |")
    lines.append("")
    lines.append("## Summary by tier")
    lines.append("")
    lines.append("| Tier | Items |")
    lines.append("|---|---:|")
    for tier in sorted(by_tier):
        lines.append(f"| {tier} | {by_tier[tier]} |")
    lines.append(f"| **Total** | **{total}** |")
    lines.append("")
    lines.append("### Core Structural Syntax (PM highlights)")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|---|---:|")
    lines.append(f"| Rows with `metadata.core_structural_syntax` | {len(core_rows)} |")
    lines.append("")

    header = "| Source | Category | Tier | Authority | ID | Title | Version | Publication (portal) | Status | URL |"
    sep = "|---|---|---|---|---|---|---|---|---|---|"

    lines.append("## Core Structural Syntax (PM priority)")
    lines.append("")
    lines.append(
        "Container syntax (MPEG-TS / ISOBMFF / NAL carriage), DVB-DASH normative track, DVB-Sub, "
        "legacy captions (CEA-608/708), and DVB BlueBook **A168** when discovered on dvb.org."
    )
    lines.append("")
    lines.append(header)
    lines.append(sep)
    for row in core_rows:
        lines.append(_table_row(row, row["source"]))
    lines.append("")

    lines.append("## Other system-level standards (Structural/System)")
    lines.append("")
    lines.append(header)
    lines.append(sep)
    for row in system_rows:
        lines.append(_table_row(row, row["source"]))
    lines.append("")

    lines.append("## Transport-Level standards")
    lines.append("")
    lines.append(header)
    lines.append(sep)
    for row in transport_rows:
        lines.append(_table_row(row, row["source"]))

    lines.append("")
    lines.append("## Sources")
    lines.append("")
    for source in sorted(grouped):
        lines.append(f"- `{source}`")

    lines.append("")
    lines.append("## URLs")
    lines.append("")
    urls = sorted({row["remote_url"] for row in records if row.get("remote_url")}, key=str.lower)
    for url in urls:
        lines.append(f"- {url}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
