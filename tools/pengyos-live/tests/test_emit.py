"""Tests for emit.

Covers the emit deliverable: writing the live overlay to an output directory,
flattening absolute target paths relative to the output root, deterministic /
golden output for desktop + cosmic and desktop + console, the ``--json``
summary, and the CLI end-to-end behaviour.

The committed golden trees under ``tests/golden/<edition>-<session>/`` are the
reference oracle: emit renders the overlay there and the emitted tree is
compared byte-for-byte against them.
"""

import json
import os
from pathlib import Path

import pytest

from pengyos_live import cli, emit
from pengyos_live.cli import EXIT_FAIL, EXIT_OK, EXIT_USAGE, main
from pengyos_live.model import (
    AUTOLOGIN_PATH,
    INSTALLER_SESSION_PATH,
    INSTALLER_UNIT_PATH,
    LIVE_MARKER_PATH,
    SUDOERS_PATH,
    Edition,
    Session,
    build_overlay,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN = REPO_ROOT / "tests" / "golden"


# --- emit_path: flatten absolute target paths -------------------------------


def test_emit_path_flattens_leading_slash():
    assert str(emit.emit_path("/etc/pengyos/live", "/out")) == os.path.join(
        "/out", "etc", "pengyos", "live"
    )


def test_emit_path_is_under_out_dir():
    dest = emit.emit_path(AUTOLOGIN_PATH, "/out")
    assert str(dest).startswith("/out/etc/")


# --- write_overlay ----------------------------------------------------------


def test_write_overlay_writes_all_files_at_flattened_paths(tmp_path):
    ov = build_overlay(Edition.DESKTOP, Session.COSMIC)
    written = emit.write_overlay(ov, tmp_path)

    assert len(written) == 4
    for f in ov.files:
        dest = emit.emit_path(f.path, tmp_path)
        assert dest.is_file()
        assert dest.read_text(encoding="utf-8") == f.content
        # the path is under the output root, not the real target root
        assert str(dest).startswith(str(tmp_path))


def test_write_overlay_creates_nested_directories(tmp_path):
    ov = build_overlay(Edition.DESKTOP, Session.COSMIC)
    write = emit.write_overlay(ov, tmp_path)
    # the getty drop-in lives several directories deep
    deep = [p for p in write if "getty" in str(p)][0]
    assert deep.parent.is_dir()


def test_write_overlay_overwrites_existing(tmp_path):
    ov = build_overlay(Edition.DESKTOP, Session.CONSOLE)
    marker = emit.emit_path(LIVE_MARKER_PATH, tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("stale\n", encoding="utf-8")
    emit.write_overlay(ov, tmp_path)
    assert marker.read_text(encoding="utf-8").endswith("live=true\n")
    assert "stale" not in marker.read_text(encoding="utf-8")


def test_write_overlay_order_is_autologin_marker_installer_sudo(tmp_path):
    ov = build_overlay(Edition.DESKTOP, Session.COSMIC)
    written = [str(p) for p in emit.write_overlay(ov, tmp_path)]
    assert written.index(str(emit.emit_path(AUTOLOGIN_PATH, tmp_path))) < written.index(
        str(emit.emit_path(LIVE_MARKER_PATH, tmp_path))
    )
    assert written.index(str(emit.emit_path(LIVE_MARKER_PATH, tmp_path))) < written.index(
        str(emit.emit_path(INSTALLER_SESSION_PATH, tmp_path))
    )
    # the sudo policy is written last, after the installer autostart
    assert written.index(str(emit.emit_path(SUDOERS_PATH, tmp_path))) == len(written) - 1
    assert len(written) == 4


# --- emit_from_strings ------------------------------------------------------


def test_emit_from_strings_writes_four_files(tmp_path):
    ov = emit.emit_from_strings("desktop", "cosmic", tmp_path)
    assert ov.edition is Edition.DESKTOP
    assert ov.session is Session.COSMIC
    # four files (nested dirs are not counted): autologin, marker, installer,
    # sudo policy
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert len(files) == 4


def test_emit_from_strings_console_writes_unit_not_session(tmp_path):
    emit.emit_from_strings("core", "console", tmp_path)
    assert (tmp_path / "etc/systemd/system/pengyos-installer.service").is_file()
    assert not (tmp_path / "usr/share/autostart/pengyos-installer.desktop").exists()


def test_emit_writes_sudo_policy_for_both_sessions(tmp_path):
    for session in ("cosmic", "console"):
        out = tmp_path / session
        emit.emit_from_strings("desktop", session, out)
        sudo = out / "etc/sudoers.d/pengyos-live"
        assert sudo.is_file(), f"sudo policy missing for {session}"
        assert "NOPASSWD" in sudo.read_text(encoding="utf-8")
        assert "/usr/bin/pengyos-install" in sudo.read_text(encoding="utf-8")


def test_emit_from_strings_unknown_edition_raises():
    # ValueError is raised before any filesystem write, so no tmp_path needed.
    with pytest.raises(ValueError):
        emit.emit_from_strings("tablet", "cosmic", "/nonexistent")


def test_emit_from_strings_unknown_session_raises():
    with pytest.raises(ValueError):
        emit.emit_from_strings("desktop", "wayland", "/nonexistent")


# --- determinism / golden --------------------------------------------------


def test_emit_is_deterministic_across_runs(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    emit.emit_from_strings("desktop", "cosmic", a)
    emit.emit_from_strings("desktop", "cosmic", b)
    for goldfile in a.rglob("*"):
        if goldfile.is_file():
            rel = goldfile.relative_to(a)
            assert goldfile.read_bytes() == (b / rel).read_bytes()


@pytest.mark.parametrize("case", ["desktop-cosmic", "desktop-console"])
def test_emit_matches_committed_golden(tmp_path, case):
    """The emitted tree must match the committed golden reference byte-for-byte."""
    edition, session = case.split("-")
    golden = GOLDEN / case
    out = tmp_path / case

    emit.emit_from_strings(edition, session, out)

    for goldfile in golden.rglob("*"):
        if not goldfile.is_file():
            continue
        rel = goldfile.relative_to(golden)
        emitted = out / rel
        assert emitted.is_file(), f"emitted overlay missing {rel}"
        assert emitted.read_bytes() == goldfile.read_bytes(), (
            f"emitted {rel} for {case} does not match golden"
        )


def test_golden_fixtures_exist_for_focus_cases():
    """Sanity: the golden oracle for desktop+cosmic and desktop+console exists."""
    for case in ("desktop-cosmic", "desktop-console"):
        assert (GOLDEN / case).is_dir(), f"missing golden oracle: {case}"


# --- summary / --json -------------------------------------------------------


def test_summary_from_written_has_expected_keys(tmp_path):
    ov = build_overlay(Edition.DESKTOP, Session.COSMIC)
    written = emit.write_overlay(ov, tmp_path)
    summary = emit.summary_from_written(ov, written)
    assert set(summary) == {"edition", "session", "files", "written"}
    assert summary["edition"] == "desktop"
    assert summary["session"] == "cosmic"
    assert len(summary["files"]) == 4
    assert len(summary["written"]) == 4


def test_summary_to_json_is_valid_and_sorted(tmp_path):
    ov = build_overlay(Edition.CORE, Session.CONSOLE)
    written = emit.write_overlay(ov, tmp_path)
    text = emit.summary_to_json(emit.summary_from_written(ov, written))
    parsed = json.loads(text)
    assert parsed["edition"] == "core"
    assert parsed["session"] == "console"


# --- CLI emit end-to-end ----------------------------------------------------


def test_cli_emit_returns_ok_and_writes(tmp_path):
    out = tmp_path / "out"
    rc = main(
        [
            "emit",
            "--edition",
            "desktop",
            "--session",
            "cosmic",
            "--out",
            str(out),
        ]
    )
    assert rc == EXIT_OK
    assert (out / "etc/pengyos/live").is_file()
    assert (out / "etc/systemd/system/getty@.service.d/autologin.conf").is_file()
    assert (out / "usr/share/autostart/pengyos-installer.desktop").is_file()


def test_cli_emit_json_output(tmp_path, capsys):
    out = tmp_path / "out"
    rc = main(
        [
            "emit",
            "--edition",
            "core",
            "--session",
            "console",
            "--out",
            str(out),
            "--json",
        ]
    )
    assert rc == EXIT_OK
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["edition"] == "core"
    assert parsed["session"] == "console"
    assert len(parsed["files"]) == 4
    assert len(parsed["written"]) == 4


def test_cli_emit_human_output_names_every_artifact(tmp_path, capsys):
    out = tmp_path / "out"
    rc = main(
        [
            "emit",
            "--edition",
            "server",
            "--session",
            "cosmic",
            "--out",
            str(out),
        ]
    )
    assert rc == EXIT_OK
    out_text = capsys.readouterr().out
    assert "4 live-overlay file(s)" in out_text
    for needle in ("autologin", "pengyos-installer.desktop", "sudoers"):
        assert needle in out_text


def test_cli_emit_unknown_session_is_usage_error(capsys):
    # argparse `choices` rejects this before main returns -> exit 2.
    with pytest.raises(SystemExit) as exc:
        main(["emit", "--edition", "desktop", "--session", "wayland", "--out", "o"])
    assert exc.value.code == EXIT_USAGE
