"""Tests for the canonical witness layout as data (implementation task 2).

Covers the :class:`ArtifactSpec` data, the alias matching semantics, the
``iter_files``/``find_matching`` discovery primitives, and the agreement with the
CLI's ``CANONICAL_LAYOUT`` surface.
"""

import json

import pytest

from pengyos_witness_bundle import cli
from pengyos_witness_bundle.layout import (
    CANONICAL_LAYOUT,
    ArtifactSpec,
    canonical_names,
    find_matching,
    iter_files,
    kinds,
    spec_for,
)


# --- the data itself -------------------------------------------------------


def test_layout_has_exactly_four_kinds():
    kinds_seen = {spec.kind for spec in CANONICAL_LAYOUT}
    assert kinds_seen == {"sbom", "cyclonedx", "sbom_signature", "repro_report"}
    assert len(CANONICAL_LAYOUT) == 4


def test_canonical_names_match_the_contract():
    assert canonical_names() == {
        "sbom": "sbom.spdx.json",
        "cyclonedx": "sbom.cyclonedx.json",
        "sbom_signature": "sbom.spdx.json.sig",
        "repro_report": "repro-report.json",
    }


def test_every_spec_has_aliases():
    for spec in CANONICAL_LAYOUT:
        assert isinstance(spec.aliases, tuple)
        assert len(spec.aliases) >= 1
        assert all(pat for pat in spec.aliases)  # no empty patterns


def test_signature_canonical_ends_with_sig():
    assert spec_for("sbom_signature").canonical.endswith(".sig")


def test_sbom_canonical_is_spdx():
    assert spec_for("sbom").canonical == "sbom.spdx.json"


def test_specs_are_frozen_dataclasses():
    spec = spec_for("sbom")
    with pytest.raises(AttributeError):
        spec.canonical = "nope"  # type: ignore[misc]


# --- spec_for --------------------------------------------------------------


def test_spec_for_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown artifact kind"):
        spec_for("not-a-kind")


def test_kinds_returns_all_four():
    assert set(kinds()) == {"sbom", "cyclonedx", "sbom_signature", "repro_report"}


# --- ArtifactSpec.matches --------------------------------------------------


@pytest.mark.parametrize(
    "rel_path, pattern, expected",
    [
        # sbom aliases
        ("images/pengyos-x86_64.spdx.json", "images/*.spdx.json", True),
        ("sbom.spdx.json", "sbom.spdx.json", True),
        ("other.spdx.json", "*.spdx.json", True),
        # a signature is NOT an sbom
        ("images/pengyos-x86_64.spdx.json.sig", "images/*.spdx.json", False),
        # cyclonedx aliases
        ("images/pengyos-x86_64.cyclonedx.json", "images/*.cyclonedx.json", True),
        # the sbom signature matches its own alias
        ("images/pengyos-x86_64.spdx.json.sig", "images/*.spdx.json.sig", True),
        ("sbom.spdx.json.sig", "sbom.spdx.json.sig", True),
        # repro report
        ("repro-report.txt", "repro-report.txt", True),
        ("images/pengyos-x86_64.repro.txt", "images/*.repro.txt", True),
        # aliases for what pengyos-build / pengyos-repro-report actually emit
        ("images/pengyos-x86_64.cdx.json", "images/*.cdx.json", True),
        ("images/repro-report.json", "images/repro-report.json", True),
        ("repro-report.json", "repro-report.json", True),
    ],
)
def test_match_against_pattern(rel_path, pattern, expected):
    assert ArtifactSpec("x", "x", (pattern,)).matches(rel_path) is expected


def test_sbom_does_not_match_signature_file():
    # The disjoint-format ordering must hold: an images/*.spdx.json alias must
    # never pick up the sibling .sig file.
    sbom = spec_for("sbom")
    assert sbom.matches("images/pengyos-x86_64.spdx.json") is True
    assert sbom.matches("images/pengyos-x86_64.spdx.json.sig") is False


def test_matches_is_posix_normalized():
    # Backslashes are normalised to forward slashes before matching.
    assert spec_for("sbom").matches(r"images\pengyos-x86_64.spdx.json") is True


# --- iter_files ------------------------------------------------------------


def test_iter_files_yields_sorted_relative_posix(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.spdx.json").write_text("a")
    (tmp_path / "images" / "b.cyclonedx.json").write_text("b")
    (tmp_path / "top.txt").write_text("t")

    files = list(iter_files(tmp_path))
    assert files == [
        "images/a.spdx.json",
        "images/b.cyclonedx.json",
        "top.txt",
    ]


def test_iter_files_skips_directories(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "file.txt").write_text("x")
    assert list(iter_files(tmp_path)) == ["file.txt"]


def test_iter_files_empty_dir(tmp_path):
    assert list(iter_files(tmp_path)) == []


def test_iter_files_none_for_missing_dir(tmp_path):
    # A missing directory yields nothing rather than raising.
    assert list(iter_files(tmp_path / "does-not-exist")) == []


def test_iter_files_file_as_root_yields_nothing(tmp_path):
    f = tmp_path / "only.txt"
    f.write_text("x")
    assert list(iter_files(f)) == []


# --- find_matching ---------------------------------------------------------


def test_find_matching_sbom_from_images_alias(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pengyos-x86_64.spdx.json").write_text("{}")
    assert find_matching(tmp_path, "sbom") == "images/pengyos-x86_64.spdx.json"


def test_find_matching_cyclonedx_from_images_alias(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pengyos-x86_64.cyclonedx.json").write_text("{}")
    assert find_matching(tmp_path, "cyclonedx") == "images/pengyos-x86_64.cyclonedx.json"


def test_find_matching_signature(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pengyos-x86_64.spdx.json.sig").write_text("sig")
    assert find_matching(tmp_path, "sbom_signature") == (
        "images/pengyos-x86_64.spdx.json.sig"
    )


def test_find_matching_repro_report(tmp_path):
    (tmp_path / "repro-report.txt").write_text("repro")
    assert find_matching(tmp_path, "repro_report") == "repro-report.txt"


def test_find_matching_top_level_spdx_alias(tmp_path):
    # Falls back to the bare *.spdx.json alias when not under images/.
    (tmp_path / "sbom.spdx.json").write_text("{}")
    assert find_matching(tmp_path, "sbom") == "sbom.spdx.json"


def test_find_matching_unknown_kind_raises(tmp_path):
    with pytest.raises(ValueError):
        find_matching(tmp_path, "nope")


def test_find_matching_none_when_absent(tmp_path):
    assert find_matching(tmp_path, "sbom") is None


def test_find_matching_does_not_pick_up_sibling_sig_for_sbom(tmp_path):
    # Only a signature is present (no real SBOM): sbom must stay None and the
    # signature must still be findable as itself.
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pengyos-x86_64.spdx.json.sig").write_text("sig")
    assert find_matching(tmp_path, "sbom") is None
    assert find_matching(tmp_path, "sbom_signature") == (
        "images/pengyos-x86_64.spdx.json.sig"
    )


# --- find_matching: most-specific-alias tie-break --------------------------
#
# `find_matching` used to iterate files in sorted path order and return the first
# that matched *any* alias. A broad `*.spdx.json` therefore grabbed a spurious
# host-package SPDX doc (e.g. `host-python3-.../Misc/externals.spdx.json`, which
# sorts before `images/`) and shadowed the real `images/<target>.spdx.json`. The
# fix prefers the file matching the lowest-index (most specific) alias.


def test_find_matching_prefers_images_over_broad_alias(tmp_path):
    # The spurious host-package SPDX doc sorts BEFORE images/ (h < i) and would
    # win the old "first file matching any alias" scan. The real SBOM must win.
    (tmp_path / "host-python3-3.14.7" / "Misc").mkdir(parents=True)
    (
        tmp_path / "host-python3-3.14.7" / "Misc" / "externals.spdx.json"
    ).write_text("{}")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pengyos-core.spdx.json").write_text("{}")

    assert find_matching(tmp_path, "sbom") == "images/pengyos-core.spdx.json"


def test_find_matching_prefers_images_alias_over_top_level_alias(tmp_path):
    # Both a top-level `sbom.spdx.json` (matched by the 2nd alias) and an
    # `images/<target>.spdx.json` (matched by the 1st, more specific alias) exist;
    # the more specific `images/` match wins.
    (tmp_path / "sbom.spdx.json").write_text("{}")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pengyos-core.spdx.json").write_text("{}")

    assert find_matching(tmp_path, "sbom") == "images/pengyos-core.spdx.json"


def test_find_matching_prefers_lowest_index_even_when_broad_sorts_first(tmp_path):
    # A broad `*.spdx.json` match that sorts before the specific `images/` match
    # must still lose. Here the spurious doc lives at a deep path that sorts early.
    (tmp_path / "000-host").mkdir()
    (tmp_path / "000-host" / "pkg.spdx.json").write_text("{}")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pengyos-core.spdx.json").write_text("{}")

    assert find_matching(tmp_path, "sbom") == "images/pengyos-core.spdx.json"


def test_find_matching_same_lowest_index_breaks_tie_by_sorted_path(tmp_path):
    # Two files match the SAME lowest-index alias: the earliest in sorted path
    # order wins (deterministic). Both are under images/.
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "b.spdx.json").write_text("{}")
    (tmp_path / "images" / "a.spdx.json").write_text("{}")

    assert find_matching(tmp_path, "sbom") == "images/a.spdx.json"


def test_find_matching_specific_alias_beats_none_in_between(tmp_path):
    # A file that only matches the broad alias (index 2) loses to one that matches
    # the middle alias (index 1) even though the broad one sorts first.
    (tmp_path / "zzz-other.spdx.json").write_text("{}")  # broad alias only
    (tmp_path / "sbom.spdx.json").write_text("{}")  # middle alias, sorts first

    assert find_matching(tmp_path, "sbom") == "sbom.spdx.json"


def test_find_matching_lowest_alias_index_helper(tmp_path):
    from pengyos_witness_bundle.layout import _lowest_matching_alias_index

    sbom = spec_for("sbom")
    # images/ -> index 0 (most specific)
    assert _lowest_matching_alias_index(sbom, "images/pengyos-x86_64.spdx.json") == 0
    # top-level sbom.spdx.json -> index 1
    assert _lowest_matching_alias_index(sbom, "sbom.spdx.json") == 1
    # a bare other.spdx.json -> index 2 (the broad fallback)
    assert _lowest_matching_alias_index(sbom, "other.spdx.json") == 2
    # a signature file matches no sbom alias
    assert (
        _lowest_matching_alias_index(sbom, "images/pengyos-x86_64.spdx.json.sig")
        is None
    )
    # a totally unrelated file matches nothing
    assert _lowest_matching_alias_index(sbom, "foo/bar.txt") is None


# --- CLI surface agreement --------------------------------------------------


def test_cli_canonical_layout_agrees_with_layout_data():
    assert cli.CANONICAL_LAYOUT == canonical_names()


def test_cli_canonical_layout_is_json_serializable():
    # The JSON the `layout --json` emits must equal the canonical dict.
    assert json.loads(json.dumps(cli.CANONICAL_LAYOUT)) == cli.CANONICAL_LAYOUT
