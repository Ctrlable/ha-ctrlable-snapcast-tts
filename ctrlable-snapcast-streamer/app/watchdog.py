"""Startup watchdog — verifies Snapcast connectivity and logs stream state."""
from __future__ import annotations

import logging

from snapcast import SnapcastRPCError, SnapcastTimeoutError, get_client
from state import get_state

_LOGGER = logging.getLogger(__name__)


async def run_watchdog() -> bool:
    """
    Verify Snapcast is reachable on startup and log each enabled client's
    current stream.  If a client's group is stuck on its announce stream after
    a mid-announcement crash, the next announce() call will detect the mismatch
    and restore correctly via its finally block — no active recovery needed here.

    Returns True if Snapcast is reachable, False otherwise (caller sets degraded).
    """
    state = get_state()
    if not state.clients:
        _LOGGER.info("Watchdog: no clients configured yet")
        return True

    try:
        snap = get_client()
        groups = await snap.list_groups()
        group_map = {g.id: g for g in groups}
    except (SnapcastRPCError, SnapcastTimeoutError, RuntimeError) as exc:
        _LOGGER.error("Watchdog: Snapcast unreachable — %s", exc)
        return False

    for client_id, cs in state.clients.items():
        if not cs.enabled or not cs.announce_group_id:
            continue
        grp = group_map.get(cs.announce_group_id)
        if grp is None:
            _LOGGER.warning(
                "Watchdog: %r announce_group %r not found in Snapcast — re-scan needed",
                client_id, cs.announce_group_id,
            )
            continue
        current_stream = grp.stream_id
        if cs.announce_stream_id and current_stream != cs.announce_stream_id:
            _LOGGER.info(
                "Watchdog: %r group is on %r (not announce stream %r) — will restore on next announce",
                client_id, current_stream, cs.announce_stream_id,
            )
        else:
            _LOGGER.info("Watchdog: %r OK (stream=%r)", client_id, current_stream)

    _LOGGER.info("Watchdog complete")
    return True
