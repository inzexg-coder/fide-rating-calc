import asyncio
import statistics
from datetime import datetime, timezone
from typing import Optional
import aiohttp

from fide_titles import FIDE_TITLES, TC_MAP
from fetchers import GameRecord, fetch_lichess_games, fetch_chesscom_games, enrich_chesscom_titles
from fide_client import FIDEClient


class Anchor:
    """A titled opponent whose FIDE rating we found, serving as a rating anchor."""
    __slots__ = (
        "game_id", "date", "time_class", "fide_category",
        "opponent", "opponent_title",
        "platform_rating", "fide_rating",
        "offset", "weight",
        "user_accuracy", "opponent_accuracy",
        "direct",
    )

    def __init__(self, game: GameRecord, title: str, fide_rating: int, fide_category: str, direct: bool = True, weight: float = 1.0):
        self.game_id = game.game_id
        self.date = game.date
        self.time_class = game.time_class
        self.fide_category = fide_category
        self.opponent = game.opponent
        self.opponent_title = title
        self.platform_rating = game.opponent_rating or 0
        self.fide_rating = fide_rating
        self.offset = fide_rating - self.platform_rating if self.platform_rating else 0
        self.user_accuracy = game.user_accuracy
        self.opponent_accuracy = game.opponent_accuracy
        self.direct = direct
        self.weight = self._compute_weight() if weight is None else weight

    def _compute_weight(self) -> float:
        w = 1.0
        ua = self.user_accuracy
        oa = self.opponent_accuracy
        if ua is not None and oa is not None:
            w *= (ua / 100.0) * (oa / 100.0)
        elif ua is not None:
            w *= (ua / 100.0)
        elif oa is not None:
            w *= (oa / 100.0)
        if not self.direct:
            w *= 0.5
        return max(w, 0.01)

    def to_dict(self):
        return {
            "game_id": self.game_id,
            "date": self.date.isoformat(),
            "time_class": self.time_class,
            "fide_category": self.fide_category,
            "opponent": self.opponent,
            "title": self.opponent_title,
            "platform_rating": self.platform_rating,
            "fide_rating": self.fide_rating,
            "offset": self.offset,
            "weight": round(self.weight, 3),
            "direct": self.direct,
            "accuracy": {
                "user": self.user_accuracy,
                "opponent": self.opponent_accuracy,
            }
        }


class DailyEstimate:
    __slots__ = ("date", "user_platform_rating", "estimated_fide", "num_anchors", "total_weight", "avg_offset")

    def __init__(self, date, user_rating, fide=None, n_anchors=0, total_w=0, avg_off=0):
        self.date = date
        self.user_platform_rating = user_rating
        self.estimated_fide = fide
        self.num_anchors = n_anchors
        self.total_weight = total_w
        self.avg_offset = avg_off

    def to_dict(self):
        return {
            "date": self.date.isoformat() if hasattr(self.date, "isoformat") else str(self.date),
            "user_platform_rating": self.user_platform_rating,
            "estimated_fide": self.estimated_fide,
            "num_anchors": self.num_anchors,
            "total_weight": round(self.total_weight, 3),
            "avg_offset": round(self.avg_offset, 1),
        }


class Estimator:
    def __init__(self):
        self.fide = FIDEClient()

    async def estimate(self, platform: str, username: str) -> dict:
        if platform == "lichess":
            games = await fetch_lichess_games(username)
        elif platform == "chesscom":
            games = await fetch_chesscom_games(username)
            await enrich_chesscom_titles(games)
        else:
            raise ValueError(f"Unknown platform: {platform}")

        if not games:
            return {"error": "No games found", "games_count": 0}

        by_tc: dict[str, list[GameRecord]] = {}
        for g in games:
            by_tc.setdefault(g.time_class, []).append(g)

        results = {}
        for tc, tc_games in by_tc.items():
            try:
                result = await self._process_time_control(tc, tc_games, platform)
                results[tc] = result
            except Exception as e:
                results[tc] = {"error": str(e)}

        return {
            "username": username,
            "platform": platform,
            "total_games": len(games),
            "time_controls": results,
            "cache_stats": self.fide.get_cache_stats(),
        }

    async def _process_time_control(self, tc: str, games: list, platform: str) -> dict:
        fide_cat = TC_MAP.get(tc, "standard")

        async with aiohttp.ClientSession() as session:
            # Step 1: Find direct anchors (titled opponents)
            direct_anchors = await self._find_direct_anchors(games, fide_cat, session)

            # Step 2: If no direct anchors, try chain search
            all_anchors = list(direct_anchors)
            if not all_anchors:
                indirect = await self._find_indirect_anchors(games, tc, platform, fide_cat, session)
                all_anchors = indirect

        # Step 3: Build daily estimates with cumulative anchors
        daily = self._build_daily_estimates(games, all_anchors)

        # Current estimate = last day with anchors
        current = None
        valid_days = [d for d in daily if d.estimated_fide is not None]
        if valid_days:
            last = valid_days[-1]
            current = {
                "estimated_fide": int(round(last.estimated_fide)),
                "user_platform_rating": last.user_platform_rating,
                "num_anchors": last.num_anchors,
                "avg_offset": round(last.avg_offset, 1),
            }

        return {
            "time_class": tc,
            "fide_category": fide_cat,
            "total_games": len(games),
            "direct_anchors": len(direct_anchors),
            "indirect_anchors": len(all_anchors) - len(direct_anchors),
            "total_anchors": len(all_anchors),
            "current_estimate": current,
            "anchors": [a.to_dict() for a in all_anchors],
            "daily_estimates": [d.to_dict() for d in daily],
        }

    async def _find_direct_anchors(self, games: list, fide_cat: str, session: aiohttp.ClientSession) -> list:
        """Find titled opponents and look up their FIDE ratings."""
        from fetchers import _lich_user_name, _cc_user_name

        anchors = []
        for game in games:
            title = game.opponent_title
            if not title or title not in FIDE_TITLES:
                continue

            if game.platform == "lichess":
                name = await _lich_user_name(session, game.opponent)
            elif game.platform == "chesscom":
                name = await _cc_user_name(session, game.opponent)
            else:
                continue

            fide_info = await self.fide.search_player(name, title)
            if fide_info and fide_info.get("found"):
                fide_rating = self.fide.get_fide_rating(fide_info, fide_cat)
                if fide_rating and fide_rating > 0:
                    anchor = Anchor(
                        game=game, title=title,
                        fide_rating=fide_rating,
                        fide_category=fide_cat,
                        direct=True,
                    )
                    anchors.append(anchor)

        return anchors

    async def _find_indirect_anchors(self, games: list, tc: str, platform: str, fide_cat: str, session: aiohttp.ClientSession) -> list:
        """If no direct anchors, take the last opponent and search through their games."""
        if not games:
            return []

        last_game = games[-1]
        last_opponent = last_game.opponent

        try:
            if platform == "lichess":
                opp_games = await fetch_lichess_games(last_opponent)
            elif platform == "chesscom":
                opp_games = await fetch_chesscom_games(last_opponent)
                await enrich_chesscom_titles(opp_games)
            else:
                return []

            opp_tc_games = [g for g in opp_games if g.time_class == tc]
            indirect = await self._find_direct_anchors(opp_tc_games, fide_cat, session)
            for a in indirect:
                a.direct = False
                a.weight = a._compute_weight() * 0.5
            return indirect
        except Exception:
            return []

    def _build_daily_estimates(self, games: list, anchors: list) -> list:
        if not games:
            return []

        anchors_sorted = sorted(anchors, key=lambda a: a.date)
        estimates = []
        cumulative = []
        idx = 0

        for game in games:
            while idx < len(anchors_sorted):
                a = anchors_sorted[idx]
                if a.date <= game.date:
                    cumulative.append(a)
                    idx += 1
                else:
                    break

            if cumulative and game.user_rating is not None:
                total_w = sum(a.weight for a in cumulative)
                if total_w > 0:
                    avg_offset = sum(a.offset * a.weight for a in cumulative) / total_w
                    estimated = game.user_rating + avg_offset
                else:
                    avg_offset = 0
                    estimated = None
            else:
                total_w = 0
                avg_offset = 0
                estimated = None

            estimates.append(DailyEstimate(
                date=game.date,
                user_rating=game.user_rating,
                fide=int(round(estimated)) if estimated is not None else None,
                n_anchors=len(cumulative),
                total_w=total_w,
                avg_off=avg_offset,
            ))

        return estimates
