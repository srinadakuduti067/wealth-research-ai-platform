import asyncio
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import ROOT, settings
from app.db import database_ready
from app.orchestrator import research


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    ticker: str | None = Field(default=None, max_length=12)


class ChatResponse(BaseModel):
    status: str
    answer: str
    tools_used: list[str]
    sources: list[dict]
    quote: dict | None


app = FastAPI(title="Wealth Research Copilot - Synthetic Demo")


@app.get("/api/health")
async def health():
    ready = await asyncio.to_thread(database_ready)
    if not ready:
        raise HTTPException(status_code=503, detail="Document database unavailable.")
    return {"status": "ok", "model_configured": bool(settings.gcp_project)}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        return await research(request.question, request.ticker)
    except Exception:
        return ChatResponse(
            status="error",
            answer="Research is temporarily unavailable. Please retry.",
            tools_used=[],
            sources=[],
            quote=None,
        )


@app.get("/")
async def index():
    return FileResponse(Path(ROOT) / "frontend" / "index.html")
