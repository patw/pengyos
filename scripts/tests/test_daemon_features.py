"""Tests for check-daemon-features.py (run by the desktop post-build).

The check exists because a Buildroot sub-option that defaults off ships a daemon
missing half its protocols and says nothing: on 26.10.40 bluez5_utils was built
--disable-hid --disable-hog, so a Bluetooth gamepad paired, connected, resolved
its GATT HID service, reported a battery percentage — and never became an input
device. No file in the image could answer "why".

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check-daemon-features.py"


@pytest.fixture
def cdf():
    spec = importlib.util.spec_from_file_location("cdf", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def features(tmp_path, text, name="features-required.txt"):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_contains_holds_for_a_binary(cdf, tmp_path):
    target = tmp_path / "target"
    (target / "usr/libexec/bluetooth").mkdir(parents=True)
    (target / "usr/libexec/bluetooth/bluetoothd").write_bytes(b"\x7fELF\x00hog\x00uhid\x00")
    req = features(tmp_path, "contains usr/libexec/bluetooth/bluetoothd uhid  # HoG\n")
    assert cdf.check(target, [req]) == []


def test_contains_reports_a_missing_capability(cdf, tmp_path):
    """The 26.10.40 bug: the daemon is there, the HID profile is not."""
    target = tmp_path / "target"
    (target / "usr/libexec/bluetooth").mkdir(parents=True)
    (target / "usr/libexec/bluetooth/bluetoothd").write_bytes(b"\x7fELF\x00a2dp\x00")
    req = features(tmp_path, "contains usr/libexec/bluetooth/bluetoothd uhid  # PLUGINS_HID\n")
    problems = cdf.check(target, [req])
    assert len(problems) == 1
    assert "no 'uhid' in it" in problems[0]
    assert "PLUGINS_HID" in problems[0], "the reason must survive into the failure"


def test_missing_file_is_not_an_error_of_the_checker(cdf, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    req = features(tmp_path, "contains usr/bin/nothing uhid\n")
    problems = cdf.check(target, [req])
    assert problems and "missing" in problems[0]


def test_exec_any_searches_bin_sbin_and_libexec(cdf, tmp_path):
    target = tmp_path / "target"
    (target / "usr/libexec/bluetooth").mkdir(parents=True)
    btmon = target / "usr/libexec/bluetooth/btmon"
    btmon.write_text("#!/bin/sh\n")
    btmon.chmod(0o755)
    req = features(tmp_path, "exec-any btmon\n")
    assert cdf.check(target, [req]) == []


def test_exec_any_respects_the_executable_bit(cdf, tmp_path):
    target = tmp_path / "target"
    (target / "usr/bin").mkdir(parents=True)
    (target / "usr/bin/btmon").write_text("not executable\n")
    req = features(tmp_path, "exec-any btmon\n")
    problems = cdf.check(target, [req])
    assert problems and "no executable by that name" in problems[0]


def test_absolute_symlinks_resolve_inside_the_rootfs(cdf, tmp_path):
    # ksmbd-tools installs usr/bin/ksmbd.mountd -> /usr/libexec/ksmbd.tools;
    # the link must be followed into the target, not the build host's /usr.
    target = tmp_path / "target"
    (target / "usr/bin").mkdir(parents=True)
    (target / "usr/libexec").mkdir(parents=True)
    tool = target / "usr/libexec/ksmbd.tools"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    (target / "usr/bin/ksmbd.mountd").symlink_to("/usr/libexec/ksmbd.tools")
    (target / "usr/bin/ksmbd.gone").symlink_to("/usr/libexec/ksmbd.missing")
    req = features(tmp_path, "exec-any ksmbd.mountd\nexec usr/bin/ksmbd.mountd\n")
    assert cdf.check(target, [req]) == []
    problems = cdf.check(target, [features(tmp_path, "exec-any ksmbd.gone\n")])
    assert problems and "no executable by that name" in problems[0]


def test_exists_covers_plain_data_files(cdf, tmp_path):
    target = tmp_path / "target"
    (target / "usr/lib/udev/rules.d").mkdir(parents=True)
    (target / "usr/lib/udev/rules.d/70-pengyos-rgb.rules").write_text('KERNEL=="port"\n')
    req = features(tmp_path, "exists usr/lib/udev/rules.d/70-pengyos-rgb.rules  # overlay\n")
    assert cdf.check(target, [req]) == []


def test_comments_and_blank_lines_are_ignored(cdf, tmp_path):
    req = features(tmp_path, "# a comment\n\n   \n# another\n")
    assert cdf.parse_features(req) == []


def test_unknown_kind_is_a_problem_not_a_crash(cdf, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    req = features(tmp_path, "frobnicate usr/bin/thing\n")
    problems = cdf.check(target, [req])
    assert problems and "unknown check" in problems[0]


def test_line_numbers_point_at_the_offending_line(cdf, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    req = features(tmp_path, "# header\nexec usr/bin/missing\n")
    problems = cdf.check(target, [req])
    assert problems and ":2:" in problems[0]


def test_the_real_features_file_is_well_formed(cdf):
    """Every line in the shipped file must parse against the schema."""
    repo = Path(__file__).resolve().parents[2]
    req = repo / "buildroot-external/board/desktop/features-required.txt"
    checks = cdf.parse_features(req)
    assert checks, "the features file is empty"
    kinds = {"contains", "exec", "exec-any", "exists"}
    for lineno, kind, args, _reason in checks:
        assert kind in kinds, f"{req.name}:{lineno}: unknown kind {kind!r}"
        assert 1 <= len(args) <= 2, f"{req.name}:{lineno}: bad argument count"
        assert args, f"{req.name}:{lineno}: no arguments"
