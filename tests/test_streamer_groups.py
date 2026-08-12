"""Tests for announce() stream-switch snapshot-and-restore behaviour."""
import asyncio
import sys
import os
import types
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Minimal stubs so streamer.py can be imported without the full add-on env ──

state_mod = types.ModuleType("state")

@dataclass
class ClientState:
    name: str = ""
    enabled: bool = True
    announce_port: int = 5206
    announce_group_id: str = "group-lr"
    announce_stream_id: str = "ann_snapclientannouncement616"
    home_group_id: str = "group-lr"
    home_group_autodetected: bool = True
    format_cache: dict = field(default_factory=dict)

@dataclass
class SnapcastConfig:
    host: str = "10.1.8.9"
    rpc_port: int = 1715

@dataclass
class AppState:
    snapcast: SnapcastConfig = field(default_factory=SnapcastConfig)
    clients: dict = field(default_factory=dict)

_app_state = AppState()

state_mod.get_state = lambda: _app_state
state_mod.save_state = lambda: None
state_mod.ClientState = ClientState
sys.modules["state"] = state_mod

snapcast_mod = types.ModuleType("snapcast")

@dataclass
class SnapGroup:
    id: str
    stream_id: str
    # announce() locates the group by MEMBERSHIP, not by the id cached in state,
    # because snapserver regenerates group ids on every restart (0.1.31). The
    # stub needs this field or no group ever matches and the switch is skipped.
    client_ids: list[str] = field(default_factory=list)

_snap_instance = MagicMock()
snapcast_mod.get_client = lambda: _snap_instance
sys.modules["snapcast"] = snapcast_mod

httpx_mod = types.ModuleType("httpx")
sys.modules["httpx"] = httpx_mod

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ctrlable-snapcast-streamer", "app"))

import importlib
streamer = importlib.import_module("streamer")

# ── Constants ─────────────────────────────────────────────────────────────────

CLIENT_ID = "snapclient-announcement-6#16"
GROUP_ID = "group-lr"
ANNOUNCE_STREAM = "ann_snapclientannouncement616"
AIRPLAY_STREAM = "airplay-stream"
MUSIC_STREAM = "Music Assistant - masnapclientannouncement616"
TTS_URL = "http://ha:8123/tts/test.wav"
SOURCE_HOST = "ha"


def _make_cs(**kwargs) -> ClientState:
    cs = ClientState(
        name="Living Room",
        enabled=True,
        announce_port=5206,
        announce_group_id=GROUP_ID,
        announce_stream_id=ANNOUNCE_STREAM,
        home_group_id=GROUP_ID,
        home_group_autodetected=True,
        format_cache={SOURCE_HOST: "other"},
    )
    for k, v in kwargs.items():
        setattr(cs, k, v)
    return cs


def _setup_state(cs: ClientState) -> None:
    _app_state.clients = {CLIENT_ID: cs}


def _mock_snap(current_stream: str) -> MagicMock:
    """Return a mock SnapcastClient where the group is bound to `current_stream`.

    client_ids MUST be populated. announce() finds the group by MEMBERSHIP -- the
    group that currently contains this client -- rather than by the id cached in
    state, because snapserver regenerates group ids on every restart and the
    cached one goes stale (fixed in 0.1.31, where the stale id caused
    announcements to be silently skipped). A group without client_ids therefore
    matches nothing and the stream switch never happens.
    """
    snap = MagicMock()
    snap.list_groups = AsyncMock(return_value=[
        SnapGroup(id=GROUP_ID, stream_id=current_stream, client_ids=[CLIENT_ID])
    ])
    snap.set_group_stream = AsyncMock()
    return snap


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_switches_stream_when_on_airplay():
    """When group is on AirPlay, announce switches to announce stream and restores."""
    cs = _make_cs()
    _setup_state(cs)
    snap = _mock_snap(current_stream=AIRPLAY_STREAM)

    with (
        patch.object(streamer, "get_snap", return_value=snap),
        # 48000 Hz x 2 ch x 2 bytes = 192000 B/s, so this is exactly 1.0s of audio.
        patch.object(streamer, "_stream_ffmpeg", new=AsyncMock(return_value=192000)),
        # Patch ONLY asyncio.sleep, never the whole module. Replacing
        # streamer.asyncio wholesale broke these tests the moment the sticky
        # group feature started using asyncio.create_task: the restore is
        # wrapped in contextlib.suppress(Exception), so a mock-induced error was
        # swallowed and the assertion saw no restore call -- reporting a
        # restore bug that did not exist.
        patch.object(streamer.asyncio, "sleep", new=AsyncMock()),
    ):
        streamer._locks = {}
        await streamer.announce(CLIENT_ID, TTS_URL, SOURCE_HOST)

    calls = snap.set_group_stream.call_args_list
    assert len(calls) == 2, f"Expected 2 set_group_stream calls, got {len(calls)}"
    assert calls[0].args == (GROUP_ID, ANNOUNCE_STREAM), "Should switch to announce stream"
    assert calls[1].args == (GROUP_ID, AIRPLAY_STREAM), "Should restore to AirPlay"


@pytest.mark.asyncio
async def test_switches_stream_when_on_music_assistant():
    """When group is on Music Assistant, announce switches and restores."""
    cs = _make_cs()
    _setup_state(cs)
    snap = _mock_snap(current_stream=MUSIC_STREAM)

    with (
        patch.object(streamer, "get_snap", return_value=snap),
        # 48000 Hz x 2 ch x 2 bytes = 192000 B/s, so this is exactly 1.0s of audio.
        patch.object(streamer, "_stream_ffmpeg", new=AsyncMock(return_value=192000)),
        # Patch ONLY asyncio.sleep, never the whole module. Replacing
        # streamer.asyncio wholesale broke these tests the moment the sticky
        # group feature started using asyncio.create_task: the restore is
        # wrapped in contextlib.suppress(Exception), so a mock-induced error was
        # swallowed and the assertion saw no restore call -- reporting a
        # restore bug that did not exist.
        patch.object(streamer.asyncio, "sleep", new=AsyncMock()),
    ):
        streamer._locks = {}
        await streamer.announce(CLIENT_ID, TTS_URL, SOURCE_HOST)

    calls = snap.set_group_stream.call_args_list
    assert calls[0].args == (GROUP_ID, ANNOUNCE_STREAM)
    assert calls[1].args == (GROUP_ID, MUSIC_STREAM)


@pytest.mark.asyncio
async def test_no_stream_switch_when_already_on_announce_stream():
    """When group is already on the announce stream, no switching needed."""
    cs = _make_cs()
    _setup_state(cs)
    snap = _mock_snap(current_stream=ANNOUNCE_STREAM)

    with (
        patch.object(streamer, "get_snap", return_value=snap),
        # 48000 Hz x 2 ch x 2 bytes = 192000 B/s, so this is exactly 1.0s of audio.
        patch.object(streamer, "_stream_ffmpeg", new=AsyncMock(return_value=192000)),
        # Patch ONLY asyncio.sleep, never the whole module. Replacing
        # streamer.asyncio wholesale broke these tests the moment the sticky
        # group feature started using asyncio.create_task: the restore is
        # wrapped in contextlib.suppress(Exception), so a mock-induced error was
        # swallowed and the assertion saw no restore call -- reporting a
        # restore bug that did not exist.
        patch.object(streamer.asyncio, "sleep", new=AsyncMock()),
    ):
        streamer._locks = {}
        await streamer.announce(CLIENT_ID, TTS_URL, SOURCE_HOST)

    snap.set_group_stream.assert_not_called()


@pytest.mark.asyncio
async def test_restore_happens_on_stream_error():
    """Even if streaming raises, the group stream is restored."""
    cs = _make_cs()
    _setup_state(cs)
    snap = _mock_snap(current_stream=AIRPLAY_STREAM)

    with (
        patch.object(streamer, "get_snap", return_value=snap),
        patch.object(streamer, "_stream_ffmpeg", new=AsyncMock(side_effect=RuntimeError("stream broke"))),
        # Patch ONLY asyncio.sleep, never the whole module. Replacing
        # streamer.asyncio wholesale broke these tests the moment the sticky
        # group feature started using asyncio.create_task: the restore is
        # wrapped in contextlib.suppress(Exception), so a mock-induced error was
        # swallowed and the assertion saw no restore call -- reporting a
        # restore bug that did not exist.
        patch.object(streamer.asyncio, "sleep", new=AsyncMock()),
    ):
        streamer._locks = {}
        with pytest.raises(RuntimeError):
            await streamer.announce(CLIENT_ID, TTS_URL, SOURCE_HOST)

    restore_calls = [c for c in snap.set_group_stream.call_args_list if c.args[1] == AIRPLAY_STREAM]
    assert len(restore_calls) == 1, "Stream must be restored even when streaming fails"


@pytest.mark.asyncio
async def test_no_switch_when_announce_stream_id_not_configured():
    """Clients without announce_stream_id (pre-scan) just stream directly."""
    cs = _make_cs(announce_stream_id="")
    _setup_state(cs)
    snap = _mock_snap(current_stream=AIRPLAY_STREAM)

    with (
        patch.object(streamer, "get_snap", return_value=snap),
        # 48000 Hz x 2 ch x 2 bytes = 192000 B/s, so this is exactly 1.0s of audio.
        patch.object(streamer, "_stream_ffmpeg", new=AsyncMock(return_value=192000)),
        # Patch ONLY asyncio.sleep, never the whole module. Replacing
        # streamer.asyncio wholesale broke these tests the moment the sticky
        # group feature started using asyncio.create_task: the restore is
        # wrapped in contextlib.suppress(Exception), so a mock-induced error was
        # swallowed and the assertion saw no restore call -- reporting a
        # restore bug that did not exist.
        patch.object(streamer.asyncio, "sleep", new=AsyncMock()),
    ):
        streamer._locks = {}
        await streamer.announce(CLIENT_ID, TTS_URL, SOURCE_HOST)

    snap.set_group_stream.assert_not_called()
