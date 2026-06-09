import json
import aiohttp
import asyncio
import socket
from typing import AsyncIterator, Optional
from datetime import datetime, timezone

from fide_titles import FIDE_TITLES, TC_MAP
from config import LICHESS_TOKEN

_LICHESS_HEADERS = {"Authorization": f"Bearer {LICHESS_TOKEN}"} if LICHESS_TOKEN else {}

# ── IPv4‑forced connector (Lichess IPv6 is flaky on this server) ─────



def _make_session() -> aiohttp.ClientSession:
    """Return a ClientSession that forces IPv4."""
    import socket
    conn = aiohttp.TCPConnector(family=socket.AF_INET, force_close=True)
    return aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(total=120))

# ── Lichess connectivity check ──────────────────────────────────
_LICHESS_REACHABLE_CACHE = None

async def check_lichess_reachable(session) -> bool:
    """Quick check if Lichess API is reachable (cached for 60s)."""
    global _LICHESS_REACHABLE_CACHE
    if _LICHESS_REACHABLE_CACHE is not None:
        return _LICHESS_REACHABLE_CACHE
    try:
        async with session.get(
            "https://lichess.org/api/user/ericrosen",
            timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            _LICHESS_REACHABLE_CACHE = resp.status == 200
            return _LICHESS_REACHABLE_CACHE
    except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
        _LICHESS_REACHABLE_CACHE = False
        return False


def reset_lichess_reachable_cache():
    """Reset the connectivity cache (for testing)."""
    global _LICHESS_REACHABLE_CACHE
    _LICHESS_REACHABLE_CACHE = None


class GameRecord:
    """Normalised game record from either platform."""
    __slots__ = (
        "game_id", "date", "speed", "time_class",
        "user_rating", "opponent", "opponent_rating", "opponent_title",
        "user_accuracy", "opponent_accuracy",
        "user_color", "result", "platform", "raw"
    )

    def __init__(self, **kwargs):
        for k in self.__slots__:
            setattr(self, k, kwargs.get(k))

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    @property
    def fide_category(self) -> str:
        return TC_MAP.get(self.time_class, "standard")


# ── Lichess ──────────────────────────────────────────────────────────

LICHESS_GAMES_URL = "https://lichess.org/api/games/user/{username}"
LICHESS_USER_URL = "https://lichess.org/api/user/{username}"


async def _lich_stream(session, url, params) -> AsyncIterator[dict]:
    """Iterate over NDJSON lines from Lichess with rate-limit handling."""
    headers = {"Accept": "application/x-ndjson"}
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=180)) as resp:
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    await asyncio.sleep(retry_after)
                    continue
                if resp.status != 200:
                    return
                async for line in resp.content:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
                return
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as exc:
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # exponential backoff
                continue
            return


async def _lich_get_user(session, username: str) -> dict:
    """Fetch Lichess user profile (for full name and title info + FIDE rating)."""
    try:
        async with session.get(LICHESS_USER_URL.format(username=username),
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return await resp.json()
            return {}
    except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
        return {}


async def _lich_user_name(session, username: str) -> str:
    """Get real name from Lichess profile if available."""
    data = await _lich_get_user(session, username)
    profile = data.get("profile") or {}
    first = profile.get("firstName", "")
    last = profile.get("lastName", "")
    if first or last:
        return f"{first} {last}".strip()
    return data.get("username", username)


async def _lich_profile_fide(session, username: str) -> Optional[int]:
    """Get FIDE rating from Lichess profile (user-entered, may be inflated)."""
    data = await _lich_get_user(session, username)
    fide = data.get("fideRating")
    if fide and isinstance(fide, (int, float)) and fide > 0:
        return int(fide)
    return None


async def _lich_user_title(session, username: str) -> Optional[str]:
    """Get the Lichess/FIDE title of a user."""
    data = await _lich_get_user(session, username)
    return data.get("title") or None


async def fetch_lichess_games(username: str, max_games: int = 200, progress=None) -> list[GameRecord]:
    """Fetch ALL games for a user from Lichess."""
    games: list[GameRecord] = []

    async with _make_session() as session:
        params = {
            "max": max_games if max_games else 500,
            "accuracy": "true",
            "opening": "false",
            "evals": "false",
            "moves": "false",
            "sort": "dateDesc",
        }

        async for raw in _lich_stream(session, LICHESS_GAMES_URL.format(username=username), params):
            try:
                rec = _parse_lichess_game(raw, username)
                if rec:
                    games.append(rec)
                    if progress and len(games) % 25 == 0:
                        await progress("fetch", f"Загружено {len(games)} партий...", 5 + int(len(games) / max(max_games, 1) * 20))
            except Exception:
                continue

    games.sort(key=lambda g: g.date)
    if progress:
        await progress("fetch", f"Загружено {len(games)} партий", 25)
    return games


def _parse_lichess_game(raw: dict, target_user: str) -> Optional[GameRecord]:
    """Parse a Lichess game JSON into a GameRecord."""
    if raw.get("speed") not in ("bullet", "blitz", "rapid", "classical", "correspondence"):
        return None

    players = raw.get("players", {})
    white = players.get("white", {})
    black = players.get("black", {})

    # Determine which side is the target user
    wuser = (white.get("user") or {}).get("name", "").lower()
    buser = (black.get("user") or {}).get("name", "").lower()
    target_lower = target_user.lower()

    if wuser == target_lower:
        user = white
        opp = black
        user_color = "white"
    elif buser == target_lower:
        user = black
        opp = white
        user_color = "black"
    else:
        return None  # not our user's game

    opp_user = opp.get("user") or {}
    opponent = opp_user.get("name", opp.get("name", "?"))
    opp_title = opp.get("title") or opp_user.get("title") or ""
    if opp_title:
        opp_title = opp_title.upper()

    user_rating = user.get("rating")
    opp_rating = opp.get("rating")

    ts = raw.get("timestamp") or raw.get("createdAt", 0)
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else datetime.now(tz=timezone.utc)

    # Accuracy is under analysis/accuracy for each side
    analysis = raw.get("analysis") or {}
    user_acc = (user.get("analysis") or {}).get("accuracy") or analysis.get("accuracy")
    opp_acc = (opp.get("analysis") or {}).get("accuracy")

    if user_acc is None:
        user_acc = analysis.get("averageAccuracy") if raw.get("id") else None

    return GameRecord(
        game_id=raw.get("id"),
        date=dt,
        speed=raw.get("speed"),
        time_class=raw.get("speed"),
        user_rating=user_rating,
        opponent=opponent,
        opponent_rating=opp_rating,
        opponent_title=opp_title,
        user_accuracy=user_acc,
        opponent_accuracy=opp_acc,
        user_color=user_color,
        result=raw.get("status"),
        platform="lichess",
        raw=raw,
    )


# ── Chess.com ────────────────────────────────────────────────────────

CHESSCOM_ARCHIVES_URL = "https://api.chess.com/pub/player/{username}/games/archives"
CHESSCOM_USER_URL = "https://api.chess.com/pub/player/{username}"


async def _cc_get_user(session, username: str) -> dict:
    try:
        async with session.get(CHESSCOM_USER_URL.format(username=username),
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return await resp.json()
            return {}
    except (asyncio.TimeoutError, aiohttp.ClientError):
        return {}


async def _cc_user_name(session, username: str) -> str:
    data = await _cc_get_user(session, username)
    return data.get("name", username)


async def _cc_opponent_fide(session, username: str) -> Optional[int]:
    """Get FIDE rating from Chess.com profile if available."""
    data = await _cc_get_user(session, username)
    fide = data.get("fide")
    if fide and isinstance(fide, (int, float)) and fide > 0:
        return int(fide)
    return None


async def fetch_chesscom_games(username: str, max_games: int = 200, progress=None) -> list[GameRecord]:
    """Fetch ALL games for a user from Chess.com (month by month)."""
    games: list[GameRecord] = []

    async with _make_session() as session:
        # Get available archives
        async with session.get(CHESSCOM_ARCHIVES_URL.format(username=username),
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return games
            archives = (await resp.json()).get("archives", [])

        # Only fetch recent months if max_games is set
        if max_games and max_games < 500:
            archives = archives[-3:]  # last 3 months

        # Fetch archives (stop early if we have enough)
        for url in archives:
            if max_games and len(games) >= max_games:
                break
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    for raw in data.get("games", []):
                        if max_games and len(games) >= max_games:
                            break
                        try:
                            rec = _parse_chesscom_game(raw, username)
                            if rec:
                                games.append(rec)
                                if progress and len(games) % 25 == 0:
                                    await progress("fetch", f"Загружено {len(games)} партий...", 5 + int(len(games) / max(max_games, 1) * 20))
                        except Exception:
                            continue
            except (asyncio.TimeoutError, aiohttp.ClientError):
                continue
            await asyncio.sleep(0.1)  # be nice

    games.sort(key=lambda g: g.date)
    return games


def _parse_chesscom_game(raw: dict, target_user: str) -> Optional[GameRecord]:
    """Parse a Chess.com game JSON into a GameRecord."""
    tc = raw.get("time_class", "")
    if tc not in ("bullet", "blitz", "rapid", "daily", "classical"):
        return None

    # Map to our categories
    if tc == "daily":
        tc = "correspondence"
    elif tc == "classical":
        tc = "classical"

    target_lower = target_user.lower()
    white = raw.get("white", {})
    black = raw.get("black", {})

    wuser = white.get("username", "").lower()
    buser = black.get("username", "").lower()

    if wuser == target_lower:
        user = white
        opp = black
        user_color = "white"
    elif buser == target_lower:
        user = black
        opp = white
        user_color = "black"
    else:
        return None

    opp_username = opp.get("username", "?")
    user_rating = user.get("rating")
    opp_rating = opp.get("rating")

    ts = raw.get("end_time", 0)  # unix timestamp
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(tz=timezone.utc)

    user_acc = raw.get(f"accuracy_{user_color}")
    opp_acc = raw.get(f"accuracy_{'black' if user_color == 'white' else 'white'}")

    return GameRecord(
        game_id=raw.get("uuid"),
        date=dt,
        speed=tc,
        time_class=tc,
        user_rating=user_rating,
        opponent=opp_username,
        opponent_rating=opp_rating,
        opponent_title="",  # enriched later
        user_accuracy=user_acc,
        opponent_accuracy=opp_acc,
        user_color=user_color,
        result=raw.get("result_color") or raw.get("result"),
        platform="chesscom",
        raw=raw,
    )


async def _cc_opponent_titles(username: str, opponent_usernames: set[str]) -> dict[str, str]:
    """Fetch Chess.com profiles for all unique opponents to get their titles."""
    result = {}
    async with _make_session() as session:
        for opp in opponent_usernames:
            data = await _cc_get_user(session, opp)
            title = data.get("title", "") or ""
            if title and title in FIDE_TITLES:
                result[opp] = title
            await asyncio.sleep(0.3)
    return result


async def enrich_chesscom_titles(games: list[GameRecord]):
    """Enrich Chess.com games with opponent titles by fetching profiles."""
    opponents = {g.opponent for g in games if g.opponent != "?"}
    titles = await _cc_opponent_titles(None, opponents)
    for g in games:
        g.opponent_title = titles.get(g.opponent, "")
