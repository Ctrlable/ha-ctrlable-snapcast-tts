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
    Link enabled clients to their per-client announcement streams and groups.

    Strategy:
    1. Named: find the stream named ann_<normalized_client_id> and the group
       bound to it.  That group becomes announce_group_id.  The client's
       current group (if different) becomes home_group_id so the streamer can
       move the client away during an announcement and restore it after.

    2. Fallback: no named stream found → adopt the current group's TCP stream
       (legacy single-stream setups).

    Returns {client_id: status_message} for all enabled clients.
    """
    state = get_state()
    streams = await snap.list_streams()
    groups = await snap.list_groups()
    snap_clients = await snap.list_clients()

    stream_by_id = {s.id: s for s in streams}
    name_to_sid: dict[str, str] = {}
    for s in streams:
        n = _parse_stream_name_from_uri(s.uri)
        if n:
            name_to_sid[n] = s.id

    client_to_group: dict[str, str] = {c.id: c.current_group_id for c in snap_clients}
    # Reverse map: first group bound to each stream_id wins
    stream_to_group_id: dict[str, str] = {}
    for g in groups:
        if g.stream_id and g.stream_id not in stream_to_group_id:
            stream_to_group_id[g.stream_id] = g.id
    group_by_id = {g.id: g for g in groups}

    results: dict[str, str] = {}
    changed = False

    for client_id, cs in state.clients.items():
        if not cs.enabled:
            continue

        cur_gid = client_to_group.get(client_id, "")
        expected_name = stream_name(client_id)
        announce_sid = name_to_sid.get(expected_name)

        # ── Strategy 1: named per-client stream ──────────────────
        if announce_sid:
            announce_stream = stream_by_id[announce_sid]
            port = _parse_tcp_port(announce_stream.uri)
            if not port:
                results[client_id] = f"stream '{announce_sid}' has no parseable TCP port"
                continue

            announce_gid = stream_to_group_id.get(announce_sid)

            if announce_gid:
                cs.announce_group_id = announce_gid
                cs.announce_port = port
                if cur_gid and cur_gid != announce_gid:
                    # Client is currently in a different (home) group — switching will occur.
                    cs.home_group_id = cur_gid
                    cs.home_group_autodetected = True
                    status = f"linked (port {port}, home≠announce — group switching enabled)"
                else:
                    # Client already sits in its announce group; no move needed.
                    cs.home_group_id = announce_gid
                    cs.home_group_autodetected = True
                    status = f"linked (port {port}, client already in announce group)"
                results[client_id] = status
                _LOGGER.info("scan: %r %s", client_id, status)
                changed = True
                continue

            # Named stream exists but no group is bound to it yet.
            # Bind the current group only when it is not shared with other enabled clients.
            if cur_gid:
                cur_group = group_by_id.get(cur_gid)
                shared_with = [
                    cid for cid in (cur_group.client_ids if cur_group else [])
                    if cid != client_id and state.clients.get(cid, ClientState()).enabled
                ]
                if not shared_with:
                    try:
                        await snap.set_group_stream(cur_gid, announce_sid)
                        cs.announce_group_id = cur_gid
                        cs.announce_port = port
                        if not cs.home_group_id:
                            cs.home_group_id = cur_gid
                            cs.home_group_autodetected = True
                        results[client_id] = f"linked (bound group to stream '{announce_sid}', port {port})"
                        _LOGGER.info("scan: %r bound group %r to stream %r", client_id, cur_gid, announce_sid)
                        changed = True
                        continue
                    except Exception as exc:
                        _LOGGER.warning("scan: %r failed to bind group — %s", client_id, exc)

            results[client_id] = (
                f"stream '{announce_sid}' found (port {port}) but no group is bound to it — "
                "ensure snapserver.conf has the stream, reload Snapcast, then re-scan"
            )
            continue

        # ── Strategy 2: fallback — adopt current group's TCP stream ──────────
        if cur_gid:
            cur_group = group_by_id.get(cur_gid)
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
