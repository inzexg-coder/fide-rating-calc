import json
import aiohttp
import asyncio
from typing import AsyncIterator, Optional
from datetime import datetime, timezone

from fide_titles import FIDE_TITLES, TC_MAP


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


async def _lich_get_user(session, username: str) -> dict:
    """Fetch Lichess user profile (for full name and title info)."""
    async with session.get(LICHESS_USER_URL.format(username=username),
                           timeout=aiohttp.ClientTimeout(total=10)) as resp:
        if resp.status == 200:
            return await resp.json()
        return {}


async def _lich_user_name(session, username: str) -> str:
    """Get real name from Lichess profile if available."""
    data = await _lich_get_user(session, username)
    profile = data.get("profile") or {}
    first = profile.get("firstName", "")
    last = profile.get("lastName", "")
    if first or last:
        return f"{first} {last}".strip()
    # Fall back to display name
    return data.get("username", username)


async def _lich_profile_fide(session, username: str) -> Optional[int]:
    """Get FIDE rating from Lichess profile (user-entered, may be inflated)."""
    data = await _lich_get_user(session, username)
    fide = data.get("fideRating")
    if fide and isinstance(fide, (int, float)) and fide > 0:
        return int(fide)
    return None


async def fetch_lichess_games(username: str, max_games: int = 200, progress=None) -> list[GameRecord]:
    """Fetch ALL games for a user from Lichess."""
    games: list[GameRecord] = []
    user_color_map = {}  # cache: opponent -> user's color in the game

    async with aiohttp.ClientSession() as session:
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
        return None  # user not in this game

    opp_user = opp.get("user") or {}
    opp_title = opp_user.get("title", "") or ""

    # Only keep games with titled opponents if a title exists
    # (we still need non-titled games for the user's rating history)
    # Actually, we keep ALL games for the user's rating history timeline

    ts = raw.get("createdAt", 0)  # milliseconds
    if ts < 1e12:
        ts *= 1000  # convert seconds to ms if needed
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)

    # Get accuracy from analysis
    user_analysis = user.get("analysis") or {}
    opp_analysis = opp.get("analysis") or {}
    user_acc = user_analysis.get("accuracy")
    opp_acc = opp_analysis.get("accuracy")

    return GameRecord(
        game_id=raw.get("id"),
        date=dt,
        speed=raw.get("speed"),
        time_class=raw.get("speed"),
        user_rating=user.get("rating"),
        opponent=opp_user.get("name", "?"),
        opponent_rating=opp.get("rating"),
        opponent_title=opp_title if opp_title in FIDE_TITLES else "",
        user_accuracy=user_acc,
        opponent_accuracy=opp_acc,
        user_color=user_color,
        result=raw.get("status"),
        platform="lichess",
        raw=raw,
    )


# ── Chess.com ────────────────────────────────────────────────────────

CHESSCOM_ARCHIVES_URL = "https://api.chess.com/pub/player/{username}/games/archives"
CHESSCOM_PLAYER_URL = "https://api.chess.com/pub/player/{username}"


async def _cc_get_user(session, username: str) -> dict:
    async with session.get(CHESSCOM_PLAYER_URL.format(username=username),
                           timeout=aiohttp.ClientTimeout(total=10)) as resp:
        if resp.status == 200:
            return await resp.json()
        return {}


async def _cc_user_name(session, username: str) -> str:
    data = await _cc_get_user(session, username)
    return data.get("name", username)


async def fetch_chesscom_games(username: str, max_games: int = 200, progress=None) -> list[GameRecord]:
    """Fetch ALL games for a user from Chess.com (month by month)."""
    games: list[GameRecord] = []

    async with aiohttp.ClientSession() as session:
        # Get available archives
        async with session.get(CHESSCOM_ARCHIVES_URL.format(username=username),
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
            await asyncio.sleep(0.3)  # be nice

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

    # Chess.com game export does NOT include titles directly.
    # We'll check titles later by fetching opponent profiles.
    # For now, mark opponent_title as empty — we enrich later.
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
    async with aiohttp.ClientSession() as session:
        for opp in opponent_usernames:
            data = await _cc_get_user(session, opp)
            title = data.get("title", "") or ""
            if title and title in FIDE_TITLES:
                result[opp] = title
            # Chess.com also has `fide` field — but we use FIDE API separately
            await asyncio.sleep(0.3)
    return result


async def enrich_chesscom_titles(games: list[GameRecord]):
    """Enrich Chess.com games with opponent titles by fetching profiles."""
    # Collect unique opponents
    opponents = {g.opponent for g in games if g.opponent != "?"}
    titles = await _cc_opponent_titles(None, opponents)
    for g in games:
        g.opponent_title = titles.get(g.opponent, "")
