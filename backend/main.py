import asyncio
import json
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from estimator import Estimator


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="FIDE Rating Calculator", version="2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class EstimateRequest(BaseModel):
    platform: str
    username: str


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "2.0"}


@app.post("/api/estimate")
async def estimate(req: EstimateRequest):
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

        # Send initial progress
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
                        # Wait a bit for the result
                        try:
                            msg_type2, result_data = await asyncio.wait_for(queue.get(), timeout=3.0)
                            if isinstance(result_data, dict):
                                final = json.dumps(result_data, ensure_ascii=False, default=str)
                                yield f"event: result\ndata: {final}\n\n"
                            result_sent = True
                        except asyncio.TimeoutError:
                            # No result after 100%? Stop
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

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# Serve frontend static files
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8200, reload=True)
