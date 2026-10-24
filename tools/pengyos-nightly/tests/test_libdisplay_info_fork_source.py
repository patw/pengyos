"""Tests for the libdisplay-info fork's observe source
(sources.libdisplay_info_fork_findings).

Why it exists: the fork pins 0.3.0, below Buildroot's 0.4.0, because cosmic-comp
links libdisplay-info.so.3 and its libdisplay-info-sys crate accepts only < 0.4.0.
pkg-stats scans the stock recipe, which is switched off, and NVD lists no CPE for
libdisplay-info, so CVE-2026-112271 (published 2026-10-19, affects 0.3.0) never
matched anything. divergence.toml -> libdisplay-info-0.3.0-fork names this as the
fork's observe_source.

The fixture is `git ls-remote --tags` of upstream, frozen on 2026-10-23: 0.1.0 to
0.4.0, every tag annotated (each listed twice, once with ^{}).

Run: python3 -m pytest tests -q   (from tools/pengyos-nightly)
"""

from pathlib import Path

from pengyos_nightly import sources

FIX = Path(__file__).parent / "fixtures"
TAGS = sources.parse_ls_remote((FIX / "ls-remote-libdisplay-info.txt").read_text())


def lanes(findings):
    return [(f["lane"], f["component"], f["available"]) for f in findings]


def test_the_fixture_carries_the_pin_and_the_newer_series():
    """Guard the fixture: without 0.3.0 and 0.4.0 the tests below assert nothing
    about what they claim to."""
    assert "0.3.0" in TAGS and "0.4.0" in TAGS, sorted(TAGS)
    assert not any(t.endswith("^{}") for t in TAGS)


def test_the_newer_series_is_major_and_says_what_blocks_it():
    """Today's state: 0.4.0 exists and the fork cannot take it. Reported every
    night, as a human decision, with the reason in the note."""
    f = sources.libdisplay_info_fork_findings(TAGS, "0.3.0")
    assert lanes(f) == [("major", "libdisplay-info-pengyos", "0.4.0")], f
    assert "libdisplay-info-sys" in f[0]["note"]


def test_a_point_release_in_the_pinned_series_wins_over_a_newer_series():
    """0.3.1 is what the fork can take without moving cosmic-comp, and the likeliest
    carrier of a CVE fix. Reporting only "0.4.1 available" would bury it."""
    f = sources.libdisplay_info_fork_findings(["0.3.0", "0.3.1", "0.4.0", "0.4.1"], "0.3.0")
    assert lanes(f) == [("routine", "libdisplay-info-pengyos", "0.3.1")], f
    assert "point release" in f[0]["note"]


def test_nothing_newer_is_silent():
    """The negative control: a source that always reports is a source the operator
    learns to ignore."""
    assert sources.libdisplay_info_fork_findings(TAGS, "0.4.0") == []
    assert sources.libdisplay_info_fork_findings(["0.2.0", "0.3.0"], "0.3.0") == []


def test_the_pin_is_compared_as_a_version_not_a_string():
    """0.3.10 > 0.3.9 > 0.3.2; a string compare gets all of that wrong."""
    f = sources.libdisplay_info_fork_findings(["0.3.2", "0.3.10", "0.3.9"], "0.3.2")
    assert lanes(f) == [("routine", "libdisplay-info-pengyos", "0.3.10")], f


def test_malformed_and_prefixed_tags_are_not_releases():
    """Upstream tags bare versions. Release candidates, two-part and v-prefixed
    tags are not releases of a version this source should act on."""
    tags = ["0.3.0", "0.9.0-rc1", "0.9", "v0.9.0", "0.9.0-dev"]
    assert sources.libdisplay_info_fork_findings(tags, "0.3.0") == []


def test_the_recipe_pin_is_what_gets_compared():
    """The observe source reads the fork's own version line, which must be the
    soversion-3 series cosmic-comp links."""
    mk = (Path(__file__).resolve().parents[3] /
          "buildroot-external/package/libdisplay-info-pengyos/libdisplay-info-pengyos.mk")
    pinned = sources.mk_value(mk.read_text(), "LIBDISPLAY_INFO_PENGYOS_VERSION")
    assert pinned == "0.3.0", pinned
    f = sources.libdisplay_info_fork_findings(TAGS, pinned)
    assert lanes(f) == [("major", "libdisplay-info-pengyos", "0.4.0")], f
