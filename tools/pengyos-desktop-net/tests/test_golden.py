"""Golden-config grounding for pengyos-desktop-net.

The spec's verification contract demands that **emit is golden/deterministic**:
the same profile must always render the same bytes, so the overlay is safe to
diff in CI and ship as a pinned Buildroot overlay.

These tests anchor that guarantee with a *committed* golden fixture
(``tests/fixtures/golden/golden.json`` — the exact ``emit_json()`` document) and
assert that:

* ``emit_json()`` reproduces the committed golden document **byte-for-byte**
  (a regression anchor — drift fails the test, forcing a deliberate golden
  update).
* ``emit`` writes each file to disk with exactly the golden content
  (the on-disk overlay, not just the JSON, matches).
* the golden panel default still lists ``cosmic-applet-network`` and the golden
  TOML artifacts still parse (the core spec guarantee is locked into the fixture).

The suite is standard-library-only at test time, so it needs no network, root,
display, or hardware.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from pengyos_desktop_net import emit as em
from pengyos_desktop_net import profile as prof

# Repo root = parent of this tests/ dir.
ROOT = Path(__file__).resolve().parent.parent

# The committed golden fixture: the exact ``emit_json()`` document.
GOLDEN_JSON = ROOT / "tests" / "fixtures" / "golden" / "golden.json"


def _load_golden() -> dict:
    """Load and parse the committed golden fixture."""
    assert GOLDEN_JSON.is_file(), (
        f"golden fixture missing at {GOLDEN_JSON} — commit it, do not delete"
    )
    return json.loads(GOLDEN_JSON.read_text("utf-8"))


def test_golden_fixture_committed_nonempty() -> None:
    """The golden fixture exists and is non-empty (guards accidental deletion)."""
    assert GOLDEN_JSON.stat().st_size > 0


def test_emit_matches_golden_byte_for_byte() -> None:
    """``emit_json()`` reproduces the committed golden document exactly."""
    actual = em.emit_json()
    expected = GOLDEN_JSON.read_text("utf-8")
    assert actual == expected, (
        "emit drift: run `python3 -m pengyos_desktop_net emit --json > "
        "tests/fixtures/golden/golden.json` to re-baseline (deliberately)"
    )


def test_emit_matches_golden_on_disk(tmp_path: Path) -> None:
    """``emit`` writes each file with exactly the golden content."""
    golden = _load_golden()
    out = str(tmp_path / "overlay")
    em.emit(out)
    for art in golden["artifacts"]:
        rel = art["path"].lstrip("/")
        expected = art["content"]
        actual = (Path(out) / rel).read_text("utf-8")
        assert actual == expected, f"{rel} drifted from the golden fixture"


def test_emit_is_deterministic_against_itself() -> None:
    """Two emissions of the golden document are identical."""
    assert em.emit_json() == em.emit_json()


def test_golden_panel_lists_network_applet() -> None:
    """The golden panel default still pins the network applet (core guarantee)."""
    golden = _load_golden()
    panel = next(a for a in golden["artifacts"] if a["kind"] == "cosmic-panel")
    assert f'applet="{prof.COSMIC_APPLET_NETWORK}"' in panel["content"]


def test_golden_toml_artifacts_parse() -> None:
    """The golden TOML artifacts are valid TOML (guards on-target drift)."""
    golden = _load_golden()
    for art in golden["artifacts"]:
        if art["path"].endswith(".toml"):
            tomllib.loads(art["content"])  # raises on malformed TOML


def test_golden_covers_all_kinds() -> None:
    """The golden fixture contains one artifact of every emitted kind."""
    golden = _load_golden()
    kinds = {a["kind"] for a in golden["artifacts"]}
    assert kinds == {
        "networkmanager",
        "wpa_supplicant",
        "wifi-firmware",
        "cosmic-panel",
        "cosmic-settings",
    }
