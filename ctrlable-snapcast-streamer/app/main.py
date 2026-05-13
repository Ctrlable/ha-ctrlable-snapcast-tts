"""Ctrlable Snapcast TTS Streamer — FastAPI application entry point."""
from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Ctrlable Snapcast TTS Streamer", version="0.0.1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.0.1"}
