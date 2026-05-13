"""Startup recovery watchdog — evicts clients from announce groups before HTTP server starts."""
from __future__ import annotations

import logging

from .snapcast import get_client, SnapcastRPCError, SnapcastTimeoutError
from .state import get_state

_LOGGER = logging.getLogger(__name__)


async def run_watchdog() -> bool:
    """
    For every client in state that has an announce_group_id, check if that client
    is currently sitting in its announce group and move it back to home.

    Returns True if all recoveries succeeded (or nothing needed recovery).
    Returns False if Snapcast was unreachable — caller should set degraded mode.
    """
    state = get_state()
    if not state.clients:
        _LOGGER.info("Watchdog: no clients configured yet, nothing to do")
        return True

    try:
        client = get_client()
        groups = await client.list_groups()
        group_map = {g.id: g for g in groups}
    except (SnapcastRPCError, SnapcastTimeoutError, RuntimeError) as exc:
        _LOGGER.error("Watchdog: Snapcast unreachable — %s", exc)
        return False

    recovered = 0
    failed = 0

    for client_id, cs in state.clients.items():
        if not cs.announce_group_id or not cs.home_group_id:
            continue
        ann_group = group_map.get(cs.announce_group_id)
        if ann_group is None:
            continue
        if client_id not in ann_group.client_ids:
            continue
        # Client is stuck in announce group — move it home
        try:
            snap = get_client()
            await snap.move_client_to_group(client_id, cs.home_group_id)
            await snap.remove_client_from_group(client_id, cs.announce_group_id)
            _LOGGER.warning("Watchdog: recovered client %r → home group %r", client_id, cs.home_group_id)
            recovered += 1
        except Exception as exc:
            _LOGGER.error("Watchdog: failed to recover client %r — %s", client_id, exc)
            failed += 1

    _LOGGER.info("Watchdog complete: %d recovered, %d failed", recovered, failed)
    return failed == 0
