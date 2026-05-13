"""Tests for announce() group snapshot-and-restore behaviour."""
import asyncio
import sys
import os
import types
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Minimal stubs so streamer.py can be imported without the full add-on env ──

# state stub
state_mod = types.ModuleType("state")

@dataclass
class ClientState:
    name: str = ""
    enabled: bool = True
    announce_port: int = 5206
    announce_group_id: str = "announce-group-lr"
    home_group_id: str = "shared-annoucements-group"
    home_group_autodetected: bool = True
    format_cache: dict = field(default_factory=dict)

@dataclass
class SnapcastConfig:
    host: str = "10.1.8.9"
    rpc_port: int = 1705

@dataclass
class AppState:
    snapcast: SnapcastConfig = field(default_factory=SnapcastConfig)
    clients: dict = field(default_factory=dict)

_app_state = AppState()

state_mod.get_state = lambda: _app_state
state_mod.save_state = lambda: None
state_mod.ClientState = ClientState
sys.modules["state"] = state_mod

# snapcast stub
snapcast_mod = types.ModuleType("snapcast")

@dataclass
class SnapClient:
    id: str
    current_group_id: str

_snap_instance = MagicMock()
snapcast_mod.get_client = lambda: _snap_instance
sys.modules["snapcast"] = snapcast_mod

# httpx stub
httpx_mod = types.ModuleType("httpx")
sys.modules["httpx"] = httpx_mod

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ctrlable-snapcast-streamer", "app"))

import importlib
streamer = importlib.import_module("streamer")


# ── Helpers ───────────────────────────────────────────────────────────────────

CLIENT_ID = "snapclient-announcement-6#16"
ANNOUNCE_GROUP = "announce-group-lr"
HOME_GROUP = "shared-annoucements-group"
AIRPLAY_GROUP = "airplay-group-lr"

TTS_URL = "http://ha:8123/tts/test.wav"
SOURCE_HOST = "ha"


def _make_cs(**kwargs) -> ClientState:
    cs = ClientState(
        name="Living Room",
        enabled=True,
        announce_port=5206,
        announce_group_id=ANNOUNCE_GROUP,
        home_group_id=HOME_GROUP,
        home_group_autodetected=True,
        format_cache={SOURCE_HOST: "other"},
    )
    for k, v in kwargs.items():
        setattr(cs, k, v)
    return cs


def _setup_state(cs: ClientState) -> None:
    _app_state.clients = {CLIENT_ID: cs}


def _mock_snap(current_group: str) -> MagicMock:
    """Return a mock SnapcastClient where the client is in `current_group`."""
    snap = MagicMock()
    snap.list_clients = AsyncMock(return_value=[SnapClient(id=CLIENT_ID, current_group_id=current_group)])
    snap.move_client_to_group = AsyncMock()
    return snap


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restore_to_live_group_not_home_group():
    """When client is manually moved to AirPlay, announce restores to AirPlay, not home_group."""
    cs = _make_cs()
    _setup_state(cs)
    snap = _mock_snap(current_group=AIRPLAY_GROUP)

    with (
        patch.object(snapcast_mod, "get_client", return_value=snap),
        patch.object(streamer, "get_snap", return_value=snap),
        patch.object(streamer, "_stream_ffmpeg", new=AsyncMock()),
        patch.object(streamer, "asyncio") as mock_asyncio,
    ):
        mock_asyncio.sleep = AsyncMock()
        mock_asyncio.Lock = asyncio.Lock
        mock_asyncio.gather = asyncio.gather
        mock_asyncio.open_connection = AsyncMock()
        streamer._locks = {}

        await streamer.announce(CLIENT_ID, TTS_URL, SOURCE_HOST)

    calls = snap.move_client_to_group.call_args_list
    assert len(calls) == 2
    # First call: move to announce group
    assert calls[0].args == (CLIENT_ID, ANNOUNCE_GROUP)
    # Second call (restore): must go back to AIRPLAY_GROUP, NOT home_group
    assert calls[1].args == (CLIENT_ID, AIRPLAY_GROUP), (
        f"Expected restore to AirPlay group {AIRPLAY_GROUP!r}, got {calls[1].args[1]!r}"
    )


@pytest.mark.asyncio
async def test_no_group_switch_when_already_in_announce_group():
    """Client already in announce group → no move, just stream."""
    cs = _make_cs()
    _setup_state(cs)
    snap = _mock_snap(current_group=ANNOUNCE_GROUP)

    with (
        patch.object(streamer, "get_snap", return_value=snap),
        patch.object(streamer, "_stream_ffmpeg", new=AsyncMock()),
        patch.object(streamer, "asyncio") as mock_asyncio,
    ):
        mock_asyncio.sleep = AsyncMock()
        mock_asyncio.Lock = asyncio.Lock
        streamer._locks = {}

        await streamer.announce(CLIENT_ID, TTS_URL, SOURCE_HOST)

    snap.move_client_to_group.assert_not_called()


@pytest.mark.asyncio
async def test_restore_to_home_group_normal_case():
    """Standard case: client in home group → moves to announce → restores to home."""
    cs = _make_cs()
    _setup_state(cs)
    snap = _mock_snap(current_group=HOME_GROUP)

    with (
        patch.object(streamer, "get_snap", return_value=snap),
        patch.object(streamer, "_stream_ffmpeg", new=AsyncMock()),
        patch.object(streamer, "asyncio") as mock_asyncio,
    ):
        mock_asyncio.sleep = AsyncMock()
        mock_asyncio.Lock = asyncio.Lock
        streamer._locks = {}

        await streamer.announce(CLIENT_ID, TTS_URL, SOURCE_HOST)

    calls = snap.move_client_to_group.call_args_list
    assert calls[0].args == (CLIENT_ID, ANNOUNCE_GROUP)
    assert calls[1].args == (CLIENT_ID, HOME_GROUP)


@pytest.mark.asyncio
async def test_restore_happens_on_stream_error():
    """Even if streaming raises, the client is restored to its live group."""
    cs = _make_cs()
    _setup_state(cs)
    snap = _mock_snap(current_group=HOME_GROUP)

    with (
        patch.object(streamer, "get_snap", return_value=snap),
        patch.object(streamer, "_stream_ffmpeg", new=AsyncMock(side_effect=RuntimeError("stream broke"))),
        patch.object(streamer, "asyncio") as mock_asyncio,
    ):
        mock_asyncio.sleep = AsyncMock()
        mock_asyncio.Lock = asyncio.Lock
        streamer._locks = {}

        with pytest.raises(RuntimeError):
            await streamer.announce(CLIENT_ID, TTS_URL, SOURCE_HOST)

    # Even after exception, restore must be called
    restore_calls = [c for c in snap.move_client_to_group.call_args_list if c.args[1] == HOME_GROUP]
    assert len(restore_calls) == 1, "Restore must happen even when streaming fails"


@pytest.mark.asyncio
async def test_fallback_to_saved_home_when_rpc_fails():
    """If list_clients fails, fall back to saved home_group_id for restore."""
    cs = _make_cs()
    _setup_state(cs)
    snap = MagicMock()
    snap.list_clients = AsyncMock(side_effect=Exception("RPC timeout"))
    snap.move_client_to_group = AsyncMock()

    with (
        patch.object(streamer, "get_snap", return_value=snap),
        patch.object(streamer, "_stream_ffmpeg", new=AsyncMock()),
        patch.object(streamer, "asyncio") as mock_asyncio,
    ):
        mock_asyncio.sleep = AsyncMock()
        mock_asyncio.Lock = asyncio.Lock
        streamer._locks = {}

        await streamer.announce(CLIENT_ID, TTS_URL, SOURCE_HOST)

    calls = snap.move_client_to_group.call_args_list
    # Should still move and restore using saved home_group_id as fallback
    assert calls[0].args == (CLIENT_ID, ANNOUNCE_GROUP)
    assert calls[1].args == (CLIENT_ID, HOME_GROUP)
