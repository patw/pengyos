"""Tests for artifact discovery (implementation task 3).

Covers :func:`discover`, the :class:`DiscoveryResult` / :class:`DiscoveredArtifact`
surfaces, the missing-reporting contract, and the ``missing_kinds`` /
``present_kinds`` conveniences. Every case is a normal discovery outcome — a
missing artifact never raises a traceback.
"""

import pytest

from pengyos_witness_bundle.discovery import (
    DiscoveryResult,
    discover,
    missing_kinds,
    present_kinds,
)


# --- a "good" minimal build dir --------------------------------------------
#
# The good_build fixture is declared in conftest.py and shared across the
# suite; it is used below instead of an inline helper.


# --- discover: complete -----------------------------------------------------


def test_discover_complete_on_good_build(good_build):
    result = discover(good_build)
    assert result.complete is True
    assert result.present == ("sbom", "cyclonedx", "sbom_signature", "repro_report")
    assert result.missing == ()
    assert result.missing_message() == ""


def test_discover_reports_build_dir_as_posix(good_build, tmp_path):
    result = discover(good_build)
    assert result.build_dir == str(tmp_path)


def test_discover_artifacts_in_canonical_order(good_build):
    result = discover(good_build)
    assert [a.kind for a in result.artifacts] == [
        "sbom",
        "cyclonedx",
        "sbom_signature",
        "repro_report",
    ]
    assert len(result.artifacts) == 4


def test_discover_finds_sbom_from_images_alias(good_build):
    result = discover(good_build)
    sbom = next(a for a in result.artifacts if a.kind == "sbom")
    assert sbom.found is True
    assert sbom.path == "images/pengyos-x86_64.spdx.json"
    assert sbom.canonical == "sbom.spdx.json"
    assert "images/*.spdx.json" in sbom.aliases


def test_discover_finds_cyclonedx(good_build):
    result = discover(good_build)
    cx = next(a for a in result.artifacts if a.kind == "cyclonedx")
    assert cx.found is True
    assert cx.path == "images/pengyos-x86_64.cyclonedx.json"


def test_discover_finds_signature(good_build):
    result = discover(good_build)
    sig = next(a for a in result.artifacts if a.kind == "sbom_signature")
    assert sig.found is True
    assert sig.path == "images/pengyos-x86_64.spdx.json.sig"
    assert sig.canonical == "sbom.spdx.json.sig"


def test_discover_finds_repro_report(good_build):
    result = discover(good_build)
    repro = next(a for a in result.artifacts if a.kind == "repro_report")
    assert repro.found is True
    assert repro.path == "repro-report.txt"


# --- discover: partial ------------------------------------------------------


def test_discover_partial_reports_missing_only(tmp_path):
    # Only the SBOM and its signature exist.
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pengyos-x86_64.spdx.json").write_text("{}")
    (tmp_path / "images" / "pengyos-x86_64.spdx.json.sig").write_text("sig")

    result = discover(tmp_path)
    assert result.complete is False
    assert result.present == ("sbom", "sbom_signature")
    assert result.missing == ("cyclonedx", "repro_report")


def test_discover_missing_sbom_but_present_signature(tmp_path):
    # A bare signature (no real SBOM): sbom stays missing, signature present.
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pengyos-x86_64.spdx.json.sig").write_text("sig")

    result = discover(tmp_path)
    assert result.missing == ("sbom", "cyclonedx", "repro_report")
    assert result.present == ("sbom_signature",)
    sig = next(a for a in result.artifacts if a.kind == "sbom_signature")
    assert sig.found is True


def test_discover_missing_message_lists_expected_names(tmp_path):
    result = discover(tmp_path)  # empty dir -> everything missing
    assert result.complete is False
    msg = result.missing_message()
    assert "missing artifacts in" in msg
    assert "sbom.spdx.json" in msg
    assert "sbom.cyclonedx.json" in msg
    assert "sbom.spdx.json.sig" in msg
    assert "repro-report.json" in msg


def test_discover_missing_message_empty_when_complete(good_build):
    assert discover(good_build).missing_message() == ""


# --- discover: empty / missing / file-as-root -------------------------------


def test_discover_empty_dir_all_missing(tmp_path):
    result = discover(tmp_path)
    assert result.complete is False
    assert result.present == ()
    assert result.missing == ("sbom", "cyclonedx", "sbom_signature", "repro_report")
    assert all(a.found is False for a in result.artifacts)


def test_discover_missing_dir_does_not_raise(tmp_path):
    # A build dir that does not exist at all is a clean, complete-empty result.
    result = discover(tmp_path / "does-not-exist")
    assert result.complete is False
    assert result.present == ()
    assert result.build_dir == str(tmp_path / "does-not-exist")


def test_discover_file_as_root_does_not_raise(tmp_path):
    f = tmp_path / "only.txt"
    f.write_text("x")
    result = discover(f)
    assert result.complete is False
    assert result.present == ()
    assert result.missing == ("sbom", "cyclonedx", "sbom_signature", "repro_report")


def test_discover_accepts_pathlib_and_str(good_build):
    import pathlib

    good = good_build
    assert discover(good).complete is True
    assert discover(str(good)).complete is True
    assert discover(pathlib.Path(good)).complete is True


# --- conveniences -----------------------------------------------------------


def test_missing_kinds(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pengyos-x86_64.spdx.json").write_text("{}")
    assert missing_kinds(tmp_path) == ("cyclonedx", "sbom_signature", "repro_report")


def test_present_kinds(good_build, tmp_path):
    # good_build (fixture) has already written all four artifacts into tmp_path.
    assert present_kinds(tmp_path) == (
        "sbom",
        "cyclonedx",
        "sbom_signature",
        "repro_report",
    )


# --- result shape contract --------------------------------------------------


def test_discovery_result_complete_reflects_missing():
    complete = DiscoveryResult(build_dir=".", present=("sbom",), missing=())
    assert complete.complete is True
    incomplete = DiscoveryResult(build_dir=".", present=(), missing=("sbom",))
    assert incomplete.complete is False


def test_discovery_result_is_mutable_and_reconstructable():
    # DiscoveryResult is a plain (mutable) dataclass, so it round-trips.
    result = DiscoveryResult(build_dir=".", present=("sbom",), missing=())
    result.build_dir = "/tmp/build"
    assert result.build_dir == "/tmp/build"
    assert DiscoveryResult(**vars(result)) == result


def test_discovered_artifact_is_frozen():
    from pengyos_witness_bundle.discovery import DiscoveredArtifact

    art = DiscoveredArtifact("sbom", "sbom.spdx.json", True, "x", ("x",))
    assert art.found is True
    with pytest.raises(AttributeError):
        art.found = False  # type: ignore[misc]


def test_discover_partial_present_and_missing_are_canonical_order(tmp_path):
    # Present and missing must follow canonical order even when interleaved.
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pengyos-x86_64.spdx.json").write_text("{}")
    (tmp_path / "images" / "pengyos-x86_64.cyclonedx.json").write_text("{}")
    (tmp_path / "repro-report.txt").write_text("repro")

    result = discover(tmp_path)
    assert result.present == ("sbom", "cyclonedx", "repro_report")
    assert result.missing == ("sbom_signature",)


# --- discovery: most-specific-alias tie-break ------------------------------
#
# The real M2 build has a spurious host-package SPDX doc (e.g.
# `host-python3-.../Misc/externals.spdx.json`) that sorts before `images/` and
# would shadow the real `images/<target>.spdx.json` under the old "first file
# matching any alias" scan. Discovery must prefer the real SBOM.


def _build_with_spurious_host_spdx(good_build):
    """A good M2-style build PLUS a spurious host-package SPDX doc that sorts first."""
    (good_build / "host-python3-3.14.7" / "Misc").mkdir(parents=True)
    (
        good_build / "host-python3-3.14.7" / "Misc" / "externals.spdx.json"
    ).write_text("{}")
    return good_build


def test_discover_rejects_spurious_host_spdx(good_build):
    build = _build_with_spurious_host_spdx(good_build)
    result = discover(build)
    assert result.complete is True
    sbom = next(a for a in result.artifacts if a.kind == "sbom")
    # The real SBOM wins over the spurious host-package SPDX doc.
    assert sbom.path == "images/pengyos-x86_64.spdx.json"
    assert "externals.spdx.json" not in (sbom.path or "")


def test_discover_complete_even_with_host_spdx_present(good_build):
    # The spurious doc does not break completeness: all four kinds still resolve.
    build = _build_with_spurious_host_spdx(good_build)
    assert discover(build).complete is True


def test_discover_does_not_shadow_signature_from_host_spdx(good_build, tmp_path):
    # A host-package SPDX doc must not be mistaken for the SBOM signature either.
    _build_with_spurious_host_spdx(good_build)
    result = discover(tmp_path)
    sig = next(a for a in result.artifacts if a.kind == "sbom_signature")
    assert sig.path == "images/pengyos-x86_64.spdx.json.sig"
