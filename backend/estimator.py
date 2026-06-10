import asyncio
import json
import statistics
import os
from datetime import datetime, timezone
from typing import Optional
import aiohttp

from fide_titles import FIDE_TITLES, TC_MAP
from fetchers import GameRecord, fetch_lichess_games, fetch_chesscom_games, enrich_chesscom_titles, FIDE_TITLE_RATINGS
from fetchers import _lich_profile_fide, _lich_get_user, _make_session, _lich_user_title, check_lichess_reachable, reset_lichess_reachable_cache, _lookup_fide_by_realname
from fetchers import _cc_opponent_fide
from regression import estimate_via_regression

CROWD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cache", "crowd_offsets.json")

def _load_crowd():
    try:
        with open(CROWD_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_crowd(data: dict):
    os.makedirs(os.path.dirname(CROWD_PATH), exist_ok=True)
    with open(CROWD_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _bracket(rating: int) -> str:
    low = (rating // 100) * 100
    return f"{low}-{low+99}"


class Anchor:
    """A titled (or FIDE-rated) opponent serving as a rating Anchor."""
    __slots__ = (
        "game_id", "date", "time_class", "fide_category",
        "opponent", "opponent_title",
        "platform_rating", "fide_rating",
        "raw_offset", "adjusted_offset", "weight",
        "user_accuracy", "opponent_accuracy",
        "direct", "is_titled",
    )

    def __init__(self, game: GameRecord, title: str, fide_rating: int,
                 fide_category: str, direct: bool = True, is_titled: bool = True):
        self.game_id = game.game_id
        self.date = game.date
        self.time_class = game.time_class
        self.fide_category = fide_category
        self.opponent = game.opponent
        self.opponent_title = title
        self.platform_rating = game.opponent_rating or 0
        self.fide_rating = fide_rating
        self.raw_offset = fide_rating - self.platform_rating if self.platform_rating else 0
        self.user_accuracy = game.user_accuracy
        self.opponent_accuracy = game.opponent_accuracy
        self.direct = direct
        self.is_titled = is_titled
        self.adjusted_offset = self._adjust_offset()
        self.weight = self._compute_weight()

    def _avg_accuracy(self) -> float:
        ua = self.user_accuracy
        oa = self.opponent_accuracy
        if ua is not None and oa is not None:
            return (ua + oa) / 200.0
        if ua is not None:
            return ua / 100.0
        if oa is not None:
            return oa / 100.0
        return 0.5

    def _adjust_offset(self) -> float:
        acc = self._avg_accuracy()
        # multiplier: 0.5 → 1.0,  0.75 → 1.125,  1.0 → 1.25
        multiplier = 1.0 + (acc - 0.5) * 0.5
        return self.raw_offset * multiplier

    def _compute_weight(self) -> float:
        w = 1.0
        acc = self._avg_accuracy()
        w *= max(acc, 0.1)
        if not self.direct:
            w *= 0.5
        if not self.is_titled:
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
            "raw_offset": self.raw_offset,
            "adjusted_offset": round(self.adjusted_offset, 1),
            "weight": round(self.weight, 3),
            "direct": self.direct,
            "is_titled": self.is_titled,
            "accuracy": {
                "user": self.user_accuracy,
                "opponent": self.opponent_accuracy,
            }
        }


class DailyEstimate:
    __slots__ = ("date", "user_platform_rating", "estimated_fide",
                 "num_anchors", "total_weight", "avg_offset")

    def __init__(self, date, user_rating, fide=None, n_anchors=0,
                 total_w=0, avg_off=0):
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
            "avg_offset": round(self.avg_offset, 2),
        }


class Estimator:
    """Main estimation engine with 6-level cascade."""

    def __init__(self, progress_callback=None):
        self._progress = progress_callback

    async def _report(self, step: str, message: str, percent: int):
        if self._progress:
            await self._progress(step, message, min(percent, 99))


    async def estimate_from_games(self, platform: str, username: str, games: list) -> dict:
        """Run FIDE estimation on pre-fetched games (client-provided)."""
        if not games:
            return {"error": "No games provided", "games_count": 0}

        # Ensure ascending date order
        games.sort(key=lambda g: g.date)

        # Merge correspondence→classical for unified display, skip bullet
        games = [g for g in games if g.time_class != "bullet"]
        for g in games:
            if g.time_class == "correspondence":
                g.time_class = "classical"

        by_tc = {}
        for g in games:
            by_tc.setdefault(g.time_class, []).append(g)

        results = {}
        tc_list = list(by_tc.items())
        for idx, (tc, tc_games) in enumerate(tc_list):
            pct = 15 + int(60 * (idx + 1) / len(tc_list))
            await self._report("analyze", f"[{idx+1}/{len(tc_list)}] {tc} ({len(tc_games)} партий)...", pct)
            try:
                result = await self._process_time_control(tc, tc_games, platform, username, skip_online=True)
                results[tc] = result
            except Exception as e:
                results[tc] = {"error": str(e)}
                await self._report("analyze", f"Ошибка в {tc}: {str(e)}", pct)

        self._save_crowd_data(results)
        await self._report("complete", "Анализ завершён!", 100)

        # Calculate average accuracy across all games
        all_accs = [g.user_accuracy for g in games if g.user_accuracy is not None]
        avg_accuracy = round(sum(all_accs) / len(all_accs), 1) if all_accs else None

        # Collect all anchors from all time controls into a top-level list
        all_anchors = []
        for tc_data in results.values():
            if isinstance(tc_data, dict) and "anchors" in tc_data:
                for a in tc_data["anchors"]:
                    all_anchors.append(a)

        return {
            "username": username,
            "platform": platform,
            "total_games": len(games),
            "average_accuracy": avg_accuracy,
            "time_controls": results,
            "anchors": all_anchors,
        }



    async def estimate(self, platform: str, username: str) -> dict:
        await self._report("fetch", f"Загрузка партий с {platform}...", 5)
        if platform == "lichess":
            games = await fetch_lichess_games(username, progress=self._progress)
        elif platform == "chesscom":
            games = await fetch_chesscom_games(username, progress=self._progress)
            await enrich_chesscom_titles(games)
        else:
            raise ValueError(f"Unknown platform: {platform}")

        if not games:
            await self._report("error", "Партий не найдено", 100)
            return {"error": "No games found", "games_count": 0}

        # Merge correspondence→classical for unified display, skip bullet
        games = [g for g in games if g.time_class != "bullet"]
        for g in games:
            if g.time_class == "correspondence":
                g.time_class = "classical"

        by_tc = {}
        for g in games:
            by_tc.setdefault(g.time_class, []).append(g)

        await self._report("analyze", f"Найдено {len(games)} партий, {len(by_tc)} контролей. Анализирую...", 15)

        results = {}
        tc_list = list(by_tc.items())
        for idx, (tc, tc_games) in enumerate(tc_list):
            pct = 15 + int(65 * (idx + 1) / len(tc_list))
            await self._report("analyze", f"[{idx+1}/{len(tc_list)}] {tc} ({len(tc_games)} партий)...", pct)
            try:
                result = await self._process_time_control(tc, tc_games, platform, username, skip_online=True)
                results[tc] = result
            except Exception as e:
                results[tc] = {"error": str(e)}
                await self._report("analyze", f"Ошибка в {tc}: {str(e)}", pct)

        # Save crowd data after analysis
        self._save_crowd_data(results)

        await self._report("complete", "Анализ завершён!", 100)

        # Calculate average accuracy across all games
        all_accs = [g.user_accuracy for g in games if g.user_accuracy is not None]
        avg_accuracy = round(sum(all_accs) / len(all_accs), 1) if all_accs else None

        # Collect all anchors from all time controls into a top-level list
        all_anchors = []
        for tc_data in results.values():
            if isinstance(tc_data, dict) and "anchors" in tc_data:
                for a in tc_data["anchors"]:
                    all_anchors.append(a)

        return {
            "username": username,
            "platform": platform,
            "total_games": len(games),
            "average_accuracy": avg_accuracy,
            "time_controls": results,
            "anchors": all_anchors,
        }

    # ── Process one time control ────────────────────────────────────

    async def _process_time_control(self, tc: str, games: list, platform: str, username: str, skip_online: bool = False) -> dict:
        fide_cat = TC_MAP.get(tc, "standard")

        async with _make_session() as session:
            # Check Lichess reachability (skip if client provided games - Lichess is unreachable from server)
            if skip_online:
                lichess_ok = False
                await self._report("fetch", "⚠ Lichess API недоступен (режим оффлайн). Используются только переданные данные.", 55)
            else:
                lichess_ok = await check_lichess_reachable(session)
                if not lichess_ok:
                    await self._report("fetch", "⚠ Lichess API недоступен с сервера. Используются только регрессионные формулы.", 55)

            # Step 1: Titled Anchors — use Lichess profile FIDE (or Chess.com profile FIDE)
            titled = await self._find_titled_references(games, fide_cat, session, lichess_ok)
            await self._report("anchors", f"Найдено {len(titled)} титулованных якорей в {tc}", 60)

            all_anchors = list(titled)

            # Step 1a: Non-titled regression anchors (always, even offline)
            nontitled_reg = await self._find_nontitled_regression_references(games, fide_cat, platform, tc)
            if nontitled_reg:
                await self._report("anchors", f"Плюс {len(nontitled_reg)} не-титулованных якорей (регрессия)", 61)
                all_anchors.extend(nontitled_reg)

            # Step 1b: User's own FIDE from profile (Lichess)
            if lichess_ok:
                user_fide = await self._find_user_own_fide(platform, username, games, fide_cat, session)
                if user_fide:
                    await self._report("anchors", f"Твой FIDE из профиля: {user_fide}", 62)
                    all_anchors.append(user_fide)

            # Step 1c: Profile FIDE Anchors (opponents with FIDE in Lichess profile)
            if lichess_ok:
                profile = await self._find_profile_fide_references(games, fide_cat, session)
                if profile:
                    await self._report("anchors", f"Плюс {len(profile)} якорей из профилей Lichess", 63)
                    all_anchors.extend(profile)

            # Step 2: Non-titled FIDE Anchors (any opponent with FIDE in profile)
            if lichess_ok:
                nontitled = await self._find_nontitled_references(games, fide_cat, session)
                all_anchors.extend(nontitled)
                if nontitled:
                    await self._report("anchors", f"Плюс {len(nontitled)} не-титулованных якорей с FIDE", 65)

            # Step 3: Chain search (only if Lichess is reachable)
            if not all_anchors and lichess_ok:
                await self._report("chain", "Якорей нет. Ищу косвенные через последнего соперника...", 70)
                indirect = await self._find_indirect_anchors(games, tc, platform, fide_cat, session)
                all_anchors = indirect
                if indirect:
                    await self._report("anchors", f"Найдено {len(indirect)} косвенных якорей", 75)

            # Step 4: Crowd offsets
            if not all_anchors:
                await self._report("anchors", "Якорей не найдено. Fallback: краудсорсинг...", 75)
                crowd = self._get_crowd_fallback(games, fide_cat)
                if crowd is not None:
                    await self._report("anchors", f"Fallback: краудсорсинг (offset={crowd:+.0f})", 78)
                    fake_anchor = self._make_crowd_anchor(games, crowd, fide_cat)
                    all_anchors = [fake_anchor]

        # Step 5: If still NO Anchors, use accuracy-adjusted formula
        has_Anchors = bool(all_anchors)
        if not has_Anchors:
            await self._report("estimate", "Построение графика на основе точности и рейтинга...", 80)
            daily = self._build_regression_daily(games, platform, tc, fide_cat)
        else:
            daily = self._build_daily_estimates(games, all_anchors)

        await self._report("estimate", f"Построение графика по {len(daily)} точкам...", 85)

        current_fide = None
        max_fide = None
        user_rating = None
        valid_days = [d for d in daily if d.estimated_fide is not None]
        if valid_days:
            last = valid_days[-1]
            current_fide = int(round(last.estimated_fide))
            user_rating = last.user_platform_rating
            max_fide = max(int(round(d.estimated_fide)) for d in valid_days)

        return {
            "time_class": tc,
            "fide_category": fide_cat,
            "total_games": len(games),
            "direct_anchors": len([a for a in all_anchors if a.direct and a.is_titled]),
            "indirect_anchors": len([a for a in all_anchors if not a.direct]),
            "total_anchors": len(all_anchors),
            "has_anchors": has_Anchors,
            "current_fide": current_fide,
            "max_fide": max_fide,
            "user_platform_rating": user_rating,
            "anchors": [a.to_dict() for a in all_anchors],
            "daily_estimates": [d.to_dict() for d in daily],
        }

    # ── NEW: User's own FIDE from profile (Lichess) ────────────────

    async def _find_user_own_fide(self, platform: str, username: str, games: list,
                                   fide_cat: str, session) -> Optional[Anchor]:
        """Get user's own FIDE rating from their Lichess profile, if valid.
        Only use if fide < 2000 and fide < user's platform rating (avoid inflation)."""
        if platform != "lichess":
            return None
        try:
            profile_fide = await _lich_profile_fide(session, username)
        except Exception:
            return None
        if profile_fide is None:
            return None

        last_rating = None
        for g in reversed(games):
            if g.user_rating:
                last_rating = g.user_rating
                break
        if last_rating is None:
            return None

        if profile_fide >= 2000:
            return None
        if profile_fide >= last_rating:
            return None

        class FakeGame:
            pass
        fake = FakeGame()
        fake.game_id = None
        fake.date = games[-1].date if games else datetime.now(tz=timezone.utc)
        fake.time_class = games[0].time_class if games else fide_cat
        fake.opponent = f"(свой FIDE: {profile_fide})"
        fake.opponent_rating = 0
        fake.opponent_title = ""
        fake.user_accuracy = None
        fake.opponent_accuracy = None
        fake.platform = "lichess"

        offset = profile_fide - last_rating
        a = Anchor.__new__(Anchor)
        a.game_id = None
        a.date = fake.date
        a.time_class = fake.time_class
        a.fide_category = fide_cat
        a.opponent = f"(свой FIDE: {profile_fide})"
        a.opponent_title = ""
        a.platform_rating = last_rating
        a.fide_rating = profile_fide
        a.raw_offset = offset
        a.adjusted_offset = offset
        a.user_accuracy = None
        a.opponent_accuracy = None
        a.direct = True
        a.is_titled = False
        a.weight = 0.25
        return a



    async def _find_titled_references(self, games: list, fide_cat: str,
                                   session, lichess_ok: bool = True) -> list:
        """Find titled opponents and look up their FIDE ratings via Lichess profile."""
        Anchors = []
        for game in games:
            title = game.opponent_title
            if not title or title not in FIDE_TITLES:
                continue

            fide_rating = None
            found_title = title

            # Try to get real name from Lichess profile if available
            real_name = None
            if game.platform == "lichess" and lichess_ok:
                try:
                    profile_data = await _lich_get_user(session, game.opponent)
                    real_name = profile_data.get("profile", {}).get("realName", "")
                except Exception:
                    pass

            # First priority: Use FIDE lookup by real name
            if real_name:
                try:
                    looked_up = await asyncio.wait_for(
                        _lookup_fide_by_realname(session, real_name, title, game.opponent), 
                        timeout=5.0
                    )
                    if looked_up:
                        fide_rating, found_title = looked_up
                except (asyncio.TimeoutError, Exception):
                    pass

            # Second priority: Direct profile FIDE lookup (fallback to original method)
            if fide_rating is None:
                if game.platform == "lichess" and lichess_ok:
                    try:
                        profile_fide = await asyncio.wait_for(
                            _lich_profile_fide(session, game.opponent), timeout=5.0
                        )
                    except (asyncio.TimeoutError, Exception):
                        profile_fide = None
                    fide_rating = profile_fide
                    if fide_rating is not None:
                        found_title = title

                elif game.platform == "chesscom":
                    try:
                        profile_fide = await _cc_opponent_fide(session, game.opponent)
                    except Exception:
                        profile_fide = None
                    fide_rating = profile_fide
                    if fide_rating is not None:
                        found_title = title

            # Third priority: Use default FIDE rating based on title
            if fide_rating is None:
                if title in FIDE_TITLE_RATINGS:
                    fide_rating = FIDE_TITLE_RATINGS[title]
                    found_title = title

            # If we found a FIDE rating, create the Anchor
            if fide_rating is not None:
                Anchors.append(Anchor(
                    game=game, title=found_title,
                    fide_rating=fide_rating,
                    fide_category=fide_cat,
                    direct=True, is_titled=True,
                ))

        return Anchors

    async def _find_nontitled_references(self, games: list, fide_cat: str,
                                      session) -> list:
        """Search ANY opponent (without a title) who has FIDE in their profile."""
        Anchors = []
        searched = set()
        max_search = 15
        for game in games:
            if game.opponent_title and game.opponent_title.upper() in FIDE_TITLES:
                continue
            if not game.opponent or game.opponent in searched:
                continue
            searched.add(game.opponent)
            if len(searched) > max_search:
                break

            fide_rating = None
            if game.platform == "lichess":
                try:
                    fide_rating = await _lich_profile_fide(session, game.opponent)
                except Exception:
                    continue
            elif game.platform == "chesscom":
                try:
                    fide_rating = await _cc_opponent_fide(session, game.opponent)
                except Exception:
                    continue

            if fide_rating is None:
                continue

            Anchors.append(Anchor(
                game=game, title=game.opponent_title or "",
                fide_rating=fide_rating,
                fide_category=fide_cat,
                direct=True, is_titled=False,
            ))
        return Anchors

    async def _find_nontitled_regression_references(self, games: list, fide_cat: str,
                                                    platform: str, tc: str) -> list:
        """Use regression-estimated FIDE for non-titled opponents as weak anchors.
        These have lower weight than titled anchors but provide additional signal."""
        from regression import estimate_via_regression
        Anchors = []
        for game in games:
            if game.opponent_title and game.opponent_title.upper() in FIDE_TITLES:
                continue  # already handled by titled references
            if not game.opponent_rating:
                continue
            fide = estimate_via_regression(platform, tc, fide_cat, game.opponent_rating)
            if fide is None:
                continue
            a = Anchor(
                game=game, title="",
                fide_rating=fide,
                fide_category=fide_cat,
                direct=False,
                is_titled=False,
            )
            a.weight = 0.15  # lower weight than titled (0.5+) or non-titled profile (0.3)
            Anchors.append(a)
        return Anchors

    # ── Profile FIDE Anchors (existing, more stringent checks) ──────

    async def _find_profile_fide_references(self, games: list, fide_cat: str,
                                          session) -> list:
        """Use FIDE ratings set by opponents on their Lichess profile (may be inflated).
        Only use if: fide < 2000 and fide < opponent's platform rating (not inflated)."""
        Anchors = []
        checked = set()
        limit = 10

        for game in games:
            if game.platform != "lichess":
                continue
            if not game.opponent or game.opponent in checked:
                continue
            checked.add(game.opponent)
            if len(checked) > limit:
                break

            try:
                profile_fide = await _lich_profile_fide(session, game.opponent)
            except Exception:
                continue
            if profile_fide is None:
                continue

            if profile_fide >= 2000:
                continue
            if game.opponent_rating and profile_fide >= game.opponent_rating:
                continue

            a = Anchor(
                game=game, title=game.opponent_title or "",
                fide_rating=profile_fide,
                fide_category=fide_cat,
                direct=True,
                is_titled=bool(game.opponent_title and game.opponent_title.upper() in FIDE_TITLES),
            )
            a.weight = 0.3
            Anchors.append(a)

        return Anchors


    async def _find_indirect_anchors(self, games: list, tc: str, platform: str,
                                     fide_cat: str, session) -> list:
        if not games:
            return []

        last_game = games[-1]
        last_opponent = last_game.opponent

        await self._report("chain", f"Загрузка последних партий {last_opponent}...", 71)

        try:
            if platform == "lichess":
                opp_games = await asyncio.wait_for(
                    fetch_lichess_games(last_opponent, max_games=150),
                    timeout=30.0
                )
            elif platform == "chesscom":
                opp_games = await asyncio.wait_for(
                    fetch_chesscom_games(last_opponent, max_games=150),
                    timeout=30.0
                )
                await enrich_chesscom_titles(opp_games)
            else:
                return []

            if not opp_games:
                return []

            await self._report("chain", f"Загружено {len(opp_games)} партий {last_opponent}", 73)

            opp_tc_games = [g for g in opp_games if g.time_class == tc]
            if not opp_tc_games:
                return []

            await self._report("chain", f"Поиск источников в {len(opp_tc_games)} партиях {tc}...", 74)

            indirect = await self._find_titled_references(opp_tc_games, fide_cat, session)
            indirect += await self._find_nontitled_references(opp_tc_games, fide_cat, session)

            for a in indirect:
                a.direct = False
                a.weight = a._compute_weight() * 0.5
            return indirect

        except asyncio.TimeoutError:
            await self._report("chain", "Таймаут цепочки. Пропускаем.", 75)
            return []
        except Exception:
            return []


    def _build_regression_daily(self, games: list, platform: str,
                                 tc: str, fide_cat: str) -> list:
        """Build daily estimates using accuracy-adjusted formula.
        High accuracy → FIDE closer to platform rating.
        Low accuracy → FIDE lower.
        Groups games by date, using the last game's rating per day."""
        from collections import defaultdict

        # Pre‑compute overall accuracy stats for this TC
        all_accs = [g.user_accuracy for g in games if g.user_accuracy is not None]
        avg_acc = sum(all_accs) / len(all_accs) / 100.0 if all_accs else 0.5

        # Compute average user rating (for games that have it) as fallback for null ratings
        all_ratings = [g.user_rating for g in games if g.user_rating is not None]
        avg_rating = sum(all_ratings) / len(all_ratings) if all_ratings else 1200

        # Group games by date
        daily_map = defaultdict(list)
        for g in games:
            date_key = g.date.date() if hasattr(g.date, 'date') else str(g.date)[:10]
            daily_map[date_key].append(g)

        daily = []
        sorted_dates = sorted(daily_map.keys())

        # Accumulate offset from regression across all games up to each date
        cumulative_offset = 0.0
        for date_key in sorted_dates:
            day_games = daily_map[date_key]
            # Use the last game's rating for this day
            last_game = day_games[-1]
            game_rating = last_game.user_rating if last_game.user_rating else avg_rating

            # Compute offset for each game on this day and average them
            offsets = []
            for g in day_games:
                gr = g.user_rating if g.user_rating else avg_rating
                fide = estimate_via_regression(platform, tc, fide_cat, gr)
                if fide:
                    acc = (g.user_accuracy / 100.0) if g.user_accuracy is not None else avg_acc
                    mult = 0.85 + acc * 0.3
                    fide_adj = max(int(round(fide * mult)), 100)
                    offsets.append(fide_adj - gr)
                else:
                    offsets.append(0)

            avg_offset = sum(offsets) / len(offsets)
            cumulative_offset = avg_offset  # use current day's offset

            estimated = game_rating + cumulative_offset if game_rating else None

            daily.append(DailyEstimate(
                date=last_game.date,
                user_rating=game_rating,
                fide=int(round(estimated)) if estimated is not None else None,
                n_anchors=0,
                total_w=0,
                avg_off=round(cumulative_offset, 2),
            ))
        return daily

    # ── Crowdsourcing ──────────────────────────────────────────────

    def _save_crowd_data(self, results: dict):
        crowd = _load_crowd()
        for tc_name, tc_data in results.items():
            if "error" in tc_data:
                continue
            for a in tc_data.get("anchors", []):
                if a.get("raw_offset") is None:
                    continue
                b = _bracket(a["platform_rating"])
                key = f"{tc_name}:{b}"
                if key not in crowd:
                    crowd[key] = []
                crowd[key].append({
                    "offset": a["raw_offset"],
                    "weight": a["weight"],
                    "is_titled": a["is_titled"],
                })
                crowd[key] = crowd[key][-50:]
        _save_crowd(crowd)

    def _get_crowd_fallback(self, games: list, fide_cat: str) -> Optional[float]:
        if not games:
            return None

        last_rating = None
        for g in reversed(games):
            if g.user_rating:
                last_rating = g.user_rating
                break
        if last_rating is None:
            return None

        crowd = _load_crowd()
        tc_name = games[0].time_class
        b = _bracket(last_rating)
        key = f"{tc_name}:{b}"

        entries = crowd.get(key, [])
        if not entries:
            low = (last_rating // 100) * 100
            for adj in [low - 100, low + 100, low - 200, low + 200]:
                adj_key = f"{tc_name}:{adj}-{adj+99}"
                entries = crowd.get(adj_key, [])
                if entries:
                    break

        if not entries:
            return None

        total_w = sum(e["weight"] for e in entries)
        if total_w <= 0:
            return None
        avg = sum(e["offset"] * e["weight"] for e in entries) / total_w
        return avg

    def _make_crowd_anchor(self, games: list, offset: float, fide_cat: str) -> Anchor:
        class FakeGame:
            pass
        fake = FakeGame()
        fake.game_id = None
        fake.date = games[-1].date if games else datetime.now(tz=timezone.utc)
        fake.time_class = games[0].time_class if games else fide_cat
        fake.opponent = "(crowd)"
        fake.opponent_rating = 0
        fake.opponent_title = ""
        fake.user_accuracy = None
        fake.opponent_accuracy = None
        fake.platform = "crowd"

        a = Anchor.__new__(Anchor)
        a.game_id = None
        a.date = fake.date
        a.time_class = fake.time_class
        a.fide_category = fide_cat
        a.opponent = "(краудсорсинг)"
        a.opponent_title = ""
        a.platform_rating = 0
        a.fide_rating = 0
        a.raw_offset = offset
        a.adjusted_offset = offset
        a.user_accuracy = None
        a.opponent_accuracy = None
        a.direct = False
        a.is_titled = False
        a.weight = 0.3
        return a

    # ── Daily chart builder ────────────────────────────────────────

    def _build_daily_estimates(self, games: list, Anchors: list) -> list:
        if not games:
            return []

        from collections import defaultdict

        # Sort anchors by date
        anchors_sorted = sorted(Anchors, key=lambda a: a.date if a.date else datetime.min.replace(tzinfo=timezone.utc))

        # Group games by date (YYYY-MM-DD)
        games_by_date = defaultdict(list)
        for g in games:
            date_key = g.date.date() if hasattr(g.date, 'date') else str(g.date)[:10]
            games_by_date[date_key].append(g)

        sorted_dates = sorted(games_by_date.keys())
        estimates = []
        cumulative = []
        idx = 0
        anchor_idx = 0

        for date_key in sorted_dates:
            day_games = games_by_date[date_key]
            # Use last game of the day for user_rating
            last_game = day_games[-1]

            # Add any anchors that occurred on or before this date
            while anchor_idx < len(anchors_sorted):
                a = anchors_sorted[anchor_idx]
                anchor_date = a.date if a.date else datetime.min.replace(tzinfo=timezone.utc)
                # Compare dates only (ignore time)
                game_date = last_game.date.date() if hasattr(last_game.date, 'date') else str(last_game.date)[:10]
                if hasattr(anchor_date, 'date'):
                    anchor_date_only = anchor_date.date()
                else:
                    anchor_date_only = str(anchor_date)[:10]
                if anchor_date_only <= game_date:
                    cumulative.append(a)
                    anchor_idx += 1
                else:
                    break

            if cumulative and last_game.user_rating is not None:
                total_w = sum(a.weight for a in cumulative)
                if total_w > 0:
                    sorted_by_offset = sorted(cumulative, key=lambda a: a.adjusted_offset)
                    cum_w = 0
                    median_offset = 0
                    target = total_w / 2
                    for a in sorted_by_offset:
                        cum_w += a.weight
                        if cum_w >= target:
                            median_offset = a.adjusted_offset
                            break
                    estimated = last_game.user_rating + median_offset
                else:
                    median_offset = 0
                    estimated = None
            else:
                total_w = 0
                median_offset = 0
                estimated = None

            estimates.append(DailyEstimate(
                date=last_game.date,
                user_rating=last_game.user_rating,
                fide=int(round(estimated)) if estimated is not None else None,
                n_anchors=len(cumulative),
                total_w=total_w,
                avg_off=median_offset,
            ))

        return estimates
