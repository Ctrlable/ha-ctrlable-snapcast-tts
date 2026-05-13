"""Snapserver.conf snippet generation and Snapcast stream discovery/linking."""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

from snapcast import SnapcastClient
from state import ClientState, get_state, save_state

_LOGGER = logging.getLogger(__name__)
_STREAM_FORMAT = "sampleformat=48000:16:2&codec=pcm"


def normalize_id(client_id: str) -> str:
    """Strip non-alphanumeric chars to form a valid Snapcast stream name segment."""
    return re.sub(r"[^a-zA-Z0-9]", "", client_id)


def stream_name(client_id: str) -> str:
    return f"ann_{normalize_id(client_id)}"


def source_line(client_id: str, port: int) -> str:
    name = stream_name(client_id)
    return f"source = tcp://0.0.0.0?name={name}&port={port}&mode=server&{_STREAM_FORMAT}"


def get_config_snippet(clients: dict[str, ClientState]) -> str:
    """Return the full managed snapserver.conf block for all enabled clients."""
    lines = ["# >>> ctrlable managed >>>"]
    for cid, cs in clients.items():
        if cs.enabled and cs.announce_port:
            lines.append(source_line(cid, cs.announce_port))
    lines.append("# <<< ctrlable managed <<<")
    return "\n".join(lines)


def _parse_tcp_port(uri: str) -> int | None:
    """Extract port from a Snapcast TCP stream URI.

    Snapcast stores port in the netloc (tcp://0.0.0.0:5200?...) after
    parsing the config, even though the config uses tcp://0.0.0.0?port=5200.
    Check netloc first, then fall back to query string.
    """
    try:
        parsed = urlparse(uri)
        if parsed.scheme != "tcp":
            return None
        if parsed.port:
            return parsed.port
        vals = parse_qs(parsed.query).get("port", [])
        return int(vals[0]) if vals else None
    except Exception:
        return None


def _parse_stream_name_from_uri(uri: str) -> str | None:
    try:
        parsed = urlparse(uri)
        vals = parse_qs(parsed.query).get("name", [])
        return vals[0] if vals else None
    except Exception:
        return None


async def scan_and_link(snap: SnapcastClient) -> dict[str, str]:
    """
    Link enabled clients to their announcement streams using two strategies:

    1. Adopt: client's current group is already bound to a TCP stream → use it.
       This handles pre-existing announcement Snapcast setups.

    2. Bind: a stream matching our name/port exists but the group isn't bound
       to it yet → call Group.SetStream to wire it up.

    Returns {client_id: status_message} for all enabled clients.
    """
    state = get_state()
    streams = await snap.list_streams()
    groups = await snap.list_groups()
    snap_clients = await snap.list_clients()

    stream_by_id = {s.id: s for s in streams}
    port_to_sid: dict[int, str] = {}
    name_to_sid: dict[str, str] = {}
    for s in streams:
        p = _parse_tcp_port(s.uri)
        if p:
            port_to_sid[p] = s.id
        n = _parse_stream_name_from_uri(s.uri)
        if n:
            name_to_sid[n] = s.id

    client_to_group: dict[str, str] = {c.id: c.current_group_id for c in snap_clients}
    group_by_id = {g.id: g for g in groups}

    results: dict[str, str] = {}
    changed = False

    for client_id, cs in state.clients.items():
        if not cs.enabled:
            continue

        cur_gid = client_to_group.get(client_id)
        cur_group = group_by_id.get(cur_gid or "")

        # ── Strategy 1: adopt existing TCP stream ────────────────
        # If the client's current group is already bound to a TCP stream,
        # use that stream and port directly (no config changes needed).
        if cur_group and cur_group.stream_id:
            existing = stream_by_id.get(cur_group.stream_id)
            if existing:
                port = _parse_tcp_port(existing.uri)
                if port:
                    cs.announce_group_id = cur_gid or ""
                    cs.announce_port = port
                    if not cs.home_group_id:
                        cs.home_group_id = cur_gid or ""
                        cs.home_group_autodetected = True
                    results[client_id] = f"linked (adopted existing TCP stream on port {port})"
                    _LOGGER.info("scan: %r adopted stream %r port %d", client_id, existing.id, port)
                    changed = True
                    continue

        # ── Strategy 2: bind group to our named/ported stream ────
        # A stream matching our allocation exists; rebind the group to it.
        expected_name = stream_name(client_id)
        our_sid = name_to_sid.get(expected_name) or (
            port_to_sid.get(cs.announce_port) if cs.announce_port else None
        )
        if our_sid and cur_gid:
            try:
                await snap.set_group_stream(cur_gid, our_sid)
                cs.announce_group_id = cur_gid
                if not cs.home_group_id:
                    cs.home_group_id = cur_gid
                    cs.home_group_autodetected = True
                results[client_id] = f"linked (bound group to stream '{our_sid}')"
                _LOGGER.info("scan: %r bound group %r to stream %r", client_id, cur_gid, our_sid)
                changed = True
                continue
            except Exception as exc:
                _LOGGER.warning("scan: failed to bind group for %r — %s", client_id, exc)

        results[client_id] = (
            f"no TCP stream found on port {cs.announce_port} — "
            "add snippet to snapserver.conf and reload Snapcast"
        )

    if changed:
        save_state()

    return results
