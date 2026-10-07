"""Tests for the witness-view bundler (implementation task 5).

Covers :mod:`pengyos_witness_bundle.bundle`:

* :func:`bundle` — materialises the canonical layout under ``<build>/.witness/``
  by copying (default) or symlinking each discovered artifact; present kinds are
  bundled, missing kinds are reported in ``skipped``;
* copy mode (files are regular copies) vs symlink mode (destinations are symlinks
  pointing at the absolute source);
* the ``.witness`` directory is created if missing, and bundling is idempotent
  (re-running over existing destinations works);
* a custom ``witness_dir`` is honoured;
* error handling: an unknown mode raises ``ValueError``; a destination that is a
  non-empty directory raises ``OSError``.

Every case is offline and deterministic: fixtures are plain files on disk.
"""

import os

import pytest

from pengyos_witness_bundle.bundle import BundleResult, BundledArtifact, bundle
from pengyos_witness_bundle.discovery import discover


# --- build dirs ------------------------------------------------------------
#
# The build-dir fixtures (good_build / partial_build) are declared in conftest.py
# and shared across the suite; they are used below instead of inline helpers.


# --- bundle: complete ------------------------------------------------------


def test_bundle_bundles_all_four(good_build):
    build = good_build
    res = bundle(build, discover(build))
    assert res.count == 4
    assert res.skipped == ()
    assert [b.kind for b in res.bundled] == [
        "sbom",
        "cyclonedx",
        "sbom_signature",
        "repro_report",
    ]


def test_bundle_writes_conventional_names(good_build):
    build = good_build
    res = bundle(build, discover(build))
    witness = build / ".witness"
    assert (witness / "sbom.spdx.json").is_file()
    assert (witness / "sbom.cyclonedx.json").is_file()
    assert (witness / "sbom.spdx.json.sig").is_file()
    assert (witness / "repro-report.json").is_file()


def test_bundle_source_and_destination(good_build):
    build = good_build
    res = bundle(build, discover(build))
    sbom = next(b for b in res.bundled if b.kind == "sbom")
    assert sbom.source == "images/pengyos-x86_64.spdx.json"
    assert sbom.destination == ".witness/sbom.spdx.json"
    assert sbom.method == "copy"


def test_bundle_result_reports_witness_dir(good_build):
    build = good_build
    res = bundle(build, discover(build))
    assert res.witness_dir == str(build / ".witness")


def test_bundle_copies_content(good_build):
    build = good_build
    res = bundle(build, discover(build), mode="copy")
    witness = build / ".witness"
    # Copies are regular files (not symlinks) whose content matches the source.
    for b in res.bundled:
        dest = witness / b.canonical
        assert os.path.isfile(dest) and not os.path.islink(dest)
        assert dest.read_text() == (build / b.source).read_text()


def test_bundle_default_mode_is_copy(good_build):
    build = good_build
    res = bundle(build, discover(build))
    assert all(b.method == "copy" for b in res.bundled)


def test_bundle_symlink_mode(good_build):
    build = good_build
    res = bundle(build, discover(build), mode="symlink")
    witness = build / ".witness"
    for b in res.bundled:
        dest = witness / b.canonical
        assert os.path.islink(dest)
        assert b.method == "symlink"
        # The symlink resolves to the absolute source path and reads back the
        # source content.
        assert os.path.realpath(dest) == os.path.realpath(build / b.source)
        assert dest.read_text() == (build / b.source).read_text()


def test_bundle_creates_witness_dir_when_missing(good_build):
    build = good_build
    assert not (build / ".witness").exists()
    bundle(build, discover(build))
    assert (build / ".witness").is_dir()


def test_bundle_is_idempotent(good_build):
    # Bundling twice over the existing destinations must not fail (existing
    # files/symlinks are overwritten).
    build = good_build
    first = bundle(build, discover(build))
    second = bundle(build, discover(build))
    assert first.count == second.count == 4
    # Symlink mode over an existing symlink must also be fine.
    link = bundle(build, discover(build), mode="symlink")
    link = bundle(build, discover(build), mode="symlink")
    assert link.count == 4


def test_bundle_partial_reports_skipped(partial_build):
    build = partial_build
    res = bundle(build, discover(build))
    assert res.count == 2
    assert res.skipped == ("cyclonedx", "repro_report")
    witness = build / ".witness"
    assert (witness / "sbom.spdx.json").is_file()
    assert (witness / "sbom.spdx.json.sig").is_file()
    assert not (witness / "sbom.cyclonedx.json").exists()
    assert not (witness / "repro-report.json").exists()


def test_bundle_empty_build_skips_all(empty_build):
    res = bundle(empty_build, discover(empty_build))
    assert res.count == 0
    assert res.skipped == ("sbom", "cyclonedx", "sbom_signature", "repro_report")


def test_bundle_custom_witness_dir(good_build, tmp_path):
    build = good_build
    custom = tmp_path / "custom" / "witness-view"
    res = bundle(build, discover(build), witness_dir=custom)
    assert res.witness_dir == str(custom)
    assert (custom / "sbom.spdx.json").is_file()


def test_bundle_custom_witness_dir_outside_build_uses_abs_path(tmp_path, good_build):
    # good_build writes into tmp_path (the build dir). A witness dir outside the
    # build dir has no build-relative destination, so BundledArtifact.destination
    # falls back to the absolute POSIX path. The outside dir is a sibling of the
    # build dir (under tmp_path's parent), genuinely outside it.
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    res = bundle(good_build, discover(good_build), witness_dir=outside)
    sbom = next(b for b in res.bundled if b.kind == "sbom")
    assert sbom.destination == str(outside / "sbom.spdx.json")
    assert (outside / "sbom.spdx.json").is_file()


# --- bundle: error handling ------------------------------------------------


def test_bundle_unknown_mode_raises():
    # The mode check happens before any filesystem access, so a bogus mode raises
    # even with a null build dir.
    with pytest.raises(ValueError, match="unknown bundle mode"):
        bundle(None, None, mode="nope")


def test_bundle_destination_nonempty_dir_raises(good_build):
    # A pre-existing non-empty directory at the destination must raise, not be
    # clobbered.
    build = good_build
    witness = build / ".witness"
    witness.mkdir()
    (witness / "sbom.spdx.json").mkdir()
    (witness / "sbom.spdx.json" / "nested").mkdir()
    (witness / "sbom.spdx.json" / "nested" / "x").write_text("x")
    with pytest.raises(OSError, match="non-empty directory"):
        bundle(build, discover(build))


def test_bundle_overwrites_existing_regular_file(good_build):
    # A pre-existing regular file at a destination is overwritten (not an error).
    build = good_build
    witness = build / ".witness"
    (witness).mkdir()
    (witness / "sbom.spdx.json").write_text("STALE")
    res = bundle(build, discover(build))
    assert res.count == 4
    # The STALE file is replaced with the source content, not left untouched.
    assert (witness / "sbom.spdx.json").read_text() == (
        build / "images" / "pengyos-x86_64.spdx.json"
    ).read_text()


# --- shape contract --------------------------------------------------------


def test_bundled_artifact_is_frozen():
    art = BundledArtifact("sbom", "sbom.spdx.json", "src", "dst", "copy")
    assert art.method == "copy"
    with pytest.raises(AttributeError):
        art.method = "nope"  # type: ignore[misc]


def test_bundle_result_is_mutable_and_reconstructable():
    res = BundleResult(witness_dir=".witness", bundled=(), skipped=())
    res.witness_dir = "/tmp/build/.witness"
    assert res.count == 0
    assert BundleResult(**vars(res)) == res


def test_bundle_result_default_fields_empty():
    res = BundleResult(witness_dir=".witness")
    assert res.bundled == ()
    assert res.skipped == ()
    assert res.count == 0
