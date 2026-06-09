import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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


# Serve frontend static files
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8200, reload=True)
