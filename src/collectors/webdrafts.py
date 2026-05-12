from __future__ import annotations

import asyncio

from src.core.models import DiscoveryRecord


async def discover_webdrafts() -> list[DiscoveryRecord]:
    """Legacy placeholder removed; structural/broadcast coverage lives in structural_system."""
    await asyncio.sleep(0)
    return []
