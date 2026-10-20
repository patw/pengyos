"""Tests for the ffmpeg fork's observe source (sources.ffmpeg_fork_findings).

Why it exists: a fork is invisible to Buildroot's pkg-stats — it scans the stock
recipe, and the stock recipe is switched off. Nothing in the toolchain then notices
our 9.0.2 pin going stale while ffmpeg publishes a release that closes a CVE we
carry the fork for. That is the PengyR case (images shipped 1.10.0 while upstream
was at 1.11.1 and nothing said so), and divergence.toml requires every fork to name
an observe_source to prevent it. divergence.toml -> ffmpeg-9.0.2-fork names this one.

ffmpeg publishes no git tags, so the source is the release directory listing at
https://ffmpeg.org/releases/ — a frozen fixture, same rule as the openssh and PengyR
ones: the comparison is what is tested, not whatever upstream ships next.

Run: python3 -m pytest tests -q   (from tools/pengyos-nightly)
"""

from pathlib import Path

from pengyos_nightly import sources

FIX = Path(__file__).parent / "fixtures"
LISTING = (FIX / "ffmpeg-releases.html").read_text()


def lanes(findings):
    return [(f["lane"], f["component"], f["available"]) for f in findings]


def test_the_fixture_is_the_real_listing_shape():
    """Guard the fixture itself: if it stops carrying .tar.xz releases the tests below
    assert nothing, because the parser would find no versions at all."""
    versions = set(sources.re.findall(r"ffmpeg-([0-9][0-9.]*)\.tar\.xz", LISTING))
    assert len(versions) > 100, len(versions)
    assert "9.0.2" in versions


def test_current_pin_is_silent():
    """The negative control. A source that always reports a finding is a source the
    operator learns to ignore."""
    assert sources.ffmpeg_fork_findings(LISTING, "9.0.2") == []


def test_a_point_release_is_routine():
    """9.0.2 -> 9.0.3 is the version the fork's expiry names: it closes the three CVEs
    NVD says are affected through 9.0.2. The loop may take it."""
    f = sources.ffmpeg_fork_findings(LISTING.replace("9.0.2.tar.xz", "9.0.3.tar.xz"),
                                     "9.0.2")
    assert lanes(f) == [("routine", "ffmpeg-pengyos", "9.0.3")], f
    assert "point release" in f[0]["note"]


def test_a_new_series_is_major():
    """ffmpeg is the Desktop's media path; a series change is a human decision."""
    f = sources.ffmpeg_fork_findings(LISTING, "8.1.3")
    assert lanes(f) == [("major", "ffmpeg-pengyos", "9.0.2")], f
    assert "series" in f[0]["note"]


def test_signatures_and_alternates_are_not_releases():
    """The listing carries .asc/.sig for every release and .tar.gz/.tar.bz2
    alternates. Counting those as versions would report a finding every night.

    The .asc case is the one that actually bites: an unanchored
    `ffmpeg-<ver>.tar.xz` regex matches inside `ffmpeg-<ver>.tar.xz.asc`, so the
    signature is read as a release and the fork reports a bump to a version that
    does not exist. Measured on this parser before the lookahead was added.
    """
    listing = ('<a href="ffmpeg-9.0.2.tar.xz.asc">a</a>\n'
               '<a href="ffmpeg-9.0.2.tar.xz.pq.asc">q</a>\n'
               '<a href="ffmpeg-9.9.9.tar.xz.asc">newer-as-signature</a>\n'
               '<a href="ffmpeg-9.0.2.tar.gz">g</a>\n'
               '<a href="ffmpeg-9.0.2.tar.bz2">b</a>\n'
               '<a href="ffmpeg-9.1.0.tar.gz">newer-series-as-gz</a>')
    assert sources.ffmpeg_fork_findings(listing, "9.0.2") == []


def test_prereleases_are_not_offered_as_a_bump():
    assert sources.ffmpeg_fork_findings('<a href="ffmpeg-9.1.1rc1.tar.xz">r</a>',
                                        "9.0.2") == []


def test_the_pin_is_compared_as_a_version_not_a_string():
    """Lexically "9.1" < "9.0.2" is false, and "9.10" > "9.1": a string compare
    misorders ffmpeg's own versioning and would hide a newer release."""
    assert sources.ffmpeg_fork_findings('<a href="ffmpeg-9.1.tar.xz">x</a>',
                                        "9.0.2")[0]["available"] == "9.1"
    assert sources.ffmpeg_fork_findings('<a href="ffmpeg-9.10.tar.xz">x</a>',
                                        "9.1")[0]["available"] == "9.10"


def test_the_newest_release_is_the_one_offered():
    """Not the first match in the listing: the directory is alphabetical, so
    "8.1.3" comes after "9.0.2" in the file and before it in version order."""
    f = sources.ffmpeg_fork_findings(LISTING, "7.1.5")
    assert lanes(f) == [("major", "ffmpeg-pengyos", "9.0.2")], f


def test_the_recipe_pin_is_what_gets_compared():
    """The observe source reads the fork's own version line, not Buildroot's pin.
    Asked at Buildroot's 6.1.5 it must name 9.0.2 as a new series - that is the whole
    reason the fork exists, and it is the answer a future Buildroot release makes."""
    mk = (Path(__file__).resolve().parents[3] /
          "buildroot-external/package/ffmpeg-pengyos/ffmpeg-pengyos.mk")
    pinned = sources.mk_value(mk.read_text(), "FFMPEG_PENGYOS_VERSION")
    assert pinned == "9.0.2", pinned
    assert sources.ffmpeg_fork_findings(LISTING, pinned) == []
    f = sources.ffmpeg_fork_findings(LISTING, "6.1.5")
    assert lanes(f) == [("major", "ffmpeg-pengyos", "9.0.2")], f
