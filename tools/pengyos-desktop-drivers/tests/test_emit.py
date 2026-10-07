"""emit tests.

These pin the deterministic contract of :mod:`pengyos_desktop_drivers.emit`:

* :func:`render_fragment` -- vendored header + sorted, deduped ``CONFIG_X=y``
  lines, single trailing newline.
* :func:`render_firmware_list` -- upstream names filtered to the curated
  prefixes, sorted, newline-terminated.
* :func:`emit_desktop_profile` -- writes both artifacts under ``--out``, returns a
  report with path / bytes / sha256, and is byte-for-byte deterministic.

The golden hashes pin the exact bytes so a drift in the profile (or a broken
render) fails loudly. They are only meaningful because the profile is grounded in
``test_profile``; together they prove the emitted Desktop profile is stable.
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from pengyos_desktop_drivers import data, emit, profile

# Golden hashes for the current, grounded Desktop profile. Regenerate by running
# emit and copying the reported sha256 values if the profile legitimately changes.
GOLDEN_FRAGMENT_SHA256 = "fc0d75e96d186f8715f290ec7b381783e0a90612f9b64be8187f259315d975c2"
GOLDEN_FIRMWARE_SHA256 = "87e1d8df5de62a58f6159b5b158d4198fc3161b66851e6276921d72bdd849feb"


# --------------------------------------------------------------------------- #
# render_fragment
# --------------------------------------------------------------------------- #
def test_render_fragment_prepends_vendor_header() -> None:
    text = emit.render_fragment(["CONFIG_DRM", "CONFIG_INPUT"])
    assert text.startswith(profile.FRAGMENT_HEADER)


def test_render_fragment_writes_sorted_config_lines() -> None:
    text = emit.render_fragment(["CONFIG_ZEBRA", "CONFIG_ALPHA", "CONFIG_MID"])
    config_lines = [ln for ln in text.splitlines() if ln.startswith("CONFIG_")]
    assert config_lines == [
        "CONFIG_ALPHA=y",
        "CONFIG_MID=y",
        "CONFIG_ZEBRA=y",
    ]


def test_render_fragment_dedupes_and_trailing_newline() -> None:
    text = emit.render_fragment(["CONFIG_A", "CONFIG_A", "CONFIG_B"])
    # Deduped (set semantics) and exactly one trailing newline.
    assert text.count("CONFIG_A=y") == 1
    assert text.endswith("CONFIG_B=y\n")
    assert not text.endswith("\n\n")


def test_render_fragment_symbol_line_format() -> None:
    text = emit.render_fragment(["CONFIG_DRM"])
    assert "CONFIG_DRM=y\n" in text


# --------------------------------------------------------------------------- #
# render_firmware_list
# --------------------------------------------------------------------------- #
def test_render_firmware_list_filters_and_sorts() -> None:
    names = ["zzz/z.bin", "aaa/a.bin", "mmm/m.bin", "other/x.bin"]
    text = emit.render_firmware_list(names, ["aaa/", "mmm/"])
    assert text == "aaa/a.bin\nmmm/m.bin\n"


def test_render_firmware_list_prefix_must_match_start() -> None:
    # A prefix only matches at the start of a path, not as a substring.
    names = ["i915/foo.bin", "noti915/bar.bin", "i915x/baz.bin"]
    text = emit.render_firmware_list(names, ["i915/"])
    assert text == "i915/foo.bin\n"


def test_render_firmware_list_empty_when_no_match() -> None:
    text = emit.render_firmware_list(["a/a.bin"], ["zzz/"])
    assert text == "\n"


def test_render_firmware_list_dedupes() -> None:
    text = emit.render_firmware_list(["x/x.bin", "x/x.bin"], ["x/"])
    assert text == "x/x.bin\n"


# --------------------------------------------------------------------------- #
# emit_desktop_profile
# --------------------------------------------------------------------------- #
def test_emit_writes_both_artifacts(tmp_path: pathlib.Path) -> None:
    report = emit.emit_desktop_profile(tmp_path)
    frag = emit.FRAGMENT_FILENAME
    fw = emit.FIRMWARE_FILENAME
    assert (tmp_path / frag).is_file(), "linux.fragment must be written"
    assert (tmp_path / fw).is_file(), "firmware.list must be written"
    assert report["fragment"]["path"] == str(tmp_path / frag)
    assert report["firmware"]["path"] == str(tmp_path / fw)


def test_emit_report_has_path_bytes_sha256(tmp_path: pathlib.Path) -> None:
    report = emit.emit_desktop_profile(tmp_path)
    # fragment reports path/bytes/sha256; firmware reports path/lines/sha256.
    assert set(report["fragment"]) == {"path", "bytes", "sha256"}
    assert set(report["firmware"]) == {"path", "lines", "sha256"}
    assert isinstance(report["fragment"]["sha256"], str)
    assert len(report["fragment"]["sha256"]) == 64  # hex SHA-256
    # The reported byte length matches the file on disk.
    frag_bytes = (tmp_path / emit.FRAGMENT_FILENAME).read_bytes()
    assert report["fragment"]["bytes"] == len(frag_bytes)


def test_emit_report_firmware_lines_match_file(tmp_path: pathlib.Path) -> None:
    report = emit.emit_desktop_profile(tmp_path)
    fw_text = (tmp_path / emit.FIRMWARE_FILENAME).read_text(encoding="utf-8")
    assert report["firmware"]["lines"] == fw_text.count("\n")
    assert report["firmware"]["sha256"] == hashlib.sha256(
        fw_text.encode("utf-8")
    ).hexdigest()


def test_emit_creates_missing_out_dir(tmp_path: pathlib.Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    report = emit.emit_desktop_profile(nested)
    assert (nested / emit.FRAGMENT_FILENAME).is_file()
    assert (nested / emit.FIRMWARE_FILENAME).is_file()
    _ = report


def test_emit_fragment_contains_all_profile_symbols() -> None:
    # Render directly (no disk) and assert the profile's symbols are present.
    frag_text = emit.render_fragment(profile.all_symbols())
    for symbol in profile.all_symbols():
        assert f"{symbol}=y" in frag_text, f"{symbol} missing from fragment"


def test_emit_fragment_has_no_vendor_acceleration_symbols() -> None:
    # Note: the vendored header literally says "no NVIDIA", so the *substring*
    # "NVIDIA" is expected in the text. What must be absent are actual enabled
    # proprietary/accel symbols -- i.e. a CONFIG_ line turning them on.
    frag_text = emit.render_fragment(profile.all_symbols())
    assert "NVIDIA=y" not in frag_text
    assert "CONFIG_NVIDIA" not in frag_text
    assert "CONFIG_DRM_ACCEL=y" not in frag_text
    assert "CONFIG_DRM_NOUVEAU=y" not in frag_text
    # And no vendor GPU driver is enabled directly.
    for symbol in profile.all_symbols():
        assert "NVIDIA" not in symbol and "NOUVEAU" not in symbol


def test_emit_firmware_is_subset_of_upstream_oracle() -> None:
    upstream = set(data.load_firmware_names())
    fw_text = emit.render_firmware_list(
        data.load_firmware_names(), profile.firmware_prefixes()
    )
    for line in fw_text.splitlines():
        assert line in upstream, f"{line} is not an upstream firmware name"


def test_emit_firmware_covers_every_curated_prefix() -> None:
    upstream = data.load_firmware_names()
    fw_text = emit.render_firmware_list(upstream, profile.firmware_prefixes())
    selected = set(fw_text.splitlines())
    for prefix in profile.firmware_prefixes():
        assert any(n.startswith(prefix) for n in selected), (
            f"emitted firmware is missing entries under {prefix}"
        )


def test_emit_fragment_line_count_matches_unique_symbols() -> None:
    frag_text = emit.render_fragment(profile.all_symbols())
    header_lines = len(profile.FRAGMENT_HEADER.splitlines())
    assert frag_text.count("\n") == header_lines + len(set(profile.all_symbols()))


def test_emit_is_deterministic(tmp_path_factory: pytest.TempPathFactory) -> None:
    dir_a = tmp_path_factory.mktemp("a")
    dir_b = tmp_path_factory.mktemp("b")
    r1 = emit.emit_desktop_profile(dir_a)
    r2 = emit.emit_desktop_profile(dir_b)
    assert r1["fragment"]["sha256"] == r2["fragment"]["sha256"]
    assert r1["firmware"]["sha256"] == r2["firmware"]["sha256"]


def test_emit_golden_fragment_hash(tmp_path: pathlib.Path) -> None:
    report = emit.emit_desktop_profile(tmp_path)
    assert report["fragment"]["sha256"] == GOLDEN_FRAGMENT_SHA256


def test_emit_golden_firmware_hash(tmp_path: pathlib.Path) -> None:
    report = emit.emit_desktop_profile(tmp_path)
    assert report["firmware"]["sha256"] == GOLDEN_FIRMWARE_SHA256


def test_emit_firmware_line_count_is_current(tmp_path: pathlib.Path) -> None:
    report = emit.emit_desktop_profile(tmp_path)
    # 126 i915 + 677 amdgpu + 247 radeon + 194 intel/iwlwifi + 38 rtlwifi + 34
    # rtl_bt = 1316 curated entries for the current upstream oracle.
    assert report["firmware"]["lines"] == 1316
