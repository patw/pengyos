"""Tests for lint.

Covers the lint deliverable: the installer is present AND wired to autostart for
the inferred session, autologin + live marker are present, and the live path does
not write the target disk before the user confirms. Every rule has teeth — a
mutation test for each — plus the ``read_overlay`` reader, the report / ``--json``
shape, and the CLI end-to-end behaviour (clean → EXIT_OK, any rule failed →
EXIT_FAIL).

emit and lint share the model, so the strongest check links them: every emitted
golden overlay must lint clean.
"""

import json

import pytest

from pengyos_live import cli, emit, lint
from pengyos_live.cli import EXIT_FAIL, EXIT_OK, main
from pengyos_live.lint import PREEMPTIVE_DISK_WRITE_PATTERNS, read_overlay
from pengyos_live.model import (
    AUTOLOGIN_PATH,
    INSTALLER_SESSION_PATH,
    INSTALLER_UNIT_PATH,
    LIVE_MARKER_PATH,
    SUDOERS_PATH,
    Edition,
    Session,
)

ALL_CASES = [
    (e.value, s.value)
    for e in Edition
    for s in Session
]


# --- read_overlay ------------------------------------------------------------


def test_read_overlay_flattens_tree_to_absolute_paths(tmp_path):
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    overlay = read_overlay(tmp_path)
    assert AUTOLOGIN_PATH in overlay
    assert LIVE_MARKER_PATH in overlay
    assert INSTALLER_SESSION_PATH in overlay
    # the content is the exact on-disk content
    assert overlay[LIVE_MARKER_PATH].endswith("live=true\n")


def test_read_overlay_ignores_directories(tmp_path):
    # emit writes nested dirs; only files map to content
    emit.emit_from_strings("core", "console", tmp_path)
    overlay = read_overlay(tmp_path)
    assert all("/" == p[0] for p in overlay)
    # four files (autologin, marker, installer, sudo policy), no directory
    # paths leaked in
    assert len(overlay) == 4


def test_read_overlay_missing_dir_raises():
    with pytest.raises(NotADirectoryError):
        read_overlay("/nonexistent/overlay/dir")


# --- positive: clean overlays lint ok ----------------------------------------


@pytest.mark.parametrize("edition,session", ALL_CASES)
def test_lint_clean_overlay_ok(edition, session, tmp_path):
    emit.emit_from_strings(edition, session, tmp_path)
    report = lint.lint_directory(tmp_path)
    assert report.ok
    assert report.session is Session.CONSOLE if session == "console" else Session.COSMIC


def test_lint_cosmic_infers_session():
    import tempfile

    tmp = tempfile.mkdtemp()
    emit.emit_from_strings("desktop", "cosmic", tmp)
    assert lint.lint_directory(tmp).session is Session.COSMIC


def test_lint_console_infers_session():
    import tempfile

    tmp = tempfile.mkdtemp()
    emit.emit_from_strings("core", "console", tmp)
    assert lint.lint_directory(tmp).session is Session.CONSOLE


# --- Rule 1: installer present + wired to autostart --------------------------


def test_lint_missing_installer_fails(tmp_path):
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    del ov[INSTALLER_SESSION_PATH]
    report = lint.lint_overlay(ov)
    assert not report.ok
    assert report.session is None
    assert any(f.rule.value == "installer_present" for f in report.findings)


def test_lint_both_installer_artifacts_fails(tmp_path):
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    # add the console unit alongside the cosmic entry -> ambiguous
    ov[INSTALLER_UNIT_PATH] = "[Unit]\nWantedBy=multi-user.target\n"
    report = lint.lint_overlay(ov)
    assert not report.ok
    assert any(f.rule.value == "installer_present" for f in report.findings)


def test_lint_installer_wrong_binary_fails(tmp_path):
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    ov[INSTALLER_SESSION_PATH] = "[Desktop Entry]\nExec=/usr/bin/pengyos-run\n"
    report = lint.lint_overlay(ov)
    assert not report.ok
    assert any(f.rule.value == "installer_present" for f in report.findings)


def test_lint_console_unit_not_wired_to_autostart_fails(tmp_path):
    emit.emit_from_strings("core", "console", tmp_path)
    ov = read_overlay(tmp_path)
    # drop the WantedBy= line so the unit does not autostart
    ov[INSTALLER_UNIT_PATH] = "[Unit]\nDescription=x\n[Service]\nExecStart=/usr/bin/pengyos-install\n"
    report = lint.lint_overlay(ov)
    assert report.session is Session.CONSOLE
    assert any(f.rule.value == "installer_autostarted" for f in report.findings)


def test_lint_cosmic_entry_not_wired_to_autostart_fails(tmp_path):
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    ov[INSTALLER_SESSION_PATH] = "[Desktop Entry]\nExec=/usr/bin/pengyos-install\n"
    report = lint.lint_overlay(ov)
    assert report.session is Session.COSMIC
    assert any(f.rule.value == "installer_autostarted" for f in report.findings)


# --- Rule 2: autologin present -----------------------------------------------


def test_lint_missing_autologin_fails(tmp_path):
    ov = read_overlay(tmp_path)
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    del ov[AUTOLOGIN_PATH]
    report = lint.lint_overlay(ov)
    assert any(f.rule.value == "autologin_present" for f in report.findings)


def test_lint_autologin_without_live_user_fails(tmp_path):
    ov = read_overlay(tmp_path)
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    ov[AUTOLOGIN_PATH] = "[Service]\nExecStart=\nExecStart=-/sbin/getty --noclear %I $BAUD\n"
    report = lint.lint_overlay(ov)
    assert any(f.rule.value == "autologin_present" for f in report.findings)


# --- Rule 3: live marker present ---------------------------------------------


def test_lint_missing_marker_fails(tmp_path):
    ov = read_overlay(tmp_path)
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    del ov[LIVE_MARKER_PATH]
    report = lint.lint_overlay(ov)
    assert any(f.rule.value == "live_marker_present" for f in report.findings)


def test_lint_marker_without_live_flag_fails(tmp_path):
    ov = read_overlay(tmp_path)
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    ov[LIVE_MARKER_PATH] = "# managed\nedition=desktop\n"
    report = lint.lint_overlay(ov)
    assert any(f.rule.value == "live_marker_present" for f in report.findings)


# --- Rule 5: live-user sudo policy present -----------------------------------


def test_lint_missing_sudo_policy_fails(tmp_path):
    ov = read_overlay(tmp_path)
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    del ov[SUDOERS_PATH]
    report = lint.lint_overlay(ov)
    assert any(f.rule.value == "sudo_policy_present" for f in report.findings)


def test_lint_sudo_policy_without_nopasswd_fails(tmp_path):
    ov = read_overlay(tmp_path)
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    ov[SUDOERS_PATH] = "live ALL=(ALL) /usr/bin/pengyos-install\n"
    report = lint.lint_overlay(ov)
    assert any(f.rule.value == "sudo_policy_present" for f in report.findings)


def test_lint_sudo_policy_wrong_binary_fails(tmp_path):
    ov = read_overlay(tmp_path)
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    ov[SUDOERS_PATH] = "live ALL=(ALL) NOPASSWD: /usr/bin/pengyos-run\n"
    report = lint.lint_overlay(ov)
    assert any(f.rule.value == "sudo_policy_present" for f in report.findings)


# --- Rule 4: no pre-emptive disk write ---------------------------------------


@pytest.mark.parametrize("pattern,reason", PREEMPTIVE_DISK_WRITE_PATTERNS)
def test_lint_each_preemptive_pattern_fails(tmp_path, pattern, reason):
    ov = read_overlay(tmp_path)
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    ov[INSTALLER_SESSION_PATH] = (
        "[Desktop Entry]\nExec=/usr/bin/pengyos-install " + pattern + "\n"
    )
    report = lint.lint_overlay(ov)
    assert any(f.rule.value == "no_preemptive_disk_write" for f in report.findings)


def test_lint_clean_installer_has_no_preemptive_pattern(tmp_path):
    ov = read_overlay(tmp_path)
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    content = ov[INSTALLER_SESSION_PATH]
    for pattern, _reason in PREEMPTIVE_DISK_WRITE_PATTERNS:
        assert pattern not in content


# --- report / JSON shape -----------------------------------------------------


def test_report_ok_property():
    import tempfile

    tmp = tempfile.mkdtemp()
    emit.emit_from_strings("desktop", "cosmic", tmp)
    assert lint.lint_directory(tmp).ok
    ov = read_overlay(tmp)
    del ov[LIVE_MARKER_PATH]
    assert not lint.lint_overlay(ov).ok


def test_report_to_dict_shape():
    import tempfile

    tmp = tempfile.mkdtemp()
    emit.emit_from_strings("server", "console", tmp)
    d = lint.report_to_dict(lint.lint_directory(tmp))
    assert d == {"ok": True, "session": "console", "errors": []}


def test_report_to_dict_reports_errors():
    ov = {}  # empty overlay -> every rule fails
    report = lint.lint_overlay(ov)
    d = lint.report_to_dict(report)
    assert d["ok"] is False
    assert d["session"] is None
    assert d["errors"][0]["rule"] == "installer_present"
    assert isinstance(d["errors"][0]["detail"], str)


def test_report_to_json_is_valid_and_sorted():
    ov = {}
    text = lint.report_to_json(lint.lint_overlay(ov))
    parsed = json.loads(text)
    assert parsed["ok"] is False
    # sorted keys: "errors" before "ok" before "session"
    assert list(parsed) == sorted(parsed)


# --- CLI lint end-to-end -----------------------------------------------------


def test_cli_lint_clean_returns_ok(tmp_path, capsys):
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    rc = main(["lint", "--overlay", str(tmp_path)])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "OK" in out
    assert "cosmic" in out


def test_cli_lint_all_emitted_cases_return_ok(capsys):
    """Every emitted golden overlay must lint clean through the CLI."""
    for edition, session in ALL_CASES:
        tmp = __import__("tempfile").mkdtemp()
        emit.emit_from_strings(edition, session, tmp)
        rc = main(["lint", "--overlay", str(tmp)])
        assert rc == EXIT_OK, f"{edition}/{session} did not lint clean"


def test_cli_lint_rule_failure_returns_fail(tmp_path, capsys):
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    ov = read_overlay(tmp_path)
    del ov[LIVE_MARKER_PATH]  # mutate on disk
    (tmp_path / "etc/pengyos/live").unlink()
    rc = main(["lint", "--overlay", str(tmp_path)])
    assert rc == EXIT_FAIL
    err = capsys.readouterr().err
    assert "FAIL" in err
    assert "live_marker_present" in err


def test_cli_lint_json_output(tmp_path, capsys):
    emit.emit_from_strings("desktop", "console", tmp_path)
    rc = main(["lint", "--overlay", str(tmp_path), "--json"])
    assert rc == EXIT_OK
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["ok"] is True
    assert parsed["session"] == "console"
    assert parsed["errors"] == []


def test_cli_lint_json_reports_fail(tmp_path, capsys):
    emit.emit_from_strings("desktop", "cosmic", tmp_path)
    (tmp_path / "etc/systemd/system/getty@.service.d/autologin.conf").unlink()
    rc = main(["lint", "--overlay", str(tmp_path), "--json"])
    assert rc == EXIT_FAIL
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["ok"] is False
    assert any(e["rule"] == "autologin_present" for e in parsed["errors"])


def test_cli_lint_missing_dir_returns_fail(capsys):
    rc = main(["lint", "--overlay", "/nonexistent/overlay/dir"])
    assert rc == EXIT_FAIL
    err = capsys.readouterr().err
    assert "cannot read" in err


# --- emit <-> lint round trip (the strongest link) ---------------------------


def test_every_golden_overlay_lints_clean():
    """The committed golden trees (emit's oracle) must each lint clean."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    golden = repo_root / "tests" / "golden"
    for case in sorted(p.name for p in golden.iterdir() if p.is_dir()):
        assert lint.lint_directory(golden / case).ok, f"golden {case} not clean"
