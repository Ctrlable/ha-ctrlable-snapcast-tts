"""Ctrlable Snapcast TTS Streamer — FastAPI application."""
from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth import require_auth
from snapcast import (
    SnapcastClient,
    SnapcastRPCError,
    SnapcastTimeoutError,
    get_client,
    init_client,
)
from state import ClientState, ensure_bearer_token, get_state, save_state
from watchdog import run_watchdog

# ── Logging setup ─────────────────────────────────────────────────

LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_LOGGER = logging.getLogger(__name__)

# ── Templates ─────────────────────────────────────────────────────

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "ui", "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

# ── In-memory activity log (last 100 entries) ─────────────────────

_activity_log: list[dict] = []
VERSION = "0.1.4"  # keep in sync with config.yaml

# ── Degraded-mode flag ────────────────────────────────────────────

_degraded = False


def _add_activity(client_id: str, fmt: str, duration: float | None, ok: bool, error: str = "") -> None:
    state = get_state()
    cs = state.clients.get(client_id)
    _activity_log.append({
        "ts": datetime.now(tz=UTC).strftime("%H:%M:%S"),
        "client_name": cs.name if cs else client_id,
        "fmt": fmt,
        "duration": duration,
        "ok": ok,
        "error": error,
    })
    if len(_activity_log) > 100:
        _activity_log.pop(0)


# ── App lifecycle ─────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _degraded
    state = get_state()
    ensure_bearer_token()

    host = os.environ.get("SNAPCAST_HOST", state.snapcast.host) or "10.1.8.9"
    port = int(os.environ.get("SNAPCAST_RPC_PORT", state.snapcast.rpc_port) or 1705)

    if host:
        state.snapcast.host = host
    if port:
        state.snapcast.rpc_port = port
    save_state()

    try:
        await init_client(host, port)
        _LOGGER.info("Snapcast connection established")
        ok = await run_watchdog()
        if not ok:
            _LOGGER.warning("Watchdog completed with failures — staying up")
        _degraded = False
    except Exception as exc:
        _LOGGER.error("Failed to connect to Snapcast on startup: %s", exc)
        _degraded = True

    yield

    with contextlib.suppress(Exception):
        await get_client().disconnect()


app = FastAPI(title="Ctrlable Snapcast TTS Streamer", version=VERSION, lifespan=lifespan)


# ── Health endpoint (no auth, used by Supervisor) ─────────────────

@app.get("/health")
async def health() -> dict:
    return {
        "status": "degraded" if _degraded else "ok",
        "version": VERSION,
        "snapcast_connected": not _degraded,
    }


# ── Snapcast API endpoints (auth required) ─────────────────────────

@app.get("/snapcast/clients", dependencies=[Depends(require_auth)])
async def api_list_clients() -> list[dict]:
    try:
        snap = get_client()
        clients = await snap.list_clients()
        state = get_state()
        result = []
        for c in clients:
            cs = state.clients.get(c.id)
            result.append({
                "id": c.id,
                "name": c.name,
                "connected": c.connected,
                "current_group_id": c.current_group_id,
                "host_ip": c.host_ip,
                "volume_percent": c.volume_percent,
                "muted": c.muted,
                "enabled": cs.enabled if cs else False,
                "announce_port": cs.announce_port if cs else 0,
            })
        return result
    except (SnapcastRPCError, SnapcastTimeoutError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/snapcast/groups", dependencies=[Depends(require_auth)])
async def api_list_groups() -> list[dict]:
    try:
        snap = get_client()
        groups = await snap.list_groups()
        return [{"id": g.id, "name": g.name, "stream_id": g.stream_id, "client_ids": g.client_ids} for g in groups]
    except (SnapcastRPCError, SnapcastTimeoutError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/status", dependencies=[Depends(require_auth)])
async def api_status() -> dict:
    state = get_state()
    return {
        "degraded": _degraded,
        "version": VERSION,
        "clients": {k: asdict(v) for k, v in state.clients.items()},
        "ports_in_use": state.ports_in_use,
    }


# ── Ingress UI ────────────────────────────────────────────────────

def _ingress_path(request: Request) -> str:
    return request.headers.get("X-Ingress-Path", "").rstrip("/")


def _base_ctx(request: Request, active: str) -> dict:
    return {
        "active": active,
        "degraded": _degraded,
        "version": VERSION,
        "ingress_path": _ingress_path(request),
    }


@app.get("/ui/", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
async def ui_connection(request: Request):
    state = get_state()
    snap_status = None
    if not _degraded:
        try:
            snap = get_client()
            clients = await snap.list_clients()
            streams = await snap.list_streams()
            snap_status = {
                "version": "0.35.x",
                "connected_clients": sum(1 for c in clients if c.connected),
                "total_clients": len(clients),
                "streams": [s.id for s in streams],
            }
        except Exception:
            pass
    ctx = _base_ctx(request, "connection")
    ctx.update({
        "snapcast_host": state.snapcast.host,
        "snapcast_rpc_port": state.snapcast.rpc_port,
        "snap_status": snap_status,
        "message": request.query_params.get("msg"),
        "message_type": request.query_params.get("t", "ok"),
    })
    return templates.TemplateResponse(request, "connection.html", ctx)


@app.post("/ui/connection", response_class=HTMLResponse)
async def ui_connection_post(
    request: Request,
    action: str = Form(...),
    snapcast_host: str = Form(...),
    snapcast_rpc_port: int = Form(...),
):
    global _degraded
    state = get_state()
    state.snapcast.host = snapcast_host
    state.snapcast.rpc_port = snapcast_rpc_port
    save_state()

    if action == "test":
        try:
            test_client = SnapcastClient(snapcast_host, snapcast_rpc_port)
            await test_client.connect()
            clients = await test_client.list_clients()
            await test_client.disconnect()
            msg = f"Connected — found {len(clients)} client(s)"
            t = "ok"
        except Exception as exc:
            msg = f"Connection failed: {exc}"
            t = "error"
        return RedirectResponse(f"/ui/?msg={msg}&t={t}", status_code=303)

    try:
        old = get_client()
        await old.disconnect()
    except Exception:
        pass
    try:
        await init_client(snapcast_host, snapcast_rpc_port)
        _degraded = False
        return RedirectResponse("/ui/?msg=Settings saved and reconnected&t=ok", status_code=303)
    except Exception as exc:
        _degraded = True
        return RedirectResponse(f"/ui/?msg=Saved but reconnect failed: {exc}&t=error", status_code=303)


@app.get("/ui/clients", response_class=HTMLResponse)
async def ui_clients(request: Request):
    state = get_state()
    client_list = []
    if not _degraded:
        try:
            snap = get_client()
            snap_clients = await snap.list_clients()
            for c in snap_clients:
                cs = state.clients.get(c.id, ClientState(name=c.name))
                client_list.append({
                    "id": c.id,
                    "name": c.name or cs.name or "(unnamed)",
                    "connected": c.connected,
                    "current_group_id": c.current_group_id,
                    "host_ip": c.host_ip,
                    "volume_percent": c.volume_percent,
                    "muted": c.muted,
                    "enabled": cs.enabled,
                    "announce_port": cs.announce_port,
                })
        except Exception:
            pass
    ctx = _base_ctx(request, "clients")
    ctx["clients"] = client_list
    return templates.TemplateResponse(request, "clients.html", ctx)


@app.post("/ui/clients/{client_id}/toggle", response_class=HTMLResponse)
async def ui_client_toggle(client_id: str):
    state = get_state()
    if client_id not in state.clients:
        name = client_id
        try:
            snap = get_client()
            clients = await snap.list_clients()
            match = next((c for c in clients if c.id == client_id), None)
            if match:
                name = match.name
        except Exception:
            pass
        state.clients[client_id] = ClientState(name=name)
    cs = state.clients[client_id]
    cs.enabled = not cs.enabled
    save_state()
    return RedirectResponse("/ui/clients", status_code=303)


@app.get("/ui/activity", response_class=HTMLResponse)
async def ui_activity(request: Request):
    ctx = _base_ctx(request, "activity")
    ctx["log"] = list(reversed(_activity_log))
    return templates.TemplateResponse(request, "activity.html", ctx)


@app.get("/ui/advanced", response_class=HTMLResponse)
async def ui_advanced(request: Request):
    state = get_state()
    ctx = _base_ctx(request, "advanced")
    ctx["bearer_token"] = state.auth.bearer_token
    ctx["state_json"] = json.dumps(
        {
            "schema_version": state.schema_version,
            "snapcast": asdict(state.snapcast),
            "clients": {k: asdict(v) for k, v in state.clients.items()},
            "ports_in_use": state.ports_in_use,
        },
        indent=2,
    )
    return templates.TemplateResponse(request, "advanced.html", ctx)


@app.post("/ui/advanced/regenerate_token")
async def ui_regenerate_token():
    state = get_state()
    state.auth.bearer_token = secrets.token_urlsafe(32)
    save_state()
    return RedirectResponse("/ui/advanced", status_code=303)
