import json
import os
import aiohttp
import asyncio
from typing import Optional
from pathlib import Path

CACHE_DIR = str(Path(__file__).parent.parent / "cache")
CACHE_PATH = os.path.join(CACHE_DIR, "fide_cache.json")
FIDE_SEARCH_URL = "https://ratings.fide.com/api/players/search"
REQUEST_DELAY = 0.5


class FIDEClient:
    def __init__(self):
        self._data: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._load_cache()

    def _load_cache(self):
        if os.path.exists(CACHE_PATH):
            try:
                with open(CACHE_PATH) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _save_cache(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def _cache_key(self, name: str, title: str) -> str:
        return f"{name.strip().lower()}|{title}"

    def get_cached(self, name: str, title: str) -> Optional[dict]:
        return self._data.get(self._cache_key(name, title))

    def set_cached(self, name: str, title: str, data: dict):
        self._data[self._cache_key(name, title)] = data
        self._save_cache()

    async def _rate_limited_request(self, session: aiohttp.ClientSession, url: str, params: dict = None) -> dict:
        now = asyncio.get_event_loop().time()
        wait = REQUEST_DELAY - (now - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = asyncio.get_event_loop().time()
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                return await resp.json()
            return {}

    async def search_player(self, name: str, title: str) -> Optional[dict]:
        cached = self.get_cached(name, title)
        if cached:
            return cached

        async with self._lock:
            async with aiohttp.ClientSession() as session:
                results = await self._rate_limited_request(
                    session, FIDE_SEARCH_URL, {"query": name}
                )

        if not results:
            self.set_cached(name, title, {"found": False})
            return None

        if isinstance(results, list):
            for r in results:
                rt = (r.get("title", "") or "").upper()
                if rt == title.upper():
                    result = self._extract_ratings(r)
                    self.set_cached(name, title, result)
                    return result
            # Fallback: best match
            if results:
                r = results[0]
                result = self._extract_ratings(r)
                result["fuzzy"] = True
                self.set_cached(name, title, result)
                return result
        elif isinstance(results, dict) and results.get("fide_id"):
            result = self._extract_ratings(results)
            self.set_cached(name, title, result)
            return result

        self.set_cached(name, title, {"found": False})
        return None

    def _extract_ratings(self, r: dict) -> dict:
        return {
            "found": True,
            "fide_id": r.get("fide_id") or r.get("id"),
            "name": r.get("name", ""),
            "title": r.get("title", ""),
            "country": r.get("country", ""),
            "standard": (r.get("standard_rating") or r.get("rating") or r.get("standard") or None),
            "rapid": (r.get("rapid_rating") or r.get("rapid") or None),
            "blitz": (r.get("blitz_rating") or r.get("blitz") or None),
            "fuzzy": False,
        }

    def get_fide_rating(self, info: dict, fide_category: str) -> Optional[int]:
        if not info or not info.get("found"):
            return None
        val = info.get(fide_category)
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    def get_cache_stats(self) -> dict:
        return {
            "total_entries": len(self._data),
            "found": sum(1 for v in self._data.values() if v.get("found")),
            "not_found": sum(1 for v in self._data.values() if not v.get("found")),
        }
