"""Ctrlable Snapcast TTS Streamer — FastAPI application."""
from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import secrets
import struct
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from auth import require_auth
from mapping import (
    NoMatchingMappingError,
    SatelliteNotMappedError,
)
from mapping import (
    delete as _delete_mapping,
)
from mapping import (
    resolve as _resolve_mapping,
)
from mapping import (
    upsert as _upsert_mapping,
)
from provisioning import get_config_snippet, scan_and_link
from snapcast import (
    SnapcastClient,
    SnapcastRPCError,
    SnapcastTimeoutError,
    get_client,
    init_client,
)
from state import ClientState, allocate_port, ensure_bearer_token, get_state, save_state
from streamer import (
    ClientNotEnabledError,
    ClientNotFoundError,
    NotProvisionedError,
    announce,
    announce_multi,
)
from watchdog import run_watchdog

# ── Logging setup (JSON structured) ───────────────────────────────

LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc)


_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logging.root.handlers.clear()
logging.root.addHandler(_handler)
logging.root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_LOGGER = logging.getLogger(__name__)

# ── Templates ─────────────────────────────────────────────────────

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "ui", "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

# ── In-memory activity log (last 100 entries) ─────────────────────

_activity_log: list[dict] = []
VERSION = "0.1.19"  # keep in sync with config.yaml

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


# ── Test audio tone (no auth — raw s16le 48kHz stereo, 1s 440Hz) ──

@app.get("/test_audio")
async def get_test_audio():
    from fastapi.responses import Response
    sample_rate = 48000
    buf = bytearray()
    for i in range(sample_rate):  # 1 second
        val = int(math.sin(2 * math.pi * 440 * i / sample_rate) * 16000)
        buf += struct.pack("<hh", val, val)
    return Response(content=bytes(buf), media_type="audio/pcm")


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


# ── Announce API ──────────────────────────────────────────────────

class AnnounceBody(BaseModel):
    client_id: str
    url: str
    source_host: str


class AnnounceMultiBody(BaseModel):
    client_ids: list[str]
    url: str
    source_host: str


@app.post("/announce", dependencies=[Depends(require_auth)])
async def api_announce(body: AnnounceBody) -> dict:
    try:
        result = await announce(body.client_id, body.url, body.source_host)
        _add_activity(body.client_id, result.fmt, result.duration, ok=True)
        return {"duration": round(result.duration, 3), "format": result.fmt}
    except ClientNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Client not found: {exc}") from exc
    except ClientNotEnabledError as exc:
        raise HTTPException(status_code=409, detail=f"Client not enabled: {exc}") from exc
    except NotProvisionedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SnapcastRPCError, SnapcastTimeoutError) as exc:
        _add_activity(body.client_id, "", None, ok=False, error=str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        _add_activity(body.client_id, "", None, ok=False, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/announce/multi", dependencies=[Depends(require_auth)])
async def api_announce_multi(body: AnnounceMultiBody) -> list[dict]:
    results = await announce_multi(body.client_ids, body.url, body.source_host)
    out = []
    for r in results:
        _add_activity(r.client_id, r.fmt, r.duration, ok=True)
        out.append({"client_id": r.client_id, "duration": round(r.duration, 3), "format": r.fmt})
    return out


class AnnounceBySatelliteBody(BaseModel):
    satellite_id: str
    wake_word: str | None = None
    url: str
    source_host: str


@app.post("/announce/by_satellite", dependencies=[Depends(require_auth)])
async def api_announce_by_satellite(body: AnnounceBySatelliteBody) -> list[dict]:
    state = get_state()
    try:
        target_ids = _resolve_mapping(state.mappings, body.satellite_id, body.wake_word)
    except SatelliteNotMappedError:
        raise HTTPException(status_code=404, detail=f"Satellite {body.satellite_id!r} has no mapping") from None
    except NoMatchingMappingError:
        raise HTTPException(status_code=422, detail=f"No mapping for satellite={body.satellite_id!r} wake_word={body.wake_word!r}") from None

    try:
        if len(target_ids) == 1:
            result = await announce(target_ids[0], body.url, body.source_host)
            _add_activity(target_ids[0], result.fmt, result.duration, ok=True)
            out: list[dict] = [{"client_id": target_ids[0], "duration": round(result.duration, 3), "format": result.fmt}]
        else:
            raw = await announce_multi(target_ids, body.url, body.source_host)
            out = []
            for r in raw:
                _add_activity(r.client_id, r.fmt, r.duration, ok=True)
                out.append({"client_id": r.client_id, "duration": round(r.duration, 3), "format": r.fmt})
        return out
    except ClientNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Client not found: {exc}") from exc
    except ClientNotEnabledError as exc:
        raise HTTPException(status_code=409, detail=f"Client not enabled: {exc}") from exc
    except NotProvisionedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SnapcastRPCError, SnapcastTimeoutError) as exc:
        for cid in target_ids:
            _add_activity(cid, "", None, ok=False, error=str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        for cid in target_ids:
            _add_activity(cid, "", None, ok=False, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Mappings API ──────────────────────────────────────────────────

@app.get("/mappings", dependencies=[Depends(require_auth)])
async def api_get_mappings() -> list[dict]:
    return get_state().mappings


class UpsertMappingBody(BaseModel):
    satellite_id: str
    wake_word: str = "*"
    target_snapclient_ids: list[str]
    notes: str = ""


@app.post("/mappings", dependencies=[Depends(require_auth)])
async def api_upsert_mapping(body: UpsertMappingBody) -> dict:
    state = get_state()
    state.mappings = _upsert_mapping(state.mappings, body.satellite_id, body.wake_word, body.target_snapclient_ids, body.notes)
    save_state()
    return {"ok": True}


class DeleteMappingBody(BaseModel):
    satellite_id: str
    wake_word: str


@app.delete("/mappings", dependencies=[Depends(require_auth)])
async def api_delete_mapping(body: DeleteMappingBody) -> dict:
    state = get_state()
    state.mappings = _delete_mapping(state.mappings, body.satellite_id, body.wake_word)
    save_state()
    return {"ok": True}


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

    ingress = _ingress_path(request)
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
        return RedirectResponse(f"{ingress}/ui/?msg={msg}&t={t}", status_code=303)

    try:
        old = get_client()
        await old.disconnect()
    except Exception:
        pass
    try:
        await init_client(snapcast_host, snapcast_rpc_port)
        _degraded = False
        return RedirectResponse(f"{ingress}/ui/?msg=Settings saved and reconnected&t=ok", status_code=303)
    except Exception as exc:
        _degraded = True
        return RedirectResponse(f"{ingress}/ui/?msg=Saved but reconnect failed: {exc}&t=error", status_code=303)


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


@app.post("/ui/clients/toggle", response_class=HTMLResponse)
async def ui_client_toggle(request: Request, client_id: str = Form(...)):
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
    if cs.enabled and cs.announce_port == 0:
        base = int(os.environ.get("ANNOUNCE_PORT_BASE", "5200"))
        cs.announce_port = allocate_port(base)
    elif not cs.enabled and cs.announce_port > 0:
        if cs.announce_port in state.ports_in_use:
            state.ports_in_use.remove(cs.announce_port)
        cs.announce_port = 0
    save_state()
    return RedirectResponse(f"{_ingress_path(request)}/ui/clients", status_code=303)


@app.post("/ui/clients/test", response_class=HTMLResponse)
async def ui_test_announce(request: Request, client_id: str = Form(...)):
    """Fire a 1-second 440 Hz test tone to the client's announce port."""
    test_url = "http://localhost:8099/test_audio"
    try:
        result = await announce(client_id, test_url, "_test_")
        _add_activity(client_id, result.fmt, result.duration, ok=True)
    except Exception as exc:
        _add_activity(client_id, "test", None, ok=False, error=str(exc))
    return RedirectResponse(f"{_ingress_path(request)}/ui/clients", status_code=303)


@app.get("/ui/activity", response_class=HTMLResponse)
async def ui_activity(request: Request):
    ctx = _base_ctx(request, "activity")
    ctx["log"] = list(reversed(_activity_log))
    return templates.TemplateResponse(request, "activity.html", ctx)


@app.get("/ui/streams", response_class=HTMLResponse)
async def ui_streams(request: Request):
    state = get_state()
    enabled = {cid: cs for cid, cs in state.clients.items() if cs.enabled}
    snippet = get_config_snippet(enabled)
    client_list = [
        {
            "id": cid,
            "name": cs.name or cid,
            "announce_port": cs.announce_port,
            "announce_group_id": cs.announce_group_id,
            "announce_stream_id": cs.announce_stream_id,
        }
        for cid, cs in enabled.items()
    ]
    ctx = _base_ctx(request, "streams")
    ctx.update({
        "snippet": snippet,
        "clients": client_list,
        "message": request.query_params.get("msg"),
        "message_type": request.query_params.get("t", "ok"),
    })
    return templates.TemplateResponse(request, "streams.html", ctx)


@app.post("/ui/streams/scan", response_class=HTMLResponse)
async def ui_streams_scan(request: Request):
    ingress = _ingress_path(request)
    try:
        snap = get_client()
        results = await scan_and_link(snap)
        total = len(results)
        linked = sum(1 for v in results.values() if "linked" in v)
        missing = total - linked
        if total == 0:
            msg = "Scan complete: no enabled clients found — enable clients on the Clients tab first"
            t = "warn"
        elif missing == 0:
            msg = f"Scan complete: all {linked} client(s) linked"
            t = "ok"
        else:
            msg = f"Scan complete: {linked}/{total} linked, {missing} need snapserver.conf entry"
            t = "warn"
    except (SnapcastRPCError, SnapcastTimeoutError) as exc:
        msg = f"Scan failed: {exc}"
        t = "error"
    return RedirectResponse(f"{ingress}/ui/streams?msg={msg}&t={t}", status_code=303)


@app.get("/ui/mappings", response_class=HTMLResponse)
async def ui_mappings(request: Request):
    state = get_state()
    enabled_clients = [
        {"id": cid, "name": cs.name or cid}
        for cid, cs in state.clients.items()
        if cs.enabled
    ]
    client_names = {cid: (cs.name or cid) for cid, cs in state.clients.items()}
    ctx = _base_ctx(request, "mappings")
    ctx.update({
        "mappings": state.mappings,
        "enabled_clients": enabled_clients,
        "client_names": client_names,
        "message": request.query_params.get("msg"),
        "message_type": request.query_params.get("t", "ok"),
    })
    return templates.TemplateResponse(request, "mappings.html", ctx)


@app.post("/ui/mappings/add", response_class=HTMLResponse)
async def ui_mappings_add(
    request: Request,
    satellite_id: str = Form(...),
    wake_word: str = Form("*"),
    target_snapclient_ids: list[str] | None = Form(None),
    notes: str = Form(""),
):
    ids = target_snapclient_ids or []
    ingress = _ingress_path(request)
    if not satellite_id.strip():
        return RedirectResponse(f"{ingress}/ui/mappings?msg=Satellite+ID+is+required&t=error", status_code=303)
    if not ids:
        return RedirectResponse(f"{ingress}/ui/mappings?msg=Select+at+least+one+target+client&t=error", status_code=303)
    state = get_state()
    state.mappings = _upsert_mapping(
        state.mappings,
        satellite_id.strip(),
        wake_word.strip() or "*",
        ids,
        notes.strip(),
    )
    save_state()
    return RedirectResponse(f"{ingress}/ui/mappings?msg=Mapping+saved&t=ok", status_code=303)


@app.post("/ui/mappings/delete", response_class=HTMLResponse)
async def ui_mappings_delete(
    request: Request,
    satellite_id: str = Form(...),
    wake_word: str = Form(...),
):
    state = get_state()
    state.mappings = _delete_mapping(state.mappings, satellite_id, wake_word)
    save_state()
    return RedirectResponse(f"{_ingress_path(request)}/ui/mappings?msg=Mapping+deleted&t=ok", status_code=303)


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
            "mappings": state.mappings,
        },
        indent=2,
    )
    return templates.TemplateResponse(request, "advanced.html", ctx)


@app.post("/ui/advanced/regenerate_token")
async def ui_regenerate_token(request: Request):
    state = get_state()
    state.auth.bearer_token = secrets.token_urlsafe(32)
    save_state()
    return RedirectResponse(f"{_ingress_path(request)}/ui/advanced", status_code=303)
