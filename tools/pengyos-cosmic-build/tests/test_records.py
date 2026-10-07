"""Tests for the pengyos-cosmic-build wiring data model (:mod:`records`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_cosmic_build.records import (
    VENDORED_SITE_PLACEHOLDER,
    VendorRecord,
    VendorRecordError,
    load_record,
    load_records,
    read_sha256,
    symbol_for,
    write_record,
)

GOOD_SHA = "a" * 64


def _record(**overrides) -> VendorRecord:
    """A valid VendorRecord, overridable per-test."""
    kwargs = {
        "name": "cosmic-portal",
        "version": "1.0.0",
        "tarball": "cosmic-portal-1.0.0.tar.gz",
        "sha256": GOOD_SHA,
        "site": "file:///home/user/dl",
    }
    kwargs.update(overrides)
    return VendorRecord(**kwargs)


# --- symbol_for -------------------------------------------------------------


def test_symbol_for_basic():
    assert symbol_for("cosmic-portal") == "BR2_PACKAGE_COSMIC_PORTAL"


def test_symbol_for_dashes_and_multiword():
    assert symbol_for("xdg-desktop-portal") == "BR2_PACKAGE_XDG_DESKTOP_PORTAL"


def test_symbol_for_single_word():
    assert symbol_for("greetd") == "BR2_PACKAGE_GREETD"


def test_symbol_for_matches_defconfig_convention():
    # The defconfig (buildroot-external/configs/*.defconfig) references these
    # exact symbols; the transform must produce them.
    defconfig = Path(__file__).resolve().parent.parent.parent.parent / (
        "buildroot-external/configs/pengyos_desktop_defconfig"
    )
    text = defconfig.read_text(encoding="utf-8")
    for pkg in ("cosmic-portal", "xdg-desktop-portal"):
        assert symbol_for(pkg) in text


def test_symbol_for_matches_engine_recipe_symbol():
    # The data model must agree with the engine's own Kconfig transform, so the
    # recipe and the defconfig can never disagree about a package's symbol.
    from pengyos_cosmic_pkg.recipe import recipe_symbol

    for pkg in ("cosmic-portal", "xdg-desktop-portal", "greetd", "cosmic-comp"):
        assert symbol_for(pkg) == recipe_symbol(pkg)


# --- VendorRecord.validate --------------------------------------------------


def test_validate_ok():
    _record().validate()  # must not raise


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "1bad"),  # must start with a letter (Kconfig symbol)
        ("name", "HasSpace"),
        ("version", ""),
        ("tarball", ""),
        ("site", ""),
    ],
)
def test_validate_rejects_malformed(field, value):
    with pytest.raises(VendorRecordError):
        _record(**{field: value}).validate()


def test_validate_rejects_bad_sha256():
    with pytest.raises(VendorRecordError):
        _record(sha256="deadbeef").validate()


def test_validate_rejects_placeholder_site():
    with pytest.raises(VendorRecordError):
        _record(site=VENDORED_SITE_PLACEHOLDER).validate()



# --- read_sha256 ------------------------------------------------------------


def test_read_sha256_basic():
    assert read_sha256(f"sha256  {GOOD_SHA}  cosmic-portal-1.0.0.tar.gz") == GOOD_SHA


def test_read_sha256_skips_comments_and_blanks():
    body = f"# comment\n\nsha256  {GOOD_SHA}  cosmic-portal-1.0.0.tar.gz\n"
    assert read_sha256(body) == GOOD_SHA


def test_read_sha256_bad_shape():
    with pytest.raises(VendorRecordError):
        read_sha256("md5  abc cosmic-portal.tar.gz")


def test_read_sha256_missing():
    with pytest.raises(VendorRecordError):
        read_sha256("# only a comment\n")


# --- write / load round-trip ------------------------------------------------


def test_write_and_load_round_trip(tmp_path: Path):
    path = tmp_path / "cosmic-portal.json"
    record = _record()
    write_record(record, path)
    loaded = load_record(path)
    assert loaded == record


def test_write_is_deterministic(tmp_path: Path):
    path = tmp_path / "cosmic-portal.json"
    write_record(_record(), path)
    first = path.read_text(encoding="utf-8")
    write_record(_record(), path)
    assert path.read_text(encoding="utf-8") == first


def test_load_records_sorted_by_name(tmp_path: Path):
    write_record(_record(name="zeta"), tmp_path / "zeta.json")
    write_record(_record(name="alpha"), tmp_path / "alpha.json")
    names = [r.name for r in load_records(tmp_path)]
    assert names == ["alpha", "zeta"]


def test_load_records_reports_malformed(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"name": "cosmic-portal", "sha256": "nope"}', encoding="utf-8")
    with pytest.raises(VendorRecordError):
        list(load_records(tmp_path))


def test_load_record_invalid_json(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(VendorRecordError):
        load_record(bad)
