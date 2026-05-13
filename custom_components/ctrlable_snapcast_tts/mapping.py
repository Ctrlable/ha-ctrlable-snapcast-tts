"""Satellite → snapclient mapping resolver."""
from __future__ import annotations


class SatelliteNotMappedError(Exception):
    pass


class NoMatchingMappingError(Exception):
    pass


def resolve(mappings: list[dict], satellite_id: str, wake_word: str | None) -> list[str]:
    """Return ordered list of target snapclient IDs for a satellite + wake word.

    Prefers wake-word-specific rows over wildcard (*) rows.
    Raises SatelliteNotMappedError if satellite has no rows at all.
    Raises NoMatchingMappingError if satellite is known but no row matches.
    """
    candidates = [m for m in mappings if m["satellite_id"] == satellite_id]
    if not candidates:
        raise SatelliteNotMappedError(satellite_id)

    if wake_word:
        specific = [m for m in candidates if m["wake_word"] == wake_word]
        if specific:
            return specific[0]["target_snapclient_ids"]

    wildcard = [m for m in candidates if m["wake_word"] == "*"]
    if wildcard:
        return wildcard[0]["target_snapclient_ids"]

    raise NoMatchingMappingError(satellite_id, wake_word)


def upsert(
    mappings: list[dict],
    satellite_id: str,
    wake_word: str,
    target_ids: list[str],
    notes: str = "",
) -> list[dict]:
    """Insert or replace the row matching (satellite_id, wake_word)."""
    updated = [
        m for m in mappings
        if not (m["satellite_id"] == satellite_id and m["wake_word"] == wake_word)
    ]
    updated.append({
        "satellite_id": satellite_id,
        "wake_word": wake_word,
        "target_snapclient_ids": target_ids,
        "notes": notes,
    })
    return updated


def remove(mappings: list[dict], satellite_id: str, wake_word: str) -> list[dict]:
    """Remove the row matching (satellite_id, wake_word)."""
    return [
        m for m in mappings
        if not (m["satellite_id"] == satellite_id and m["wake_word"] == wake_word)
    ]


def label(mapping: dict) -> str:
    """Human-readable label for a mapping row (used in selectors)."""
    sat = mapping["satellite_id"]
    ww = mapping["wake_word"]
    targets = ", ".join(mapping.get("target_snapclient_ids", []))
    return f"{sat} [{ww}] → {targets}"
