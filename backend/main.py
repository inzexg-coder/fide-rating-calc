import asyncio
import json
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from estimator import Estimator


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="FIDE Rating Calculator", version="3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ──────────────────────────────────────────────────────────

class EstimateRequest(BaseModel):
    platform: str
    username: str


class GameData(BaseModel):
    game_id: str = ""
    date: str = ""
    speed: str = ""
    time_class: str = ""
    user_rating: int | None = None
    opponent: str = ""
    opponent_rating: int | None = None
    opponent_title: str = ""
    user_accuracy: float | None = None
    opponent_accuracy: float | None = None
    user_color: str = ""
    result: str = ""
    platform: str = "lichess"


class BatchEstimateRequest(BaseModel):
    platform: str
    username: str
    games: list[GameData] = []


# ── Existing endpoints ──────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "3.0"}


@app.post("/api/estimate")
async def estimate(req: EstimateRequest):
    """Server-side fetching (Chess.com works, Lichess may be blocked)."""
    if req.platform not in ("lichess", "chesscom"):
        raise HTTPException(400, "Platform must be 'lichess' or 'chesscom'")
    if not req.username.strip():
        raise HTTPException(400, "Username is required")

    estimator = Estimator()
    try:
        result = await estimator.estimate(req.platform, req.username.strip())
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Internal error: {str(e)}")


@app.get("/api/estimate/stream")
async def estimate_stream(platform: str = Query(...), username: str = Query(...)):
    """SSE endpoint — used by frontend for server-side fetching (Chess.com)."""
    if platform not in ("lichess", "chesscom"):
        raise HTTPException(400, "Platform must be 'lichess' or 'chesscom'")
    if not username.strip():
        raise HTTPException(400, "Username is required")

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()

        async def progress(step: str, message: str, percent: int):
            await queue.put(("progress", step, message, percent))

        async def run():
            try:
                estimator = Estimator(progress_callback=progress)
                result = await estimator.estimate(platform, username.strip())
                await queue.put(("result", result))
            except Exception as e:
                await queue.put(("error", str(e)))

        task = asyncio.create_task(run())

        yield f"event: progress\ndata: {json.dumps({'step': 'fetch', 'message': 'Начинаю анализ...', 'percent': 0}, ensure_ascii=False)}\n\n"

        result_sent = False
        while not result_sent:
            try:
                msg_type, *args = await asyncio.wait_for(queue.get(), timeout=1.0)

                if msg_type == "progress":
                    step, message, percent = args
                    data = json.dumps({"step": step, "message": message, "percent": percent}, ensure_ascii=False)
                    yield f"event: progress\ndata: {data}\n\n"
                    if percent >= 100:
                        try:
                            msg_type2, result_data = await asyncio.wait_for(queue.get(), timeout=3.0)
                            if isinstance(result_data, dict):
                                final = json.dumps(result_data, ensure_ascii=False, default=str)
                                yield f"event: result\ndata: {final}\n\n"
                            result_sent = True
                        except asyncio.TimeoutError:
                            result_sent = True

                elif msg_type == "result":
                    result_data = args[0]
                    data = json.dumps({"step": "complete", "message": "Анализ завершён!", "percent": 100}, ensure_ascii=False)
                    yield f"event: progress\ndata: {data}\n\n"
                    final = json.dumps(result_data, ensure_ascii=False, default=str)
                    yield f"event: result\ndata: {final}\n\n"
                    result_sent = True

                elif msg_type == "error":
                    err_msg = args[0]
                    data = json.dumps({"detail": str(err_msg)}, ensure_ascii=False)
                    yield f"event: error\ndata: {data}\n\n"
                    result_sent = True

            except asyncio.TimeoutError:
                if task.done() and queue.empty():
                    exc = task.exception()
                    if exc:
                        data = json.dumps({"detail": f"Internal error: {exc}"}, ensure_ascii=False)
                        yield f"event: error\ndata: {data}\n\n"
                    result_sent = True
                continue

        # Await the background task to prevent premature cancellation on disconnect
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Client-submitted games endpoint ─────────────────────────────────

@app.post("/api/estimate/games")
async def estimate_games(req: BatchEstimateRequest):
    """Client sends pre-fetched game data (for Lichess, which is blocked from server)."""
    if req.platform not in ("lichess", "chesscom"):
        raise HTTPException(400, "Platform must be 'lichess' or 'chesscom'")
    if not req.username.strip():
        raise HTTPException(400, "Username is required")
    if not req.games:
        raise HTTPException(400, "No games provided")

    from fetchers import GameRecord

    games = []
    for g in req.games:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(g.date) if g.date else datetime.now(tz=timezone.utc)
        except (ValueError, TypeError):
            dt = datetime.now(tz=timezone.utc)

        rec = GameRecord(
            game_id=g.game_id,
            date=dt,
            speed=g.speed,
            time_class=g.time_class,
            user_rating=g.user_rating,
            opponent=g.opponent,
            opponent_rating=g.opponent_rating,
            opponent_title=g.opponent_title,
            user_accuracy=g.user_accuracy,
            opponent_accuracy=g.opponent_accuracy,
            user_color=g.user_color,
            result=g.result,
            platform=g.platform,
        )
        games.append(rec)

    estimator = Estimator()
    try:
        result = await estimator.estimate_from_games(req.platform, req.username.strip(), games)
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Internal error: {str(e)}")


# ── Lichess proxy endpoint (client-fetched games) ───────────────────

@app.post("/api/estimate/stream/games")
async def estimate_stream_games(req: BatchEstimateRequest):
    """SSE endpoint for client-provided games."""
    return await estimate_games(req)


# Serve frontend static files
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
