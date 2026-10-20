"""Tests for the hplip fork's observe source (sources.hplip_fork_findings).

Why it exists: a fork is invisible to Buildroot's pkg-stats — it scans the stock
recipe, and the stock recipe is switched off. Nothing in the toolchain then
notices our 3.26.6 pin going stale while hplip publishes a release that closes a
CVE we carry the fork for. divergence.toml -> hplip-3.26.6-fork names this as the
fork's observe_source, for exactly the PengyR reason (images shipped 1.10.0 while
upstream was at 1.11.1 and nothing said so).

The fixture is frozen from the SourceForge files page, with the older-version
rows added by hand: the live page names only the current release, and a
one-line page would pass any parser. The comparison is what is tested.

Run: python3 -m pytest tests -q   (from tools/pengyos-nightly)
"""

from pathlib import Path

from pengyos_nightly import sources

FIX = Path(__file__).parent / "fixtures"
LISTING = (FIX / "hplip-releases.html").read_text()


def lanes(findings):
    return [(f["lane"], f["component"], f["available"]) for f in findings]


def test_the_fixture_names_the_current_release():
    """Guard the fixture: if it stops carrying a .tar.gz the tests below assert
    nothing, because the parser would find no versions at all."""
    versions = set(sources.re.findall(r"hplip-([0-9][0-9.]*)\.tar\.gz", LISTING))
    assert "3.26.6" in versions, sorted(versions)
    assert len(versions) >= 5, sorted(versions)


def test_current_pin_is_silent():
    """The negative control: a source that always reports is a source the operator
    learns to ignore."""
    assert sources.hplip_fork_findings(LISTING, "3.26.6") == []


def test_a_newer_release_is_offered_as_routine():
    """The fork exists to be deleted when Buildroot catches up; hplip's releases on
    the 3.x line are point releases, so the loop may take them."""
    f = sources.hplip_fork_findings(LISTING, "3.26.4")
    assert lanes(f) == [("routine", "hplip-pengyos", "3.26.6")], f
    assert "newer than the fork" in f[0]["note"]


def test_the_newest_release_is_the_one_offered_not_the_first_match():
    """3.24.11 sorts lexically after 3.26.6 in some orderings and 3.9.12 after
    3.26.6 in all of them; a string compare would offer the wrong release."""
    assert sources.hplip_fork_findings(LISTING, "3.9.12")[0]["available"] == "3.26.6"
    assert sources.hplip_fork_findings(LISTING, "3.21.12")[0]["available"] == "3.26.6"


def test_signatures_self_extracters_and_checksums_are_not_releases():
    """The page carries .asc, .sig, .md5sum and .run alongside every tarball.
    Counting those as versions would report a finding every night."""
    listing = ('<a href="hplip-3.99.0.tar.gz.asc">a</a>\n'
               '<a href="hplip-3.99.0.tar.gz.sig">s</a>\n'
               '<a href="hplip-3.99.0.tar.gz.md5sum">m</a>\n'
               '<a href="hplip-3.99.0.run">r</a>\n'
               '<a href="hplip-3.26.6-plugin-0.2.0.run">p</a>')
    assert sources.hplip_fork_findings(listing, "3.26.6") == []


def test_prereleases_are_not_offered_as_a_bump():
    assert sources.hplip_fork_findings('<a href="hplip-3.27.0rc1.tar.gz">r</a>',
                                       "3.26.6") == []


def test_the_pin_is_compared_as_a_version_not_a_string():
    """Lexically "3.9.12" > "3.26.6", which would offer a 2018-era release as an
    upgrade to a 3.26.6 pin. This is the shape hplip's own numbering punishes."""
    assert sources.hplip_fork_findings(LISTING, "3.26.6") == []
    f = sources.hplip_fork_findings('<a href="hplip-3.100.0.tar.gz">x</a>', "3.26.6")
    assert lanes(f) == [("routine", "hplip-pengyos", "3.100.0")], f


def test_the_recipe_pin_is_what_gets_compared():
    """The observe source reads the fork's own version line, not Buildroot's pin.
    Asked at Buildroot's 3.17.10 it must name 3.26.6 - that is the whole reason the
    fork exists, and it is the answer a future Buildroot release makes."""
    mk = (Path(__file__).resolve().parents[3] /
          "buildroot-external/package/hplip-pengyos/hplip-pengyos.mk")
    pinned = sources.mk_value(mk.read_text(), "HPLIP_PENGYOS_VERSION")
    assert pinned == "3.26.6", pinned
    assert sources.hplip_fork_findings(LISTING, pinned) == []
    f = sources.hplip_fork_findings(LISTING, "3.17.10")
    assert lanes(f) == [("routine", "hplip-pengyos", "3.26.6")], f
