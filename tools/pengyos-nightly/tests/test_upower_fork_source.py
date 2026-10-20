"""Tests for the upower fork's observe source (sources.upower_fork_findings).

Why it exists: a fork is invisible to Buildroot's pkg-stats - it scans the stock
recipe, and the stock recipe is switched off - and NVD lists no CPE for upower, so
there is no CVE feed to fall back on. Nothing else notices our 1.91.5 pin going
stale. divergence.toml -> upower-1.91.5-fork names this as the fork's
observe_source, for the PengyR reason (images shipped 1.10.0 while upstream was at
1.11.1 and nothing said so).

The fixture is `git ls-remote --tags` of upstream, frozen on 2026-10-20: it carries
all three tag schemes upstream has used (DeviceKit-power-NNN, UPOWER_0_99_N,
vX.Y.Z), annotated and lightweight tags, and v1.90.10, which sorts before v1.90.2
as a string.

Run: python3 -m pytest tests -q   (from tools/pengyos-nightly)
"""

from pathlib import Path

from pengyos_nightly import sources

FIX = Path(__file__).parent / "fixtures"
TAGS = sources.parse_ls_remote((FIX / "ls-remote-upower.txt").read_text())


def lanes(findings):
    return [(f["lane"], f["component"], f["available"]) for f in findings]


def test_the_fixture_carries_the_current_release_and_the_old_schemes():
    """Guard the fixture: without v1.91.5 and the legacy tags the tests below
    assert nothing about what they claim to."""
    assert "v1.91.5" in TAGS and "v0.99.19" in TAGS, sorted(TAGS)[-5:]
    assert "UPOWER_0_99_13" in TAGS and "DeviceKit-power-014" in TAGS
    assert not any(t.endswith("^{}") for t in TAGS)


def test_current_pin_is_silent():
    """The negative control: a source that always reports is a source the operator
    learns to ignore."""
    assert sources.upower_fork_findings(TAGS, "1.91.5") == []


def test_a_point_release_is_routine():
    f = sources.upower_fork_findings(["v1.91.5", "v1.91.6"], "1.91.5")
    assert lanes(f) == [("routine", "upower-pengyos", "1.91.6")], f
    assert "point release" in f[0]["note"]


def test_a_new_series_is_major():
    """upowerd's D-Bus API is what the panel, Settings and power-profiles-daemon
    read; a series change is a human decision."""
    f = sources.upower_fork_findings(["v1.91.5", "v1.92.0"], "1.91.5")
    assert lanes(f) == [("major", "upower-pengyos", "1.92.0")], f


def test_the_pin_is_compared_as_a_version_not_a_string():
    """v1.90.10 > v1.90.9 > v1.90.2; a string compare gets all of that wrong."""
    f = sources.upower_fork_findings(TAGS, "1.90.9")
    assert lanes(f) == [("major", "upower-pengyos", "1.91.5")], f
    f = sources.upower_fork_findings(["v1.90.2", "v1.90.10", "v1.90.9"], "1.90.2")
    assert lanes(f) == [("routine", "upower-pengyos", "1.90.10")], f


def test_legacy_and_malformed_tags_are_not_releases():
    """UPOWER_0_99_N and DeviceKit-power-NNN are the old naming; release
    candidates and suffixed tags are not releases of a version."""
    tags = ["UPOWER_9_99_0", "DeviceKit-power-999", "v1.99.0-rc1", "v1.99", "1.99.0"]
    assert sources.upower_fork_findings(tags, "1.91.5") == []


def test_the_recipe_pin_is_what_gets_compared():
    """The observe source reads the fork's own version line. Asked at Buildroot's
    0.99.19 it must name 1.91.5 - the reason the fork exists."""
    mk = (Path(__file__).resolve().parents[3] /
          "buildroot-external/package/upower-pengyos/upower-pengyos.mk")
    pinned = sources.mk_value(mk.read_text(), "UPOWER_PENGYOS_VERSION")
    assert pinned == "1.91.5", pinned
    assert sources.upower_fork_findings(TAGS, pinned) == []
    f = sources.upower_fork_findings(TAGS, "0.99.19")
    assert lanes(f) == [("major", "upower-pengyos", "1.91.5")], f
