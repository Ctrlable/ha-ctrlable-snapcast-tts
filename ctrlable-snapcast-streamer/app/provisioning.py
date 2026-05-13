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
    Link enabled clients to their per-client announcement streams.

    Each Snapcast announcement client lives permanently in its own group.
    Users change the source by switching the group's stream_id (e.g. to
    AirPlay or Music Assistant) — the client never moves between groups.
    Therefore the correct announce mechanism is stream switching, not client
    moving: record which stream to switch to (announce_stream_id) and which
    group to switch it on (announce_group_id = the client's current group).

    The scan NEVER changes any Snapcast stream or group state; it only reads
    topology and updates local state.json.

    Returns {client_id: status_message} for all enabled clients.
    """
    state = get_state()
    streams = await snap.list_streams()
    snap_clients = await snap.list_clients()

    stream_by_id = {s.id: s for s in streams}
    name_to_sid: dict[str, str] = {}
    for s in streams:
        n = _parse_stream_name_from_uri(s.uri)
        if n:
            name_to_sid[n] = s.id

    client_to_group: dict[str, str] = {c.id: c.current_group_id for c in snap_clients}

    results: dict[str, str] = {}
    changed = False

    for client_id, cs in state.clients.items():
        if not cs.enabled:
            continue

        cur_gid = client_to_group.get(client_id, "")
        expected_name = stream_name(client_id)
        announce_sid = name_to_sid.get(expected_name)

        # ── Strategy 1: named per-client stream ──────────────────────────────
        # The client's group will be switched TO this stream during announcements
        # and restored to whatever stream it was on beforehand.
        if announce_sid:
            announce_stream = stream_by_id[announce_sid]
            port = _parse_tcp_port(announce_stream.uri)
            if not port:
                results[client_id] = f"stream '{announce_sid}' has no parseable TCP port"
                continue

            cs.announce_group_id = cur_gid          # group to switch streams on
            cs.announce_stream_id = announce_sid    # stream to switch TO during announcement
            cs.announce_port = port
            # home_group_id kept for backward compat; not used for routing
            if not cs.home_group_id:
                cs.home_group_id = cur_gid
                cs.home_group_autodetected = True

            status = f"linked (port {port}, stream-switch mode)"
            results[client_id] = status
            _LOGGER.info("scan: %r %s", client_id, status)
            changed = True
            continue

        # ── Strategy 2: fallback — adopt current group's TCP port ────────────
        # Used for setups without named per-client streams (legacy).
        if cur_gid:
            from snapcast import SnapcastClient as _SC  # avoid circular; just for type ref
            # Need groups for this fallback
            groups = await snap.list_groups()
            cur_group = next((g for g in groups if g.id == cur_gid), None)
            if cur_group and cur_group.stream_id:
                existing = stream_by_id.get(cur_group.stream_id)
                if existing:
                    port = _parse_tcp_port(existing.uri)
                    if port:
                        cs.announce_group_id = cur_gid
                        cs.announce_port = port
                        if not cs.home_group_id:
                            cs.home_group_id = cur_gid
                            cs.home_group_autodetected = True
                        results[client_id] = f"linked (adopted current group TCP stream, port {port})"
                        _LOGGER.info("scan: %r adopted current group port %d", client_id, port)
                        changed = True
                        continue

        results[client_id] = (
            f"no stream named '{expected_name}' found — "
            "add snippet to snapserver.conf and reload Snapcast"
        )

    if changed:
        save_state()

    return results
