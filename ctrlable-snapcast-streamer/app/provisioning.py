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
    try:
        parsed = urlparse(uri)
        if parsed.scheme != "tcp":
            return None
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
    Scan Snapcast for TCP sources matching enabled clients by name or port.
    Sets announce_group_id and home_group_id in state for matched clients.
    Returns {client_id: status_message} for all enabled clients.
    """
    state = get_state()
    streams = await snap.list_streams()
    groups = await snap.list_groups()
    snap_clients = await snap.list_clients()

    # Index streams by name and by port
    port_to_sid: dict[int, str] = {}
    name_to_sid: dict[str, str] = {}
    for s in streams:
        p = _parse_tcp_port(s.uri)
        if p:
            port_to_sid[p] = s.id
        n = _parse_stream_name_from_uri(s.uri)
        if n:
            name_to_sid[n] = s.id

    # Index groups by stream and client lookup
    client_to_group: dict[str, str] = {c.id: c.current_group_id for c in snap_clients}
    group_by_id = {g.id: g for g in groups}

    results: dict[str, str] = {}
    changed = False

    for client_id, cs in state.clients.items():
        if not cs.enabled or not cs.announce_port:
            continue

        # Match stream by canonical name first, then by port
        expected_name = stream_name(client_id)
        stream_id = name_to_sid.get(expected_name) or port_to_sid.get(cs.announce_port)

        if not stream_id:
            results[client_id] = f"no TCP stream on port {cs.announce_port} — add to snapserver.conf"
            continue

        # Find the group containing our client that is bound to this stream
        cur_gid = client_to_group.get(client_id)
        cur_group = group_by_id.get(cur_gid or "")

        if cur_group and cur_group.stream_id == stream_id:
            # Client is already in a group bound to the announcement stream
            cs.announce_group_id = cur_gid or ""
            if not cs.home_group_id:
                cs.home_group_id = cur_gid or ""
                cs.home_group_autodetected = True
            results[client_id] = f"linked → group {(cur_gid or '')[:8]}…"
            changed = True
        else:
            # Find any group bound to this stream
            bound = [g for g in groups if g.stream_id == stream_id]
            if bound:
                cs.announce_group_id = bound[0].id
                results[client_id] = f"stream found, linked → group {bound[0].id[:8]}… (client not in it yet)"
                changed = True
            else:
                results[client_id] = f"stream {stream_id} found but no group is bound to it"

    if changed:
        save_state()

    return results
