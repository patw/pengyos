"""Tests for the SSH (in-guest) check lane.

The suite needs no network, no guest and no root: the command runner is
injected, and the QEMU side reuses the fake qemu the boot tests already use.
Every check kind is mutation-checked — a check that cannot fail is a bug, so the
suite proves each one fails on the bad answer it exists to catch.
"""

import os

import pytest

from pengyos_qemu_verify.assertions import CheckResult, FAIL_STATUS, PASS_STATUS
from pengyos_qemu_verify.boot import _ssh_hostfwd, boot_image
from pengyos_qemu_verify.cli import (
    _add_ssh_flags,
    _hostfwd_for_run,
    _ssh_check_name,
    _ssh_checks_for,
    _ssh_requested,
    _ssh_target_for,
)
from pengyos_qemu_verify.sshchecks import (
    DEFAULT_SSH_PORT,
    SSH_CONNECT_FAILURE_RC,
    CommandRun,
    SshCheck,
    SshError,
    SshTarget,
    build_ssh_argv,
    default_ssh_checks,
    evaluate_ssh_check,
    run_ssh_checks,
)

# A guest command that reports one failed unit, in systemd's real shape.
FAILED_UNITS_OUTPUT = """  UNIT            LOAD   ACTIVE SUB    DESCRIPTION
● apparmor.service loaded failed failed AppArmor profiles
"""

# The shipped default check for "no failed units", by its real command string.
FAILED_UNITS_COMMAND = default_ssh_checks()[0].command


def _runner(**answers):
    """Build an injected runner: command -> CommandRun, recording the calls."""
    calls = []

    def runner(check):
        calls.append(check.command)
        return answers.get(check.command, CommandRun(0, "ok\n"))

    runner.calls = calls
    return runner


# ==========================================================================
# argv construction (golden strings)
# ==========================================================================


def test_build_ssh_argv_golden():
    check = SshCheck.exit_zero("flatpak", "flatpak --version")
    target = SshTarget(port=2222, known_hosts_file="/work/guest-known_hosts")
    argv = build_ssh_argv(check, target)
    assert argv == [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "UserKnownHostsFile=/work/guest-known_hosts",
        "-o", "PreferredAuthentications=publickey",
        "-o", "ConnectTimeout=10",
        "-p", "2222",
        "root@127.0.0.1",
        "flatpak --version",
    ]


def test_build_ssh_argv_pins_identity_only_when_given():
    target = SshTarget()
    assert "-i" not in build_ssh_argv(SshCheck.exit_zero("a", "true"), target)
    with_key = build_ssh_argv(
        SshCheck.exit_zero("a", "true"), SshTarget(identity_file="/keys/id")
    )
    assert with_key[with_key.index("-i") + 1] == "/keys/id"


def test_build_ssh_argv_records_guest_key_in_the_workdir_not_none():
    """`accept-new` + a real known_hosts file: the throwaway key is recorded."""
    argv = build_ssh_argv(
        SshCheck.exit_zero("a", "true"), SshTarget(known_hosts_file="/w/kh")
    )
    joined = " ".join(argv)
    assert "StrictHostKeyChecking=accept-new" in joined
    assert "UserKnownHostsFile=/w/kh" in joined
    # Mutation: silently ignoring the host key is not acceptable.
    assert "StrictHostKeyChecking=no" not in joined


def test_build_ssh_argv_keeps_the_command_as_one_argument():
    argv = build_ssh_argv(SshCheck.exit_zero("a", "systemctl --failed --no-pager"), SshTarget())
    assert argv[-1] == "systemctl --failed --no-pager"


# ==========================================================================
# validation (pre-flight, before anything launches)
# ==========================================================================


@pytest.mark.parametrize(
    "kwargs,needle",
    [
        ({"name": "", "command": "true"}, "non-empty"),
        ({"name": "x", "command": "  "}, "non-empty"),
        ({"name": "x", "command": "true", "kind": "maybe"}, "must be one of"),
        ({"name": "x", "command": "true", "kind": "present"}, "required"),
    ],
)
def test_ssh_check_rejects_malformed(kwargs, needle):
    with pytest.raises(SshError) as exc:
        SshCheck(**kwargs)
    assert needle in str(exc.value)


@pytest.mark.parametrize(
    "kwargs,needle",
    [
        ({"port": 0}, "1..65535"),
        ({"port": 70000}, "1..65535"),
        ({"user": ""}, "non-empty"),
        ({"connect_timeout": 0}, "positive"),
    ],
)
def test_ssh_target_rejects_malformed(kwargs, needle):
    with pytest.raises(SshError) as exc:
        SshTarget(**kwargs)
    assert needle in str(exc.value)


# ==========================================================================
# evaluation: each kind must fail on the answer it exists to catch
# ==========================================================================


def test_exit_zero_passes_on_zero_and_fails_on_nonzero():
    check = SshCheck.exit_zero("sshd", "systemctl is-active sshd")
    ok = evaluate_ssh_check(check, CommandRun(0, "active\n"))
    assert ok.status == PASS_STATUS and ok.evidence == "active"
    bad = evaluate_ssh_check(check, CommandRun(1, "", "inactive\n"))
    assert bad.status == FAIL_STATUS and "inactive" in bad.evidence


def test_absent_fails_when_the_pattern_shows_up():
    """The mutation this check exists for: a failed unit must fail it."""
    check = SshCheck("no-failed-units", FAILED_UNITS_COMMAND, "absent", r"loaded failed")
    clean = evaluate_ssh_check(check, CommandRun(0, "  UNIT  LOAD  ACTIVE\n"))
    assert clean.status == PASS_STATUS
    dirty = evaluate_ssh_check(check, CommandRun(0, FAILED_UNITS_OUTPUT))
    assert dirty.status == FAIL_STATUS
    assert "apparmor.service loaded failed failed" in dirty.evidence


def test_present_fails_when_the_pattern_is_missing():
    check = SshCheck("flatpak", "flatpak --version", "present", r"1\.4")
    assert evaluate_ssh_check(check, CommandRun(0, "1.4.4\n")).status == PASS_STATUS
    missing = evaluate_ssh_check(check, CommandRun(0, "1.2.0\n"))
    assert missing.status == FAIL_STATUS and "no match" in missing.evidence


def test_not_zero_passes_on_failure_and_fails_on_success():
    check = SshCheck("no-core-dumps", "grep -q panic /run/x", "not-zero")
    assert evaluate_ssh_check(check, CommandRun(1)).status == PASS_STATUS
    assert evaluate_ssh_check(check, CommandRun(0)).status == FAIL_STATUS


def test_a_dead_channel_never_passes_an_absent_check():
    """Mutation: 'no match' because ssh never connected is not a pass."""
    check = SshCheck("no-failed-units", FAILED_UNITS_COMMAND, "absent", r"loaded failed")
    dead = CommandRun(SSH_CONNECT_FAILURE_RC, "", "", error="Connection refused")
    result = evaluate_ssh_check(check, dead)
    assert result.status == FAIL_STATUS
    assert "Connection refused" in result.evidence


def test_evidence_is_bounded_like_the_log_checks():
    check = SshCheck("x", "cat big", "present", "needle")
    huge = "needle " + "z" * 4000
    result = evaluate_ssh_check(check, CommandRun(0, huge))
    assert len(result.evidence) <= 512


# ==========================================================================
# run_ssh_checks: order, retries, and what is never retried
# ==========================================================================


def test_run_ssh_checks_preserves_order_and_shape():
    checks = [
        SshCheck.exit_zero("a", "true"),
        SshCheck("b", FAILED_UNITS_COMMAND, "absent", "loaded failed"),
    ]
    results = run_ssh_checks(
        checks, SshTarget(), runner=_runner(**{FAILED_UNITS_COMMAND: CommandRun(0, "clean\n")})
    )
    assert [r.name for r in results] == ["a", "b"]
    assert all(isinstance(r, CheckResult) for r in results)
    assert all(r.status == PASS_STATUS for r in results)


def test_empty_check_list_yields_no_results():
    assert run_ssh_checks([], SshTarget(), runner=_runner()) == []


def test_connect_failure_is_retried_then_passes_when_the_guest_catches_up():
    """sshd can still be coming up when readiness fires: retry the connection."""
    attempts = {"n": 0}

    def flaky(check):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return CommandRun(SSH_CONNECT_FAILURE_RC, "", "", error="Connection refused")
        return CommandRun(0, "active\n")

    slept = []
    result = run_ssh_checks(
        [SshCheck.exit_zero("sshd", "systemctl is-active sshd")],
        SshTarget(),
        runner=flaky,
        retries=2,
        retry_delay=0.01,
        sleep=slept.append,
    )
    assert result[0].status == PASS_STATUS
    assert attempts["n"] == 2 and len(slept) == 1


def test_a_real_nonzero_answer_is_never_retried():
    """A command that ran and failed is an answer, not a connection problem."""
    attempts = {"n": 0}

    def runner(check):
        attempts["n"] += 1
        return CommandRun(1, "", "inactive\n")

    result = run_ssh_checks(
        [SshCheck.exit_zero("sshd", "systemctl is-active sshd")],
        SshTarget(),
        runner=runner,
        retries=3,
        sleep=lambda _s: None,
    )
    assert result[0].status == FAIL_STATUS
    assert attempts["n"] == 1


def test_default_ssh_checks_are_the_two_facts_the_log_cannot_show():
    names = [c.name for c in default_ssh_checks()]
    assert names == ["ssh-no-failed-units", "ssh-sshd-active"]


# ==========================================================================
# boot wiring: the forward, and results landing in result.checks
# ==========================================================================


def test_ssh_hostfwd_adds_the_forward_only_when_missing():
    target = SshTarget(port=2222)
    assert _ssh_hostfwd([], target) == ["tcp::2222-:22"]
    assert _ssh_hostfwd(["tcp::2222-:22"], target) == ["tcp::2222-:22"]
    assert _ssh_hostfwd(["tcp::2223-:22"], target) == ["tcp::2223-:22", "tcp::2222-:22"]


def _make_image(tmp_path, name="disk.img"):
    img = tmp_path / name
    img.write_bytes(b"\x00" * 4096)
    return str(img)


FAKE_QEMU = r'''#!/usr/bin/env python3
import os, sys, time
argv = sys.argv[1:]
serial = argv[argv.index("-serial") + 1].split(":", 1)[1]
with open(serial, "w") as fh:
    fh.write("pengyos login: ")
time.sleep(float(os.environ.get("FAKE_QEMU_SLEEP", "0.2")))
'''


def _write_exec(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    os.chmod(path, 0o755)
    return str(path)


def test_boot_appends_ssh_checks_after_the_log_checks(tmp_path, monkeypatch):
    qemu = _write_exec(tmp_path, "fake-qemu", FAKE_QEMU)
    monkeypatch.setenv("FAKE_QEMU_SLEEP", "0.2")
    runner = _runner(
        **{
            FAILED_UNITS_COMMAND: CommandRun(0, "  UNIT  LOAD  ACTIVE SUB\n"),
            "systemctl is-active sshd": CommandRun(0, "active\n"),
        }
    )
    result = boot_image(
        _make_image(tmp_path),
        kvm=False,
        poll_interval=0.02,
        timeout=10.0,
        qemu_bin=qemu,
        assertions=[],
        ssh_checks=default_ssh_checks(),
        ssh_runner=runner,
    )
    assert [c.name for c in result.checks] == ["ssh-no-failed-units", "ssh-sshd-active"]
    assert all(c.status == PASS_STATUS for c in result.checks)
    # The forward the checks need is in the argv that actually launched.
    assert "-nic" in result.argv and "hostfwd=tcp::2222-:22" in result.argv[result.argv.index("-nic") + 1]


def test_boot_fails_ssh_checks_when_the_guest_never_gets_to_login(tmp_path, monkeypatch):
    """A timed-out boot still owes every requested SSH check a FAIL with a reason."""
    qemu = _write_exec(tmp_path, "fake-qemu", "#!/usr/bin/env python3\nimport time\ntime.sleep(3)\n")
    monkeypatch.setenv("FAKE_QEMU_SLEEP", "0.1")
    runner = _runner()
    result = boot_image(
        _make_image(tmp_path),
        kvm=False,
        poll_interval=0.02,
        timeout=0.4,
        qemu_bin=qemu,
        assertions=[],
        ssh_checks=default_ssh_checks(),
        ssh_runner=runner,
    )
    assert [c.name for c in result.checks] == ["ssh-no-failed-units", "ssh-sshd-active"]
    assert all(c.status == FAIL_STATUS for c in result.checks)
    assert all("readiness" in (c.evidence or "") for c in result.checks)
    # Nothing was run inside a guest that never came up.
    assert runner.calls == []


def test_boot_reports_a_failed_unit_as_a_failed_check(tmp_path, monkeypatch):
    """The point of the lane: a unit systemd failed is visible only from inside."""
    qemu = _write_exec(tmp_path, "fake-qemu", FAKE_QEMU)
    monkeypatch.setenv("FAKE_QEMU_SLEEP", "0.2")
    runner = _runner(**{FAILED_UNITS_COMMAND: CommandRun(0, FAILED_UNITS_OUTPUT)})
    result = boot_image(
        _make_image(tmp_path),
        kvm=False,
        poll_interval=0.02,
        timeout=10.0,
        qemu_bin=qemu,
        assertions=[],
        ssh_checks=default_ssh_checks(),
        ssh_runner=runner,
    )
    failed = [c for c in result.checks if c.status == FAIL_STATUS]
    assert [c.name for c in failed] == ["ssh-no-failed-units"]
    assert "apparmor.service" in failed[0].evidence


def test_boot_without_ssh_checks_is_unchanged(tmp_path, monkeypatch):
    """No --ssh means no new argv tokens: the pre-existing goldens stay valid."""
    qemu = _write_exec(tmp_path, "fake-qemu", FAKE_QEMU)
    monkeypatch.setenv("FAKE_QEMU_SLEEP", "0.1")
    result = boot_image(
        _make_image(tmp_path), kvm=False, poll_interval=0.02, timeout=10.0, qemu_bin=qemu
    )
    assert "-nic" not in result.argv
    assert [c.name for c in result.checks] == ["reached-login", "no-kernel-panic"]


# ==========================================================================
# CLI surface
# ==========================================================================


def _parse(argv):
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--hostfwd", action="append", default=None)
    _add_ssh_flags(parser)
    return parser.parse_args(argv)


def test_cli_ssh_flags_defaults():
    args = _parse([])
    assert _ssh_requested(args) is False
    assert args.ssh_port == DEFAULT_SSH_PORT
    assert args.ssh_user == "root"


def test_cli_ssh_command_implies_the_lane_and_names_the_check():
    args = _parse(["--ssh-command", "flatpak --version"])
    assert _ssh_requested(args) is True
    checks = _ssh_checks_for(args)
    assert [c.name for c in checks] == ["ssh:flatpak-version"]
    assert checks[0].command == "flatpak --version"
    assert _ssh_check_name("systemctl is-active sshd") == "ssh:systemctl-is-active-sshd"


def test_cli_ssh_adds_the_default_checks_and_the_forward():
    args = _parse(["--ssh"])
    checks = _ssh_checks_for(args)
    assert [c.name for c in checks] == ["ssh-no-failed-units", "ssh-sshd-active"]
    fwd = _hostfwd_for_run(args)
    assert fwd == ("tcp::2222-:22",)


def test_cli_keeps_an_explicit_hostfwd_and_adds_only_what_is_missing():
    args = _parse(["--ssh", "--hostfwd", "tcp::2222-:22", "--ssh-port", "2222"])
    assert _hostfwd_for_run(args) == ("tcp::2222-:22",)
    args2 = _parse(["--ssh", "--hostfwd", "tcp::8080-:80"])
    assert _hostfwd_for_run(args2) == ("tcp::8080-:80", "tcp::2222-:22")


def test_cli_without_ssh_leaves_hostfwd_alone():
    args = _parse(["--hostfwd", "tcp::8080-:80"])
    assert _ssh_requested(args) is False
    assert _hostfwd_for_run(args) == ("tcp::8080-:80",)
    assert _hostfwd_for_run(_parse([])) is None


def test_cli_ssh_target_honours_port_user_and_key(tmp_path, monkeypatch):
    key = tmp_path / "id"
    key.write_text("ssh-ed25519 AAAA test\n")
    monkeypatch.setenv("PENGYOS_SSH_KEY", str(key))
    args = _parse(["--ssh", "--ssh-port", "2223", "--ssh-user", "root"])
    target = _ssh_target_for(args)
    assert target.port == 2223 and target.user == "root"
    assert target.identity_file == str(key)
