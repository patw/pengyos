"""Tests for the Desktop session check.

The live half (Xvfb + QEMU + a typed console command) cannot run in a unit
suite; the pure half is what the verdict is built from, and it is tested against
real captures: the fixtures under ``tests/fixtures/`` are the ``==PS==`` /
``==FAILED==`` / ``==END==`` report read off a real 26.10.7 Desktop installer
boot, plus two mutations of it (compositor removed, a failed unit inserted).
That is what makes the checks non-vacuous: the same code that passes the real
capture fails the mutations.

Covers:

* :func:`session_argv` — the flags the session boot depends on (rendering GPU,
  removable stick with bootindex, snapshot overlay, a *socket* serial) and its
  input validation;
* :func:`session_env` — no host Wayland, no host LD_LIBRARY_PATH, X11 SDL;
* :func:`parse_session_report` — marker extraction, and the no-report case;
* :func:`evaluate_session` — the check set, its verdicts and its evidence;
* the ``desktop`` subcommand's argument surface and its exit codes.
"""

from pathlib import Path

import pytest

from pengyos_qemu_verify import cli
from pengyos_qemu_verify import desktop as D
from pengyos_qemu_verify.assertions import FAIL_STATUS, PASS_STATUS

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOOD = FIXTURES / "serial_desktop_session.log"
NO_COMP = FIXTURES / "serial_desktop_session_no_comp.log"
FAILED_UNIT = FIXTURES / "serial_desktop_session_failed_unit.log"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# --- argv -------------------------------------------------------------------


def test_session_argv_carries_the_flags_the_session_needs():
    argv = D.session_argv(
        "/img/stick.img", serial_socket="/tmp/s.sock", vars_path="/tmp/vars.fd"
    )
    assert argv[0] == D.DEFAULT_QEMU_BIN
    # A rendering GPU, not a plain framebuffer: COSMIC needs one.
    assert "virtio-gpu-gl-pci" in argv
    assert argv[argv.index("-vga") + 1] == "none"
    # The stick is removable USB storage and boots first.
    assert any(a.startswith("usb-storage") and "bootindex=1" in a for a in argv)
    assert any(a.startswith("usb-storage") and "removable=on" in a for a in argv)
    # The artifact is never written to.
    assert any("snapshot=on" in a for a in argv)
    # A socket, not a file: the check types on the console and reads the reply.
    assert any(a.startswith("unix:/tmp/s.sock") and "server=on" in a for a in argv)
    assert "-no-reboot" in argv


def test_session_argv_honours_the_size_knobs():
    argv = D.session_argv(
        "i.img", serial_socket="/tmp/s", vars_path="/tmp/v", ram_mb=8192, smp=8, accel="tcg"
    )
    assert argv[argv.index("-m") + 1] == "8192"
    assert argv[argv.index("-smp") + 1] == "8"
    assert argv[argv.index("-accel") + 1] == "tcg"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"image": "", "serial_socket": "/tmp/s", "vars_path": "/tmp/v"},
        {"image": "i.img", "serial_socket": "", "vars_path": "/tmp/v"},
        {"image": "i.img", "serial_socket": "/tmp/s", "vars_path": "/tmp/v", "ram_mb": 0},
        {"image": "i.img", "serial_socket": "/tmp/s", "vars_path": "/tmp/v", "smp": -1},
    ],
)
def test_session_argv_rejects_bad_inputs(kwargs):
    with pytest.raises(D.DesktopCheckError):
        D.session_argv(**kwargs)


# --- environment ------------------------------------------------------------


def test_session_env_drops_host_wayland_and_library_path():
    env = D.session_env(
        {"WAYLAND_DISPLAY": "wayland-0", "LD_LIBRARY_PATH": "/host/lib:", "HOME": "/home/x"},
        ":142",
    )
    assert "WAYLAND_DISPLAY" not in env
    assert "LD_LIBRARY_PATH" not in env
    assert env["HOME"] == "/home/x"
    # QEMU's sdl,gl=on must draw into the virtual X server, not the host desktop.
    assert env["DISPLAY"] == ":142"
    assert env["SDL_VIDEODRIVER"] == "x11"
    assert env["LIBGL_ALWAYS_SOFTWARE"] == "1"


def test_session_env_defaults_to_the_process_environment():
    env = D.session_env(None, ":9")
    assert env["DISPLAY"] == ":9"
    assert "LD_LIBRARY_PATH" not in env


# --- the report the guest produced ------------------------------------------


def test_parse_session_report_splits_the_two_sections():
    report = D.parse_session_report(_read(GOOD))
    assert report is not None
    assert "cosmic-comp" in report.ps and "cosmic-panel" in report.ps
    assert "pengyos-install" in report.ps
    # The real 26.10.7 boot reported no failed units.
    assert D.failed_units(report) == []


def test_parse_session_report_returns_none_without_the_markers():
    assert D.parse_session_report("pengyos-desktop login: root (automatic login)\n") is None
    assert D.parse_session_report("") is None
    # Start without end: a probe that was echoed but never answered.
    assert D.parse_session_report(f"{D.REPORT_START}\n      1 cosmic-comp\n") is None


def test_parse_session_report_uses_the_last_report():
    """A login loop can echo the probe more than once; the live answer wins."""
    stale = f"{D.REPORT_START}\n      1 cosmic-comp\n{D.REPORT_SPLIT}\n{D.REPORT_END}\n"
    fresh = (
        f"{D.REPORT_START}\n      1 cosmic-comp\n      1 cosmic-panel\n"
        f"      1 pengyos-install\n{D.REPORT_SPLIT}\n{D.REPORT_END}"
    )
    report = D.parse_session_report(stale + fresh)
    assert "pengyos-install" in report.ps


# --- the verdict ------------------------------------------------------------


def test_evaluate_session_passes_a_real_session_capture():
    outcome = D.evaluate_session(_read(GOOD))
    assert outcome.passed is True, outcome.failed_checks
    names = [c.name for c in outcome.checks]
    assert names == [
        "session-shell",
        "cosmic-comp-running",
        "cosmic-panel-running",
        "pengyos-install-running",
        "no-failed-units",
    ]
    assert all(c.status == PASS_STATUS for c in outcome.checks)
    # Evidence is a real ps line, not a bare word.
    comp = next(c for c in outcome.checks if c.name == "cosmic-comp-running")
    assert "cosmic-comp" in comp.evidence and comp.evidence.strip()[:1].isdigit()


def test_evaluate_session_fails_when_the_compositor_is_absent():
    """The 26.10.3 Desktop shape: everything else up, no compositor."""
    outcome = D.evaluate_session(_read(NO_COMP))
    assert outcome.passed is False
    assert outcome.failed_checks == ["cosmic-comp-running"]
    assert next(c for c in outcome.checks if c.name == "cosmic-comp-running").evidence is None


def test_evaluate_session_fails_on_a_unit_the_guest_reported_failed():
    outcome = D.evaluate_session(_read(FAILED_UNIT))
    assert outcome.passed is False
    assert outcome.failed_checks == ["no-failed-units"]
    check = next(c for c in outcome.checks if c.name == "no-failed-units")
    assert "cosmic-session.service" in check.evidence


def test_evaluate_session_reports_no_shell_as_an_error_not_a_check():
    """A refused console login means nothing was ever asked: there is no check
    set to report, and the verdict is a failure with a reason."""
    outcome = D.evaluate_session("pengyos-desktop login: root (automatic login)\nPermission denied\n")
    assert outcome.passed is False
    assert outcome.checks == ()
    assert outcome.report is None
    assert "no shell output" in outcome.error
    assert outcome.to_dict()["result"] == "fail"


def test_missing_processes_without_a_report_is_everything_missing():
    assert D.missing_processes(None) == list(D.REQUIRED_PROCESSES)


def test_saw_readiness_matches_text_markers_against_a_bytes_log():
    """The console log is bytes and the markers are text; mixing them is a
    TypeError, not a miss."""
    assert D.saw_readiness(b"pengyos-desktop login: \r\n") is True
    assert D.saw_readiness(b"Reached target Multi-User System\r\n") is False
    assert D.saw_readiness(b"root (automatic login)", ("automatic login",)) is True
    assert D.saw_readiness(b"") is False


def test_required_processes_are_the_session_itself():
    """These three are what 'the desktop is running' means here: the compositor,
    the panel, and the installer GUI that only exists on a stick."""
    assert D.REQUIRED_PROCESSES == ("cosmic-comp", "cosmic-panel", "pengyos-install")
    probe = D.SESSION_PROBE
    assert "systemctl --failed" in probe and "ps -eo comm" in probe


# --- the subcommand ---------------------------------------------------------


def test_desktop_parser_defaults():
    args = cli.build_parser().parse_args(["desktop", "stick.img"])
    assert args.image == "stick.img"
    assert args.out_prefix is None
    assert args.timeout == D.DEFAULT_SESSION_TIMEOUT
    assert args.settle == D.DEFAULT_SETTLE_SECONDS
    assert args.screenshot is True
    assert args.json_out is None


def test_desktop_parser_flags():
    args = cli.build_parser().parse_args(
        ["desktop", "s.img", "--out", "/o/p", "--timeout", "60", "--settle", "5",
         "--no-screenshot", "--json", "/o/j.json"]
    )
    assert args.out_prefix == "/o/p"
    assert args.timeout == 60.0 and args.settle == 5.0
    assert args.screenshot is False
    assert args.json_out == "/o/j.json"


def test_desktop_missing_image_is_a_usage_failure_not_a_crash(tmp_path, capsys):
    code = cli.main(["desktop", str(tmp_path / "nope.img"), "--out", str(tmp_path / "o")])
    assert code == cli.EXIT_USAGE
    assert "installer image not found" in capsys.readouterr().err


def test_desktop_missing_required_binary_is_reported(tmp_path, capsys, monkeypatch):
    img = tmp_path / "stick.img"
    img.write_bytes(b"\x00" * 1024)
    monkeypatch.setattr(D.shutil, "which", lambda name: None)
    code = cli.main(["desktop", str(img), "--out", str(tmp_path / "o")])
    assert code == cli.EXIT_USAGE
    assert "is not installed" in capsys.readouterr().err


# --- the plain live image: a session, without an installer ------------------
#
# 26.10.15 shipped a plain live desktop image that stopped at the greeter. The
# release script only ever session-checked the *installer* stick, and the check
# requires pengyos-install, which the plain image has no payload to run — so there
# was no way to check the image a person actually boots. The greeter fixture is a
# real capture of that boot (QEMU, 26.10.15): cosmic-comp is up because the greeter
# itself composites, but there is no session.

GREETER = FIXTURES / "serial_desktop_session_greeter_only.log"
LIVE_REQUIRED = cli.desktop_required(False)


def test_a_session_without_the_installer_passes_when_the_installer_is_not_required():
    """--no-installer must not weaken the check: it drops one process the image
    cannot legitimately run, and keeps the session as the claim."""
    outcome = D.evaluate_session(_read(GOOD), required=LIVE_REQUIRED)
    assert all(c.passed for c in outcome.checks), [c.name for c in outcome.checks if not c.passed]
    assert not any("pengyos-install" in c.name for c in outcome.checks), \
        "the check should not be made at all, not made and excused"


def test_a_greeter_still_fails_when_the_installer_is_not_required():
    """The negative control, from a real capture: a boot that reached the login
    screen and no session fails the live-image check exactly as it fails the
    installer check."""
    outcome = D.evaluate_session(_read(GREETER), required=LIVE_REQUIRED)
    failed = {c.name for c in outcome.checks if not c.passed}
    assert "cosmic-panel-running" in failed, failed
    assert not any("pengyos-install" in c.name for c in outcome.checks), failed


def test_the_installer_is_required_by_default():
    outcome = D.evaluate_session(_read(GREETER))
    failed = {c.name for c in outcome.checks if not c.passed}
    assert {"pengyos-install-running", "cosmic-panel-running"} <= failed, failed


def test_no_installer_flag_reaches_the_check():
    args = cli.build_parser().parse_args(["desktop", "/img/live.img", "--no-installer"])
    assert args.require_installer is False
    args = cli.build_parser().parse_args(["desktop", "/img/stick.img"])
    assert args.require_installer is True


def test_the_required_set_drops_the_installer_and_nothing_else():
    """The whole difference between the two images, stated exactly: one process is
    not applicable, and the session requirements are not negotiable."""
    full = cli.desktop_required(True)
    live = cli.desktop_required(False)
    assert full == D.REQUIRED_PROCESSES
    assert live == tuple(p for p in full if p != "pengyos-install")
    assert "cosmic-comp" in live and "cosmic-panel" in live, live
