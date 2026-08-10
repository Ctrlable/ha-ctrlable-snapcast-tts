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
        # Tolerate the two ways a satellite can be named, because they are not
        # interchangeable and getting it wrong fails SILENTLY: HTTP 200 from HA,
        # no group switch, no sound. A device knows its own ESPHome node name
        # ("esphome-web-75357c-xiao-esp32s3"); HA knows an entity slug that also
        # carries the friendly name and area ("bedroom-3-esphome-web-...-assist-
        # satellite"). Mappings get created from HA's list, devices send what
        # they know, and the two never meet.
        #
        # Match when one is contained in the other, and ONLY when exactly one
        # mapping qualifies -- an ambiguous match would route audio to the wrong
        # room, which is worse than not routing it at all.
        norm = _norm(satellite_id)
        loose = [m for m in mappings
                 if norm and (norm in _norm(m["satellite_id"])
                              or _norm(m["satellite_id"]) in norm)]
        if len(loose) == 1:
            candidates = loose
        elif len(loose) > 1:
            raise SatelliteNotMappedError(
                f"{satellite_id} matches {len(loose)} mappings ambiguously; "
                f"make the satellite_id exact")

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
