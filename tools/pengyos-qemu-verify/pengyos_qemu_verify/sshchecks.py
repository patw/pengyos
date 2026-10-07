"""Out-of-band (SSH) checks for the PengyOS headless boot-test harness.

The serial-log assertions in :mod:`.assertions` can only prove what the guest
*prints*. Some facts are only visible from inside the running guest: whether a
unit failed (`systemctl --failed`), whether a service is active, whether the
Flatpak stack is present, what `ss` says about listening sockets. Those need a
second channel.

PengyOS images already provide one. A build with `PENGYOS_AUTHORIZED_KEYS_FILE`
injects a key into root's authorized_keys (see
``buildroot-external/board/core/post-build.sh``), the image's sshd is
key-only (`PermitRootLogin prohibit-password`), and the harness can forward a
host port into the guest (`--hostfwd tcp::2222-:22`, see
``docs/qemu-testing.md`` §4). This module runs declared commands over that
channel and turns each one into the *same* :class:`.assertions.CheckResult` the
serial-log runner produces, so an SSH check is reported, counted and gated
exactly like a log check.

Design notes
------------
* **Same result shape, deliberately.** An SSH check yields a
  :class:`.assertions.CheckResult` (`name`, `status`, `evidence`) so the JSON /
  JUnit emitters and the smoke verdict need no new branch. SSH results are not
  a second-class report section.
* **Pure core, injected runner.** :func:`run_ssh_checks` takes a *runner*
  callable that maps an argv to a :class:`CommandRun`. The unit suite injects a
  fake runner and never touches the network; the real runner is a thin
  `subprocess.run` over :func:`build_ssh_argv`. This mirrors how
  :mod:`.boot` injects `qemu_bin` / `swtpm_bin`.
* **The guest host key is throwaway.** A verification guest regenerates its SSH
  host key at build time, so there is nothing to compare against. The runner
  pins `StrictHostKeyChecking=accept-new` with a `UserKnownHostsFile` inside the
  boot's workdir: the key is recorded as evidence for that run instead of
  being silently ignored (`StrictHostKeyChecking=no`), and a *changed* key
  within the same run is still refused.
* **Batch mode, hard timeouts.** `BatchMode=yes` plus `ConnectTimeout` means a
  guest that never answers costs a bounded wait and a FAIL, never a hung
  harness. `PreferredAuthentications=publickey` keeps it from falling back to a
  password prompt it can never satisfy.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from .assertions import (
    FAIL_STATUS,
    KIND_ABSENT,
    KIND_PRESENT,
    MAX_EVIDENCE_CHARS,
    PASS_STATUS,
    CheckResult,
)

__all__ = [
    "KIND_EXIT_ZERO",
    "KIND_NOT_ZERO",
    "SSH_CHECK_KINDS",
    "DEFAULT_SSH_PORT",
    "DEFAULT_SSH_USER",
    "DEFAULT_SSH_HOST",
    "DEFAULT_SSH_CONNECT_TIMEOUT",
    "DEFAULT_SSH_COMMAND_TIMEOUT",
    "DEFAULT_SSH_RETRIES",
    "DEFAULT_SSH_RETRY_DELAY",
    "SSH_CONNECT_FAILURE_RC",
    "SshCheck",
    "SshTarget",
    "CommandRun",
    "SshError",
    "build_ssh_argv",
    "evaluate_ssh_check",
    "run_ssh_checks",
    "subprocess_runner",
]

# --- kinds ------------------------------------------------------------------
# `present` / `absent` are reused from .assertions so one vocabulary covers both
# channels: they match a pattern, just over command output instead of a log.
KIND_EXIT_ZERO: str = "exit-zero"
KIND_NOT_ZERO: str = "not-zero"

SSH_CHECK_KINDS: Tuple[str, ...] = (KIND_PRESENT, KIND_ABSENT, KIND_EXIT_ZERO, KIND_NOT_ZERO)

# --- defaults ---------------------------------------------------------------
DEFAULT_SSH_PORT: int = 2222
DEFAULT_SSH_USER: str = "root"
DEFAULT_SSH_HOST: str = "127.0.0.1"
DEFAULT_SSH_CONNECT_TIMEOUT: int = 10
DEFAULT_SSH_COMMAND_TIMEOUT: int = 30
DEFAULT_SSH_RETRIES: int = 3
DEFAULT_SSH_RETRY_DELAY: float = 2.0

# The rc a failed *connection* is reported as (ssh itself uses 255 for its own
# failures). A command that runs and exits non-zero keeps its real rc.
SSH_CONNECT_FAILURE_RC: int = 255


class SshError(ValueError):
    """A malformed SSH check or target. Raised pre-flight, before QEMU launches."""


@dataclass(frozen=True)
class SshCheck:
    """One command to run inside a booted guest, and what counts as passing.

    Attributes:
        name: identifier used in reports / JUnit `<testcase name=...>`.
        command: the shell command the guest runs (single string, quoted as one
            ssh argument).
        kind: one of :data:`SSH_CHECK_KINDS`. `present`/`absent` match
            ``pattern`` over the command's combined output; `exit-zero` passes
            when the command exits 0; `not-zero` passes when it exits non-zero.
        pattern: required for `present`/`absent`, ignored otherwise.
    """

    name: str
    command: str
    kind: str = KIND_EXIT_ZERO
    pattern: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.name or not str(self.name).strip():
            raise SshError("SshCheck.name must be a non-empty string")
        if not self.command or not str(self.command).strip():
            raise SshError(f"SshCheck.command for {self.name!r} must be a non-empty string")
        if self.kind not in SSH_CHECK_KINDS:
            raise SshError(
                f"SshCheck.kind for {self.name!r} must be one of "
                f"{', '.join(SSH_CHECK_KINDS)} (got {self.kind!r})"
            )
        if self.kind in (KIND_PRESENT, KIND_ABSENT) and not self.pattern:
            raise SshError(
                f"SshCheck.pattern for {self.name!r} is required when kind is "
                f"{self.kind!r}"
            )

    @classmethod
    def exit_zero(cls, name: str, command: str) -> "SshCheck":
        """The command must exit 0 (e.g. `systemctl is-active sshd`)."""
        return cls(name, command, KIND_EXIT_ZERO)

    @classmethod
    def absent(cls, name: str, command: str, pattern: str) -> "SshCheck":
        """The command's output must not contain ``pattern``."""
        return cls(name, command, KIND_ABSENT, pattern)

    @classmethod
    def present(cls, name: str, command: str, pattern: str) -> "SshCheck":
        """The command's output must contain ``pattern``."""
        return cls(name, command, KIND_PRESENT, pattern)


@dataclass(frozen=True)
class SshTarget:
    """Where and how to reach the booted guest."""

    host: str = DEFAULT_SSH_HOST
    port: int = DEFAULT_SSH_PORT
    user: str = DEFAULT_SSH_USER
    identity_file: Optional[str] = None
    known_hosts_file: Optional[str] = None
    connect_timeout: int = DEFAULT_SSH_CONNECT_TIMEOUT
    command_timeout: int = DEFAULT_SSH_COMMAND_TIMEOUT

    def __post_init__(self) -> None:
        if not self.host or not str(self.host).strip():
            raise SshError("SshTarget.host must be a non-empty string")
        if not isinstance(self.port, int) or self.port <= 0 or self.port > 65535:
            raise SshError(f"SshTarget.port must be 1..65535 (got {self.port!r})")
        if not self.user or not str(self.user).strip():
            raise SshError("SshTarget.user must be a non-empty string")
        for label, value in (
            ("connect_timeout", self.connect_timeout),
            ("command_timeout", self.command_timeout),
        ):
            if not isinstance(value, int) or value <= 0:
                raise SshError(f"SshTarget.{label} must be a positive integer (got {value!r})")


@dataclass(frozen=True)
class CommandRun:
    """What one guest command produced.

    ``rc`` is :data:`SSH_CONNECT_FAILURE_RC` with ``error`` set when the command
    never ran (connection refused, banner timeout, missing ssh binary).
    """

    returncode: int
    stdout: str = ""
    stderr: str = ""
    error: str = ""

    @property
    def output(self) -> str:
        """Combined output, the text `present`/`absent` match against."""
        return f"{self.stdout}{self.stderr}"

    @property
    def connected(self) -> bool:
        """False when the command never reached the guest."""
        return not self.error


def _clip(text: str) -> str:
    """Strip and truncate evidence to the harness-wide cap."""
    text = (text or "").strip()
    return text[:MAX_EVIDENCE_CHARS] or ""


def _first_line(text: str) -> str:
    """The first non-empty line of ``text`` (empty string when there is none)."""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def build_ssh_argv(
    check: SshCheck,
    target: SshTarget,
    *,
    ssh_bin: str = "ssh",
) -> List[str]:
    """Render the `ssh` argv that runs ``check.command`` in the guest.

    Pure: no I/O, so the exact options are assertable as golden strings.

    The options are the ones ``docs/qemu-testing.md`` §4 documents for a
    verification guest, plus the guards a harness needs: `BatchMode=yes` and
    `PreferredAuthentications=publickey` (the image's sshd is key-only, so a
    prompt would hang forever), and a `UserKnownHostsFile` in the boot's workdir
    with `accept-new` rather than `no` — a throwaway guest key is recorded, not
    ignored.
    """
    if not ssh_bin or not str(ssh_bin).strip():
        raise SshError("ssh_bin must be a non-empty string")
    argv: List[str] = [ssh_bin, "-o", "BatchMode=yes"]
    known_hosts = target.known_hosts_file or "/dev/null"
    argv += [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "PreferredAuthentications=publickey",
        "-o", f"ConnectTimeout={target.connect_timeout}",
    ]
    if target.identity_file:
        argv += ["-i", target.identity_file]
    argv += ["-p", str(target.port), f"{target.user}@{target.host}", check.command]
    return argv


def _match_line(output: str, pattern: str) -> Tuple[Optional[str], bool]:
    """Return (line containing the first match, matched?) over command output."""
    import re

    match = re.search(pattern, output)
    if match is None:
        return None, False
    start = output.rfind("\n", 0, match.start()) + 1
    end = output.find("\n", match.end())
    if end == -1:
        end = len(output)
    line = output[start:end].strip()
    return (line or None), True


def evaluate_ssh_check(check: SshCheck, run: CommandRun) -> CheckResult:
    """Turn one command's :class:`CommandRun` into a :class:`CheckResult`.

    A command that never reached the guest always FAILS, with the connection
    error as evidence — a refused banner is not a passing `absent`.
    """
    if not run.connected:
        detail = run.error or f"ssh failed with rc={run.returncode}"
        return CheckResult(check.name, FAIL_STATUS, _clip(f"{check.command} -> {detail}") or None)

    if check.kind == KIND_EXIT_ZERO:
        if run.returncode == 0:
            evidence = _first_line(run.stdout) or f"{check.command} -> rc=0"
            return CheckResult(check.name, PASS_STATUS, _clip(evidence) or None)
        detail = _first_line(run.stderr) or _first_line(run.stdout) or f"rc={run.returncode}"
        return CheckResult(check.name, FAIL_STATUS, _clip(f"{check.command} -> {detail}") or None)

    if check.kind == KIND_NOT_ZERO:
        if run.returncode != 0:
            detail = _first_line(run.stderr) or _first_line(run.stdout) or f"rc={run.returncode}"
            return CheckResult(check.name, PASS_STATUS, _clip(f"{check.command} -> {detail}") or None)
        return CheckResult(
            check.name, FAIL_STATUS, _clip(f"{check.command} -> rc=0, expected non-zero") or None
        )

    line, matched = _match_line(run.output, check.pattern or "")
    if check.kind == KIND_PRESENT:
        if matched:
            return CheckResult(check.name, PASS_STATUS, _clip(line or "") or None)
        return CheckResult(
            check.name, FAIL_STATUS,
            _clip(f"{check.command} -> rc={run.returncode}, no match for {check.pattern!r}") or None,
        )
    # KIND_ABSENT
    if matched:
        return CheckResult(check.name, FAIL_STATUS, _clip(line or "") or None)
    return CheckResult(
        check.name, PASS_STATUS,
        _clip(f"{check.command} -> rc={run.returncode}, no {check.pattern!r} in output") or None,
    )


def subprocess_runner(
    target: SshTarget,
    *,
    ssh_bin: str = "ssh",
) -> Callable[[SshCheck], CommandRun]:
    """The real runner: `subprocess.run` over :func:`build_ssh_argv`.

    A timeout or a missing binary becomes a :class:`CommandRun` with
    :data:`SSH_CONNECT_FAILURE_RC` and an ``error``, so the caller still gets a
    FAIL with evidence instead of an exception mid-report.
    """

    def run(check: SshCheck) -> CommandRun:
        argv = build_ssh_argv(check, target, ssh_bin=ssh_bin)
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=target.command_timeout,
            )
        except subprocess.TimeoutExpired:
            return CommandRun(
                SSH_CONNECT_FAILURE_RC,
                error=f"timed out after {target.command_timeout}s",
            )
        except FileNotFoundError:
            return CommandRun(SSH_CONNECT_FAILURE_RC, error=f"ssh binary not found: {ssh_bin!r}")
        except OSError as exc:
            return CommandRun(SSH_CONNECT_FAILURE_RC, error=f"ssh failed: {exc}")
        return CommandRun(proc.returncode, proc.stdout, proc.stderr)

    return run


# What a failed *connection* looks like in ssh's own output. A command that ran
# and exited non-zero is a real answer and is never retried.
_CONNECT_MARKERS: Tuple[str, ...] = (
    "Connection refused",
    "Connection timed out",
    "Connection closed by",
    "No route to host",
    "Connection reset by peer",
    "banner exchange",
    "Could not resolve hostname",
    "Permission denied",
)


def _looks_like_connect_failure(run: CommandRun) -> bool:
    if run.error:
        return True
    if run.returncode != SSH_CONNECT_FAILURE_RC:
        return False
    text = f"{run.stdout}{run.stderr}"
    return any(marker in text for marker in _CONNECT_MARKERS)


def run_ssh_checks(
    checks: Sequence[SshCheck],
    target: SshTarget,
    *,
    runner: Optional[Callable[[SshCheck], CommandRun]] = None,
    ssh_bin: str = "ssh",
    retries: int = DEFAULT_SSH_RETRIES,
    retry_delay: float = DEFAULT_SSH_RETRY_DELAY,
    sleep: Callable[[float], None] = time.sleep,
) -> List[CheckResult]:
    """Run every SSH check against a live guest and return results in order.

    Args:
        checks: the :class:`SshCheck` list. Empty is allowed and yields [].
        target: where the guest is reachable.
        runner: a callable mapping an :class:`SshCheck` to a :class:`CommandRun`.
            ``None`` uses :func:`subprocess_runner`. Tests inject a fake so the
            suite needs no network, no guest and no root.
        retries: extra attempts when the *connection* fails (a guest whose sshd
            is still coming up answers refused/timed-out). A command that ran
            and exited non-zero is an answer, never retried.
        sleep: injectable for tests, so a retry path is testable without waiting.

    Returns:
        One :class:`.assertions.CheckResult` per check, in input order.
    """
    active: Callable[[SshCheck], CommandRun] = runner or subprocess_runner(target, ssh_bin=ssh_bin)
    results: List[CheckResult] = []
    for check in checks:
        run = active(check)
        attempt = 0
        while run is not None and _looks_like_connect_failure(run) and attempt < retries:
            attempt += 1
            sleep(retry_delay)
            run = active(check)
        results.append(evaluate_ssh_check(check, run))
    return results


def default_ssh_checks() -> Tuple[SshCheck, ...]:
    """The checks worth running in every PengyOS guest, over any edition.

    Grounded in what the serial log cannot show: a unit that failed *after* the
    log stopped being read, and whether sshd — the channel itself — is healthy.
    """
    return (
        SshCheck(
            "ssh-no-failed-units",
            "systemctl --failed --no-pager --plain",
            KIND_ABSENT,
            r"loaded failed",
        ),
        SshCheck.exit_zero("ssh-sshd-active", "systemctl is-active sshd"),
    )
