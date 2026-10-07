"""lint tests.

These pin the four-rule contract of :mod:`pengyos_desktop_drivers.lint`:

1. **Symbol existence** -- every enabled ``CONFIG_*`` symbol in the fragment
   exists in the vendored 2026.08 symbol list.
2. **No vendor/proprietary acceleration** -- the fragment contains none of the
   forbidden symbols (NVIDIA, AMDGPU/RADEON vendor-accel toggles, ...).
3. **Firmware subset** -- every line in the firmware list is an upstream
   ``linux-firmware`` name **and** the list is complete (every curated prefix is
   present, so a list missing the wifi stack is rejected).
4. **Class coverage** -- the fragment enables at least one display, one input,
   and one wifi driver.

The tests also pin the pure helpers (:func:`_enabled_symbols`,
:func:`_fragment_class_symbols`, the four ``_check_*`` functions), the
emit->lint composition (the generic Desktop profile is lint-clean), and the
CLI exit-code contract for both clean and dirty profiles.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from pengyos_desktop_drivers import cli, data, emit, lint, profile

# Imported as a module so the golden hashes pinned in test_emit are shared, not
# duplicated. The completeness check must not change what the clean profile
# emits, so these hashes are the pin for that.
import test_emit as emit_test  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Constants / contract
# --------------------------------------------------------------------------- #
def test_rule_ids_are_distinct_nonempty_strings() -> None:
    rule_ids = [
        lint.RULE_SYMBOL_EXISTS,
        lint.RULE_NO_VENDOR_ACCEL,
        lint.RULE_FIRMWARE_SUBSET,
        lint.RULE_CLASS_COVERAGE,
    ]
    assert len(rule_ids) == len(set(rule_ids)) == 4
    assert all(isinstance(r, str) and r for r in rule_ids)


def test_required_classes_are_display_input_wifi() -> None:
    assert lint.REQUIRED_CLASSES == ["display", "input", "wifi"]


def test_forbidden_symbols_include_the_propritetary_stack() -> None:
    # The canonical proprietary vendor-accel symbols must be forbidden.
    for forbidden in ("CONFIG_NVIDIA", "CONFIG_DRM_NOUVEAU", "CONFIG_DRM_ACCEL"):
        assert forbidden in lint.FORBIDDEN_SYMBOLS


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_read_lines_strips_and_drops_blank() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "x.txt"
        p.write_text("  CONFIG_A=y  \n\n   \nCONFIG_B=m\n", encoding="utf-8")
        assert lint._read_lines(p) == ["CONFIG_A=y", "CONFIG_B=m"]


def test_enabled_symbols_parses_y_and_m_ignores_comments() -> None:
    lines = [
        "# SPDX header",
        "CONFIG_DRM=y",
        "CONFIG_INPUT=m",
        "# CONFIG_NOUVEAU is not set",
        "# CONFIG_NVIDIA=y",  # commented-out, must be ignored
        "prose line",
    ]
    enabled = lint._enabled_symbols(lines)
    assert enabled == {"CONFIG_DRM", "CONFIG_INPUT"}


def test_enabled_symbols_empty_for_header_only() -> None:
    # The vendored fragment header is pure prose; it enables nothing.
    header_lines = [ln for ln in profile.FRAGMENT_HEADER.splitlines() if ln]
    assert lint._enabled_symbols(header_lines) == set()


def test_fragment_class_symbols_maps_enabled() -> None:
    # Only CONFIG_DRM + CONFIG_IWLWIFI are enabled; input is absent.
    symbols = {"CONFIG_DRM", "CONFIG_IWLWIFI"}
    by_class = lint._fragment_class_symbols(symbols, ["display", "input", "wifi"])
    assert "CONFIG_DRM" in by_class["display"]
    assert "CONFIG_IWLWIFI" in by_class["wifi"]
    assert by_class["input"] == set()


def test_check_symbol_existence_clean_and_dirty() -> None:
    symbols = set(data.load_symbols())
    ok, reasons = lint._check_symbol_existence(["CONFIG_DRM=y"], symbols)
    assert ok and reasons == []

    ok, reasons = lint._check_symbol_existence(["CONFIG_FAKE=y"], symbols)
    assert not ok
    assert "CONFIG_FAKE" in reasons[0]


def test_check_no_vendor_accel_clean_and_dirty() -> None:
    symbols = set(data.load_symbols())
    ok, reasons = lint._check_no_vendor_accel(["CONFIG_DRM=y"], symbols)
    assert ok and reasons == []

    ok, reasons = lint._check_no_vendor_accel(["CONFIG_NVIDIA=y"], symbols)
    assert not ok
    assert "CONFIG_NVIDIA" in reasons[0]


def test_check_no_vendor_accel_reports_unreachable_guard() -> None:
    # A forbidden symbol absent from the 2026.08 list still surfaces in the
    # message (as a documented guard), even though it can never fire here.
    ok, reasons = lint._check_no_vendor_accel(["CONFIG_NVIDIA=y"], set())
    assert not ok
    assert "CONFIG_NVIDIA" in reasons[0]


def test_check_class_coverage_names_all_missing() -> None:
    # A fragment with no required symbols fails coverage for every class.
    ok, reasons = lint._check_class_coverage([], lint.REQUIRED_CLASSES)
    assert not ok
    assert "display" in reasons[0] and "input" in reasons[0] and "wifi" in reasons[0]


def test_check_firmware_subset_clean_and_dirty() -> None:
    upstream = set(data.load_firmware_names())
    real = next(n for n in data.load_firmware_names() if n.startswith("i915/"))
    ok, reasons = lint._check_firmware_subset([real, "amdgpu/aldebaran_ip_discovery.bin"], upstream)
    assert ok and reasons == []

    ok, reasons = lint._check_firmware_subset(["madeup/x.bin"], upstream)
    assert not ok
    assert "madeup/x.bin" in reasons[0]


def test_check_firmware_completeness_clean() -> None:
    # One entry per curated prefix covers every prefix.
    ok, reasons = lint._check_firmware_completeness(_complete_firmware().splitlines())
    assert ok and reasons == []


def test_check_firmware_completeness_names_missing_prefix() -> None:
    # Drop every entry under one prefix; that prefix is reported as uncovered.
    names = data.load_firmware_names()
    wifi_prefixes = profile.class_for("wifi").firmware_prefixes  # tuple attr, not a call
    kept = [n for n in names if not any(n.startswith(p) for p in wifi_prefixes)]
    assert kept, "sanity: something other than the wifi stack exists upstream"
    ok, reasons = lint._check_firmware_completeness(kept)
    assert not ok
    joined = reasons[0]
    for prefix in wifi_prefixes:
        assert prefix in joined


def test_check_firmware_completeness_empty_is_vacuously_complete() -> None:
    # No curated prefixes declared => vacuously complete. (The real profile has
    # prefixes, so this only holds for a hypothetical prefix-free profile.)
    import pengyos_desktop_drivers.profile as _p

    orig = _p.firmware_prefixes
    try:
        _p.firmware_prefixes = lambda: ()  # type: ignore[assignment]
        ok, reasons = lint._check_firmware_completeness([])
        assert ok and reasons == []
    finally:
        _p.firmware_prefixes = orig  # type: ignore[assignment]


def test_check_class_coverage_clean_and_dirty() -> None:
    symbols = {"CONFIG_DRM", "CONFIG_INPUT_EVDEV", "CONFIG_IWLWIFI"}
    ok, reasons = lint._check_class_coverage(
        [f"{s}=y" for s in symbols], lint.REQUIRED_CLASSES
    )
    assert ok and reasons == []

    # Drop the wifi symbol: coverage fails and names the missing class.
    ok, reasons = lint._check_class_coverage(
        [f"{s}=y" for s in symbols if s != "CONFIG_IWLWIFI"], lint.REQUIRED_CLASSES
    )
    assert not ok
    assert "wifi" in reasons[0]


# --------------------------------------------------------------------------- #
# lint_fragment composition
# --------------------------------------------------------------------------- #
def _write(tmp_path: pathlib.Path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _complete_firmware() -> str:
    """Return a firmware list with one real entry under every curated prefix.

    This is the minimal list that satisfies the firmware-subset rule's
    completeness half (every curated prefix covered) -- the opposite of the
    "missing the wifi stack" case. Lets a test use a clean firmware list
    without emitting the full Desktop profile.
    """
    names = data.load_firmware_names()
    lines = [
        next(n for n in names if n.startswith(prefix))
        for prefix in profile.firmware_prefixes()
    ]
    return "\n".join(lines) + "\n"


def test_lint_clean_generic_profile(tmp_path: pathlib.Path) -> None:
    # Emit the real Desktop profile, then lint it: it must be lint-clean.
    emit.emit_desktop_profile(tmp_path)
    report = lint.lint_fragment(
        str(tmp_path / emit.FRAGMENT_FILENAME),
        str(tmp_path / emit.FIRMWARE_FILENAME),
    )
    assert report["ok"], report["errors"]
    assert all(report["rules"].values())
    assert report["errors"] == []


def test_lint_rejects_missing_wifi_firmware(tmp_path: pathlib.Path) -> None:
    """The spec's "reject a firmware list missing the wifi stack" case.

    Emit the clean profile, then drop every wifi firmware entry. The fragment
    is untouched (class coverage still holds), so the ONLY rule that fires is
    firmware-subset (completeness) -- the wifi prefixes are now uncovered.
    """
    emit.emit_desktop_profile(tmp_path)
    fw_path = tmp_path / emit.FIRMWARE_FILENAME
    fw_lines = fw_path.read_text(encoding="utf-8").splitlines()
    wifi_prefixes = profile.class_for("wifi").firmware_prefixes  # tuple attr, not a call
    kept = [n for n in fw_lines if not any(n.startswith(p) for p in wifi_prefixes)]
    # Sanity: the wifi stack is a non-empty subset of the emitted list.
    assert kept and len(kept) < len(fw_lines), "sanity: the wifi stack is non-empty"
    fw_path.write_text("\n".join(kept) + "\n", encoding="utf-8")

    with pytest.raises(lint.LintViolation) as excinfo:
        lint.lint_fragment(
            str(tmp_path / emit.FRAGMENT_FILENAME),
            str(fw_path),
        )
    # Only the firmware-subset rule fires (class coverage is on the fragment,
    # which is untouched). The rules dict is attached to the violation.
    rules = excinfo.value.rules
    assert rules[lint.RULE_FIRMWARE_SUBSET] is False
    assert rules[lint.RULE_SYMBOL_EXISTS] is True
    assert rules[lint.RULE_NO_VENDOR_ACCEL] is True
    assert rules[lint.RULE_CLASS_COVERAGE] is True
    joined = "\n".join(excinfo.value.errors)
    for prefix in wifi_prefixes:
        assert prefix in joined


def test_lint_golden_unchanged_after_completeness(tmp_path: pathlib.Path) -> None:
    # The completeness check must not change what the clean profile emits: the
    # golden fragment + firmware hashes are still the pinned ones.
    report = emit.emit_desktop_profile(tmp_path)
    assert report["fragment"]["sha256"] == emit_test.GOLDEN_FRAGMENT_SHA256
    assert report["firmware"]["sha256"] == emit_test.GOLDEN_FIRMWARE_SHA256


def test_lint_collects_every_violation(tmp_path: pathlib.Path) -> None:
    # A single dirty profile that trips all four rules surfaces every reason.
    frag = _write(
        tmp_path,
        "bad.fragment",
        "CONFIG_DRM=y\nCONFIG_NVIDIA=y\nCONFIG_FAKE=y\n",
    )
    # Complete list (so completeness passes) plus one made-up name, so only the
    # subset half of firmware-subset fires.
    fw = _write(tmp_path, "bad.firmware", _complete_firmware() + "madeup/x.bin\n")
    with pytest.raises(lint.LintViolation) as excinfo:
        lint.lint_fragment(frag, fw)
    joined = "\n".join(excinfo.value.errors)
    # symbol-exists (CONFIG_FAKE, CONFIG_NVIDIA), no-vendor-accel (NVIDIA),
    # firmware-subset (madeup/x.bin), and class-coverage (input, wifi) all fire.
    assert "CONFIG_FAKE" in joined
    assert "CONFIG_NVIDIA" in joined
    assert "madeup/x.bin" in joined
    assert "wifi" in joined  # class coverage names the missing class
    assert len(excinfo.value.errors) == 4


def test_lint_each_rule_fires_in_isolation(tmp_path: pathlib.Path) -> None:
    """Each rule fails on exactly one reason when tripped alone."""
    # Clean base: one display + input + wifi symbol, none forbidden.
    base = ["CONFIG_DRM=y", "CONFIG_INPUT_EVDEV=y", "CONFIG_IWLWIFI=y"]

    def _lint(lines: list[str], firmware: str | None = None) -> list[str]:
        # Default to a complete firmware list so completeness passes and only the
        # intended rule trips (an empty list would also trip completeness).
        if firmware is None:
            firmware = _complete_firmware()
        frag = _write(tmp_path, "p.fragment", "\n".join(lines) + "\n")
        fw = _write(tmp_path, "p.firmware", firmware)
        with pytest.raises(lint.LintViolation) as excinfo:
            lint.lint_fragment(frag, fw)
        return excinfo.value.errors

    # symbol-exists only: a made-up symbol (all else clean).
    errs = _lint(base + ["CONFIG_FAKE=y"])
    assert len(errs) == 1 and "CONFIG_FAKE" in errs[0]

    # no-vendor-accel only: a real (in-list) but forbidden symbol.
    errs = _lint(base + ["CONFIG_DRM_NOUVEAU=y"])
    assert len(errs) == 1 and "CONFIG_DRM_NOUVEAU" in errs[0]

    # firmware-subset only: clean fragment, a complete list plus a non-upstream
    # name (completeness passes -- every curated prefix is present), so only the
    # subset half fires.
    errs = _lint(base, _complete_firmware() + "madeup/x.bin\n")
    assert len(errs) == 1 and "madeup/x.bin" in errs[0]

    # class-coverage only: drop the wifi symbol (display + input remain).
    errs = _lint(["CONFIG_DRM=y", "CONFIG_INPUT_EVDEV=y"])
    assert len(errs) == 1 and "wifi" in errs[0]


def test_lint_raises_on_missing_file() -> None:
    from pengyos_desktop_drivers.errors import PengyOSError

    with pytest.raises(PengyOSError):
        lint.lint_fragment(str(ROOT / "does-not-exist.fragment"), "x")


def test_lint_result_reports_paths_and_rules(tmp_path: pathlib.Path) -> None:
    frag = _write(tmp_path, "ok.fragment", "CONFIG_DRM=y\nCONFIG_INPUT_EVDEV=y\nCONFIG_IWLWIFI=y\n")
    fw = _write(tmp_path, "ok.firmware", _complete_firmware())
    report = lint.lint_fragment(frag, fw)
    assert report["ok"]
    assert set(report["rules"]) == {
        lint.RULE_SYMBOL_EXISTS,
        lint.RULE_NO_VENDOR_ACCEL,
        lint.RULE_FIRMWARE_SUBSET,
        lint.RULE_CLASS_COVERAGE,
    }
    assert report["fragment"].endswith("ok.fragment")
    assert report["firmware"].endswith("ok.firmware")


def test_lint_empty_firmware_is_incomplete(tmp_path: pathlib.Path) -> None:
    # An empty firmware list covers no curated prefix, so it fails the
    # completeness half of firmware-subset -- the "missing the wifi stack" case
    # the spec requires lint to reject.
    frag = _write(
        tmp_path, "ok.fragment", "CONFIG_DRM=y\nCONFIG_INPUT_EVDEV=y\nCONFIG_IWLWIFI=y\n"
    )
    fw = _write(tmp_path, "empty.firmware", "")
    with pytest.raises(lint.LintViolation) as excinfo:
        lint.lint_fragment(frag, fw)
    joined = "\n".join(excinfo.value.errors)
    assert "curated prefix" in joined
    for prefix in profile.firmware_prefixes():
        assert prefix in joined


def test_lint_complete_firmware_is_valid_subset(tmp_path: pathlib.Path) -> None:
    # A list with one entry per curated prefix is a valid (complete) subset.
    frag = _write(
        tmp_path, "ok.fragment", "CONFIG_DRM=y\nCONFIG_INPUT_EVDEV=y\nCONFIG_IWLWIFI=y\n"
    )
    fw = _write(tmp_path, "ok.firmware", _complete_firmware())
    report = lint.lint_fragment(frag, fw)
    assert report["ok"], report["errors"]


# --------------------------------------------------------------------------- #
# CLI contract
# --------------------------------------------------------------------------- #
def test_cli_lint_clean_exits_zero(
    capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    emit.emit_desktop_profile(tmp_path)
    code = cli.main(
        [
            "lint",
            "--fragment",
            str(tmp_path / emit.FRAGMENT_FILENAME),
            "--firmware",
            str(tmp_path / emit.FIRMWARE_FILENAME),
        ]
    )
    assert code == 0
    assert "lint: ok" in capsys.readouterr().out


def test_cli_lint_dirty_exits_two_with_all_errors(
    capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    frag = _write(
        tmp_path,
        "bad.fragment",
        "CONFIG_DRM=y\nCONFIG_NVIDIA=y\nCONFIG_FAKE=y\n",
    )
    fw = _write(tmp_path, "bad.firmware", "madeup/x.bin\n")
    code = cli.main(["lint", "--fragment", frag, "--firmware", fw])
    assert code == 2
    err = capsys.readouterr().err
    assert "lint:" in err
    # Every failing rule's reason is surfaced.
    assert "CONFIG_FAKE" in err
    assert "CONFIG_NVIDIA" in err
    assert "madeup/x.bin" in err


def test_cli_lint_missing_file_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["lint", "--fragment", "nope", "--firmware", "nope"])
    assert code == 2
    assert "error:" in capsys.readouterr().err.lower()


def test_cli_lint_json_reports_rules(
    capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    import json

    frag = _write(tmp_path, "ok.fragment", "CONFIG_DRM=y\nCONFIG_INPUT_EVDEV=y\nCONFIG_IWLWIFI=y\n")
    fw = _write(tmp_path, "ok.firmware", _complete_firmware())
    code = cli.main(["lint", "--fragment", frag, "--firmware", fw, "--json"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert set(report["rules"]) == {
        lint.RULE_SYMBOL_EXISTS,
        lint.RULE_NO_VENDOR_ACCEL,
        lint.RULE_FIRMWARE_SUBSET,
        lint.RULE_CLASS_COVERAGE,
    }
