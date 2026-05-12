from __future__ import annotations

import asyncio
import ftplib
from datetime import datetime, timezone

from src.core.models import DiscoveryRecord


FTP_HOST = "ftp.3gpp.org"
SERIES_PATHS = {
    "26-series": "/Specs/archive/26_series/",
    "29-series": "/Specs/archive/29_series/",
}


def _scan_path(path: str, cap: int = 30, dir_cap: int = 12) -> list[tuple[str, datetime | None, str]]:
    with ftplib.FTP(FTP_HOST, timeout=30) as ftp:
        ftp.login()
        ftp.cwd(path)
        rows: list[tuple[str, datetime | None, str]] = []
        try:
            candidates = [name for name, _facts in ftp.mlsd()][:dir_cap]
        except Exception:
            candidates = ftp.nlst()[:dir_cap]

        def add_if_zip(base_dir: str, name: str) -> None:
            if not name.endswith(".zip"):
                return
            modified = None
            try:
                stamp = ftp.sendcmd(f"MDTM {name}")
                modified = datetime.strptime(stamp[4:], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except Exception:
                modified = None
            rows.append((name, modified, base_dir))

        for item in candidates:
            try:
                ftp.cwd(item)
                nested_dir = item
                try:
                    nested_files = [name for name, _facts in ftp.mlsd()][: cap * 2]
                except Exception:
                    nested_files = ftp.nlst()[: cap * 2]
                ftp.cwd(path)
                for nested_name in nested_files:
                    add_if_zip(nested_dir, nested_name)
            except Exception:
                add_if_zip(".", item)

        rows = sorted(rows, key=lambda x: x[0], reverse=True)[:cap]
        return rows


async def discover_3gpp() -> list[DiscoveryRecord]:
    records: list[DiscoveryRecord] = []
    sem = asyncio.Semaphore(2)

    async def collect(series_name: str, path: str) -> None:
        async with sem:
            retries = 2
            for attempt in range(retries + 1):
                try:
                    rows = await asyncio.to_thread(_scan_path, path)
                    for filename, modified, subdir in rows:
                        ftp_path = f"{path}{subdir.strip('./')}/" if subdir != "." else path
                        records.append(
                            DiscoveryRecord(
                                source="3gpp",
                                authority="3GPP",
                                title=f"{series_name} spec package {filename}",
                                external_id=filename.replace(".zip", ""),
                                version=None,
                                published=modified.isoformat() if modified else None,
                                remote_url=f"ftp://{FTP_HOST}{ftp_path}{filename}",
                                file_type="zip",
                                category="Transport",
                                tier="transport-level",
                                metadata={"series": series_name, "ftp_path": ftp_path},
                            )
                        )
                    return
                except Exception as exc:
                    if attempt == retries:
                        records.append(
                            DiscoveryRecord(
                                source="3gpp",
                                authority="3GPP",
                                title=f"Discovery error for {series_name}",
                                external_id=f"error-{series_name}",
                                version=None,
                                published=None,
                                remote_url=f"ftp://{FTP_HOST}{path}",
                                file_type="error",
                                category="Transport",
                                tier="transport-level",
                                metadata={"error": str(exc)},
                            )
                        )
                    await asyncio.sleep(2 * (attempt + 1))

    await asyncio.gather(*(collect(series, path) for series, path in SERIES_PATHS.items()))
    return records

