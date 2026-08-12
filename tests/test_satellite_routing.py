"""Which streamer serves which satellite.

These are pure-function tests of the resolution order, with no Home Assistant
import, because the property that matters is a routing decision and it should be
checkable without standing up a config entry.

The case that matters most is "EXISTING INSTALL": a lone entry has no satellites
list, so it must remain the catch-all and answer for everything. If that ever
breaks, every satellite in the house stops announcing at once.
"""
from __future__ import annotations

import pathlib

_SRC = (pathlib.Path(__file__).resolve().parents[1]
        / "custom_components" / "ctrlable_snapcast_tts" / "services.py").read_text()
_FN = _SRC[_SRC.index("def _get_client"):_SRC.index("def _source_host")]
_NS: dict = {"HomeAssistant": object, "AddonApiClient": object, "DOMAIN": "d"}
exec(compile(_FN, "services._get_client", "exec"), _NS)
get_client = _NS["_get_client"]


class FakeHass:
    def __init__(self, entries): self.data = {"d": entries}


ADDON = {"client": "addon", "satellites": ()}
NEW = {"client": "new", "satellites": ("atoms3r-echo-bca1a8",)}


def test_no_entries_returns_none():
    assert get_client(FakeHass({}), "anything") is None


def test_single_entry_is_catch_all():
    """The pre-change behaviour, which must survive untouched."""
    h = FakeHass({"e1": ADDON})
    assert get_client(h, "atoms3r-echo-bca1a8") == "addon"
    assert get_client(h, "") == "addon"
    assert get_client(FakeHass({"e1": {"client": "addon"}}), "x") == "addon"


def test_claimed_satellite_goes_to_its_own_entry():
    h = FakeHass({"e1": ADDON, "e2": NEW})
    assert get_client(h, "atoms3r-echo-bca1a8") == "new"


def test_unclaimed_satellites_stay_on_the_catch_all():
    """The property that makes a one-room migration safe."""
    h = FakeHass({"e1": ADDON, "e2": NEW})
    assert get_client(h, "cores3-va") == "addon"
    assert get_client(h, "respeaker-xvf3800") == "addon"


def test_entry_order_does_not_decide_the_route():
    """Insertion order is arbitrary, so it must not affect resolution."""
    assert get_client(FakeHass({"e2": NEW, "e1": ADDON}), "cores3-va") == "addon"


def test_falls_back_to_first_entry_when_no_catch_all_exists():
    assert get_client(FakeHass({"e2": NEW}), "cores3-va") == "new"


def test_prefix_must_not_steal_a_route():
    """Exact matching only.

    'atoms3r' is a prefix of 'atoms3r-echo-bca1a8'. If matching were fuzzy, an
    entry claiming the short name would silently capture the longer satellite --
    the add-on's own mapping resolver had to grow an ambiguity guard for exactly
    this, and the failure is invisible: audio simply goes to the wrong streamer.
    """
    h = FakeHass({"e1": ADDON, "e2": {"client": "new", "satellites": ("atoms3r",)}})
    assert get_client(h, "atoms3r-echo-bca1a8") == "addon"


def test_announce_without_a_satellite_uses_the_catch_all():
    """target_snapclient_ids calls carry no satellite id.

    Those ids only mean something on a particular streamer's snapserver, so
    they must land on the catch-all rather than an arbitrary entry.
    """
    assert get_client(FakeHass({"e1": ADDON, "e2": NEW}), "") == "addon"
