"""Address and fuel station lookups via OpenStreetMap Nominatim (max 1 request per second)."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import USER_AGENT

_LOGGER = logging.getLogger(__name__)
BASE = "https://nominatim.openstreetmap.org"
_lock = asyncio.Lock()
_last = 0.0


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def _get(hass: HomeAssistant, path: str, params: dict[str, Any]) -> Any:
    global _last
    async with _lock:
        wait = 1.1 - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            session = async_get_clientsession(hass)
            async with session.get(
                BASE + path,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("Nominatim %s gaf status %s", path, resp.status)
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.warning("Nominatim %s mislukt: %s", path, err)
            return None
        finally:
            _last = time.monotonic()


def _place(address: dict[str, Any]) -> str:
    for key in ("city", "town", "village", "municipality", "hamlet"):
        if address.get(key):
            return address[key]
    return ""


def _street(address: dict[str, Any]) -> str:
    road = address.get("road") or address.get("pedestrian") or ""
    number = address.get("house_number") or ""
    return f"{road} {number}".strip()


async def reverse(hass: HomeAssistant, lat: float, lon: float) -> dict[str, str]:
    """Street, place and province (state) of a position; empty strings when unknown."""
    data = await _get(
        hass,
        "/reverse",
        {
            "format": "jsonv2",
            "addressdetails": 1,
            "zoom": 18,
            "lat": f"{lat:.6f}",
            "lon": f"{lon:.6f}",
        },
    )
    address = (data or {}).get("address") or {}
    return {
        "straat": _street(address),
        "plaats": _place(address),
        "provincie": address.get("state") or "",
    }


async def fuel_station(
    hass: HomeAssistant, lat: float, lon: float
) -> dict[str, Any] | None:
    """Nearest fuel station within about 500 m, or None."""
    dlat, dlon = 0.0045, 0.0075
    data = await _get(
        hass,
        "/search",
        {
            "format": "jsonv2",
            "amenity": "fuel",
            "bounded": 1,
            "limit": 10,
            "addressdetails": 1,
            "viewbox": f"{lon - dlon},{lat + dlat},{lon + dlon},{lat - dlat}",
        },
    )
    best = None
    for item in data or []:
        try:
            meters = distance_m(lat, lon, float(item["lat"]), float(item["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if best is None or meters < best[0]:
            best = (meters, item)
    if best is None:
        return None
    meters, item = best
    address = item.get("address") or {}
    name = item.get("name") or address.get("amenity") or "Tankstation"
    return {
        "naam": name,
        "straat": _street(address),
        "plaats": _place(address),
        "meter": round(meters),
    }
