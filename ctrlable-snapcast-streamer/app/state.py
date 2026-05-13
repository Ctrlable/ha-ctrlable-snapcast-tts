"""Persistent state management via /data/state.json."""
from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
STATE_FILE = DATA_DIR / "state.json"
SCHEMA_VERSION = 1


@dataclass
class ClientState:
    name: str = ""
    enabled: bool = False
    announce_port: int = 0
    announce_group_id: str = ""
    home_group_id: str = ""
    home_group_autodetected: bool = False
    format_cache: dict[str, str] = field(default_factory=dict)


@dataclass
class SnapcastConfig:
    host: str = ""
    rpc_port: int = 1705
    config_mode: str = "file_edit"
    ssh_host: str = ""
    ssh_user: str = "root"
    ssh_key_path: str = "/data/ssh_key"


@dataclass
class AuthConfig:
    bearer_token: str = ""


@dataclass
class AppState:
    schema_version: int = SCHEMA_VERSION
    snapcast: SnapcastConfig = field(default_factory=SnapcastConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    clients: dict[str, ClientState] = field(default_factory=dict)
    ports_in_use: list[int] = field(default_factory=list)


_state: AppState | None = None


def _state_to_dict(state: AppState) -> dict:
    return {
        "schema_version": state.schema_version,
        "snapcast": asdict(state.snapcast),
        "auth": asdict(state.auth),
        "clients": {k: asdict(v) for k, v in state.clients.items()},
        "ports_in_use": state.ports_in_use,
    }


def _state_from_dict(d: dict) -> AppState:
    snap_d = d.get("snapcast", {})
    auth_d = d.get("auth", {})
    clients_d = d.get("clients", {})
    return AppState(
        schema_version=d.get("schema_version", SCHEMA_VERSION),
        snapcast=SnapcastConfig(**{k: v for k, v in snap_d.items() if k in SnapcastConfig.__dataclass_fields__}),
        auth=AuthConfig(**{k: v for k, v in auth_d.items() if k in AuthConfig.__dataclass_fields__}),
        clients={
            client_id: ClientState(**{k: v for k, v in c.items() if k in ClientState.__dataclass_fields__})
            for client_id, c in clients_d.items()
        },
        ports_in_use=d.get("ports_in_use", []),
    )


def load_state() -> AppState:
    global _state
    if STATE_FILE.exists():
        try:
            raw = json.loads(STATE_FILE.read_text())
            _state = _state_from_dict(raw)
            _LOGGER.info("State loaded from %s (%d clients)", STATE_FILE, len(_state.clients))
            return _state
        except Exception:
            _LOGGER.exception("Failed to parse state file, starting fresh")
    _state = AppState()
    _LOGGER.info("No state file found, starting fresh")
    return _state


def save_state() -> None:
    if _state is None:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_state_to_dict(_state), indent=2))
    tmp.replace(STATE_FILE)


def get_state() -> AppState:
    global _state
    if _state is None:
        _state = load_state()
    return _state


def allocate_port(base: int) -> int:
    """Allocate the next free port starting from base."""
    state = get_state()
    port = base
    while port in state.ports_in_use:
        port += 1
    state.ports_in_use.append(port)
    save_state()
    return port


def ensure_bearer_token() -> str:
    """Generate a bearer token on first run; persist it."""
    state = get_state()
    if not state.auth.bearer_token:
        state.auth.bearer_token = secrets.token_urlsafe(32)
        save_state()
        _LOGGER.warning("Generated new bearer token (copy from Advanced tab)")
    return state.auth.bearer_token
