"""Satellite → snapclient mapping resolver (add-on side)."""
from __future__ import annotations


class SatelliteNotMappedError(KeyError):
    pass


class NoMatchingMappingError(KeyError):
    pass


def _norm(wake_word: str) -> str:
    return wake_word.lower().replace("_", " ").replace("-", " ").strip()


def resolve(mappings: list[dict], satellite_id: str, wake_word: str | None) -> list[str]:
    """Return target snapclient IDs for a satellite + wake word.

    Prefers wake-word-specific rows over wildcard (*) rows.
    Wake word comparison is case-insensitive; underscores/hyphens equal spaces.
    """
    candidates = [m for m in mappings if m["satellite_id"] == satellite_id]
    if not candidates:
        raise SatelliteNotMappedError(satellite_id)

    if wake_word:
        norm_ww = _norm(wake_word)
        specific = [m for m in candidates if _norm(m.get("wake_word", "*")) == norm_ww]
        if specific:
            return specific[0]["target_snapclient_ids"]

    wildcard = [m for m in candidates if m.get("wake_word") == "*"]
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
    updated = [
        m for m in mappings
        if not (m["satellite_id"] == satellite_id and m.get("wake_word") == wake_word)
    ]
    updated.append({
        "satellite_id": satellite_id,
        "wake_word": wake_word,
        "target_snapclient_ids": target_ids,
        "notes": notes,
    })
    return updated


def delete(mappings: list[dict], satellite_id: str, wake_word: str) -> list[dict]:
    return [
        m for m in mappings
        if not (m["satellite_id"] == satellite_id and m.get("wake_word") == wake_word)
    ]
