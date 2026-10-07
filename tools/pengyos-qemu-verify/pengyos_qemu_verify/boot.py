"""Headless QEMU boot execution for the PengyOS boot-test harness.

Actually *run* a headless
boot. Given a disk image and the same inputs as :func:`.argv.build_argv`, it:

1. validates the image exists (and is readable) — ``build_argv`` stays pure,
   so existence checks live here;
2. prepares a per-boot workdir (serial log, writable OVMF VARS copy, swtpm
   state);
3. optionally starts ``swtpm`` (item 4's :class:`.tpm.Swtpm`) and attaches its
   datapath socket to the argv;
4. launches QEMU and *streams* the ``-serial file:`` log (incremental reads —
   the serial log is the readiness channel);
5. polls the accumulated log against the readiness regex list (default
   ``('login:',)``) — ready when **all** specified markers have been seen;
6. enforces ``--timeout``: the harness never hangs past the deadline (on
   timeout it terminates QEMU — SIGTERM, SIGKILL on a grace timeout — and
   reports ``timed_out=True``);
7. on readiness it terminates QEMU (the guest is sitting at a login prompt
   that the harness does not drive yet — see item 6 for assertions) and
   records the exit code;
8. tears down swtpm, and returns a :class:`BootResult` capturing the argv,
   accelerator, duration, return code, per-marker evidence and errors.

Nothing here needs a real QEMU for the unit suite: the QEMU binary is
injectable (``qemu_bin=``), so tests drive the real code path with a tiny
*fake* ``qemu-system-x86_64`` Python script that parses its own argv and
appends realistic serial output to the ``-serial file:`` log.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, replace

from . import argv as _argv
from .assertions import (
    FAIL_STATUS,
    Assertion,
    CheckResult,
    default_assertions,
    run_assertions,
)
from .sshchecks import SshCheck, SshError, SshTarget, run_ssh_checks
from . import tpm as _tpm
from .tpm import SWTPM_BIN, Swtpm
from .tpm_state import TpmStateProvision, provision_tpm_state

__all__ = [
    "DEFAULT_EXPECT",
    "DEFAULT_TIMEOUT",
    "DEFAULT_SETTLE_SECONDS",
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_TERMINATE_GRACE",
    "MarkerResult",
    "BootResult",
    "BootError",
    "ImageNotFound",
    "find_qemu_bin",
    "detect_readiness",
    "read_log_incremental",
    "boot_image",
]

# Documented interface defaults (spec.md) — same values the CLI uses.
DEFAULT_EXPECT: str = "login:"
DEFAULT_TIMEOUT: float = 120.0
DEFAULT_POLL_INTERVAL: float = 0.2  # seconds between serial-log polls
DEFAULT_TERMINATE_GRACE: float = 2.0  # SIGTERM grace before SIGKILL
# Seconds to keep reading the serial log *after* readiness is seen. The
# readiness marker is the login prompt; the autologin banner and the shell
# prompt land a second or two later, so a check that has to prove the login
# actually produced a working shell can only see them if the harness keeps
# listening. 0 reproduces the old stop-on-readiness behaviour.
DEFAULT_SETTLE_SECONDS: float = 4.0
DEFAULT_SERIAL_LOG_NAME: str = "pengyos-serial.log"
DEFAULT_VARS_COPY_NAME: str = "pengyos-ovmf-vars.fd"
MAX_EVIDENCE_CHARS: int = 512


class BootError(RuntimeError):
    """Base error for boot failures raised before/without a BootResult."""


class ImageNotFound(BootError):
    """Raised when the disk image to boot does not exist or is unreadable."""


@dataclass(frozen=True)
class MarkerResult:
    """Outcome of one readiness regex against the serial log so far."""

    pattern: str
    matched: bool
    evidence: "str | None" = None  # the log line containing the first match

    def to_dict(self) -> "dict":
        return {
            "pattern": self.pattern,
            "matched": self.matched,
            "evidence": self.evidence,
        }


@dataclass
class BootResult:
    """Everything the harness knows about one boot attempt.

    Consumed by item 7 (JUnit XML / JSON emitters); its ``to_dict()`` mirrors
    the spec's JSON shape (image, accel, duration, per-check evidence,
    overall result) so the emitters can build on it directly.
    """

    image: str
    argv: "list[str]" = field(default_factory=list)
    kvm: bool = False
    ready: bool = False
    timed_out: bool = False
    returncode: "int | None" = None
    duration: float = 0.0
    serial_log: str = ""
    markers: "list[MarkerResult]" = field(default_factory=list)
    checks: "list[CheckResult]" = field(default_factory=list)
    error: "str | None" = None
    tpm_state: str = ""  # provisioned TPM state dir ('' when --tpm not used)
    tpm_state_method: str = ""  # 'existing' | 'swtpm_setup' | 'fixture'
    tpm_protocol: str = ""  # 'control' | 'legacy' ('' when --tpm not used)
    snapshot: bool = True  # guest disk attached via a throwaway overlay

    @property
    def accel(self) -> str:
        """``'kvm'`` when KVM acceleration was requested, else ``'tcg'``."""
        return "kvm" if self.kvm else "tcg"

    @property
    def ok(self) -> bool:
        """True when readiness was reached and the harness itself had no error."""
        return self.ready and not self.timed_out and self.error is None

    @property
    def checks_passed(self) -> bool:
        """True when every attached :attr:`checks` result passed.

        Vacuously True for an empty check list (the same convention as
        ``assertions.all_passed``). The *overall* verdict (boot ready AND all
        checks passed) is the emitters' :func:`.reports.overall_result`;
        :attr:`ok` deliberately stays a boot-level property and does not look
        at the checks.
        """
        return all(c.passed for c in self.checks)

    @property
    def status(self) -> str:
        """One of ``'ready'`` / ``'timeout'`` / ``'failed'``."""
        if self.error is not None:
            return "failed"
        if self.ready:
            return "ready"
        if self.timed_out:
            return "timeout"
        return "failed"

    def to_dict(self) -> "dict":
        return {
            "image": self.image,
            "accel": self.accel,
            "duration": round(self.duration, 3),
            "returncode": self.returncode,
            "ready": self.ready,
            "timed_out": self.timed_out,
            "serial_log": self.serial_log,
            "snapshot": self.snapshot,
            "markers": [m.to_dict() for m in self.markers],
            "checks": [c.to_dict() for c in self.checks],
            "error": self.error,
            "tpm_state": self.tpm_state,
            "tpm_state_method": self.tpm_state_method,
            "tpm_protocol": self.tpm_protocol,
            "result": self.status,
        }


def _ssh_hostfwd(hostfwd: "list[str]", target: SshTarget) -> "list[str]":
    """Return ``hostfwd`` with a forward into the guest's sshd added if missing.

    An SSH check with no port forwarded is a guaranteed FAIL, and the failure
    looks like a broken image rather than a missing flag. So when a caller asks
    for SSH checks, the forward for that target's host port is added here.
    Booters that pass no ``ssh_checks`` are untouched, keeping the argv goldens.
    """
    needed = f"tcp::{target.port}-:22"
    for spec in hostfwd:
        if spec.split("-")[0].endswith(f":{target.port}"):
            return hostfwd
    return [*hostfwd, needed]


def _validate_assertions(assertions: "tuple[Assertion, ...] | list[Assertion]") -> None:
    """Pre-flight validation of a check set (no I/O, no boot started).

    Catches malformed :class:`.assertions.Assertion` objects (their
    ``__post_init__`` already raises ``ValueError``) and patterns that do not
    compile (``re.error``), re-raising the latter as ``ValueError`` so the
    CLI's pre-flight error path ("always writes JSON", exit ``1``) covers it
    uniformly — an uncompilable check must never be discovered *after* a
    real boot.

    Raises:
        ValueError: on a malformed check or a pattern that does not compile.
    """
    for assertion in assertions:
        if not isinstance(assertion, Assertion):
            raise ValueError(
                f"assertions must be Assertion instances (got {type(assertion).__name__})"
            )
        try:
            re.compile(assertion.pattern)
        except re.error as exc:
            raise ValueError(
                f"assertion {assertion.name!r} pattern does not compile: {exc}"
            ) from exc


def find_qemu_bin() -> str:
    """Return the QEMU binary to use: a real one on ``PATH`` if present, else
    the plain name (which ``subprocess`` will resolve at launch).

    A missing binary is NOT an error here: launching surfaces it as a failed
    :class:`BootResult` (``error='QEMU binary not found …'``), so callers —
    and the item 7 emitters — always get a result to report.
    """
    return shutil.which(_argv.DEFAULT_QEMU_BIN) or _argv.DEFAULT_QEMU_BIN


def detect_readiness(
    log_text: str,
    patterns: "tuple[str, ...] | list[str]",
) -> "tuple[bool, list[MarkerResult]]":
    """Evaluate the readiness regex list against a serial-log snapshot.

    Each pattern is compiled as a plain Python regular expression and searched
    anywhere in the accumulated log (the console interleaves newlines,
    backspaces and CRs; a substring-style ``search`` is deliberately tolerant).
    On a match the evidence is the *line* containing the first occurrence,
    stripped and truncated to :data:`MAX_EVIDENCE_CHARS`.

    The boot is **ready when every specified pattern has matched** (with the
    default single-marker list this is the familiar "I see ``login:``").

    Args:
        log_text: the serial log text accumulated so far (may be empty).
        patterns: the readiness regex list; must be a non-empty list of
            strings that compile as regular expressions.

    Returns:
        ``(ready, markers)`` where ``markers`` has one :class:`MarkerResult`
        per pattern, in input order.

    Raises:
        ValueError: if ``patterns`` is empty.
        re.error: if a pattern does not compile.
    """
    if not patterns:
        raise ValueError("the readiness pattern list must not be empty")
    markers: "list[MarkerResult]" = []
    for pattern in patterns:
        match = re.search(pattern, log_text)
        if match is None:
            markers.append(MarkerResult(pattern, False, None))
            continue
        start = log_text.rfind("\n", 0, match.start()) + 1
        end = log_text.find("\n", match.end())
        if end == -1:
            end = len(log_text)
        line = log_text[start:end].strip()
        markers.append(MarkerResult(pattern, True, line[:MAX_EVIDENCE_CHARS] or None))
    ready = all(m.matched for m in markers)
    return ready, markers


def read_log_incremental(path: str, offset: int) -> "tuple[str, int]":
    """Read the serial log from ``offset`` to its current end (streaming).

    Returns ``(new_text, new_offset)``. A missing file (QEMU has not opened
    the ``-serial file:`` yet) is not an error: ``("", offset)`` is returned.
    Text is decoded as UTF-8 with replacement so a malformed guest byte can
    never take the poll loop down.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", offset
    if size <= offset:
        return "", offset
    with open(path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read(size - offset)
    return chunk.decode("utf-8", errors="replace"), size


def _terminate(proc: "subprocess.Popen", grace: float) -> "int | None":
    """SIGTERM the guest's QEMU, escalating to SIGKILL after ``grace`` seconds.

    Returns the final return code (``None`` only if the process could not be
    reaped at all). Idempotent: an already-exited process is just waited on.
    """
    if proc.poll() is None:
        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                return None
    return proc.returncode


def _poll_to_deadline(
    proc: "subprocess.Popen",
    serial_log: str,
    patterns: "tuple[str, ...]",
    timeout: float,
    poll_interval: float,
    settle: float = 0.0,
) -> "tuple[str, float, bool, bool]":
    """Poll QEMU + the serial log until readiness, exit, or the deadline.

    Returns ``(log_text, elapsed, ready, timed_out)``. The readiness check is
    evaluated against the *accumulated* log on every poll, so a marker that
    appears before the first poll is still caught.
    """
    t0 = time.monotonic()
    deadline = t0 + timeout
    text = ""
    offset = 0
    while True:
        if proc.poll() is not None:
            break
        new_text, offset = read_log_incremental(serial_log, offset)
        if new_text:
            text += new_text
        if detect_readiness(text, patterns)[0]:
            if settle > 0 and proc.poll() is None:
                # Ready, but keep draining: the login banner and the prompt
                # that proves the login worked are still on their way out.
                settle_until = time.monotonic() + settle
                while time.monotonic() < settle_until and proc.poll() is None:
                    time.sleep(min(poll_interval, 0.05))
                    more, offset = read_log_incremental(serial_log, offset)
                    if more:
                        text += more
            return text, time.monotonic() - t0, True, False
        if time.monotonic() >= deadline:
            break
        time.sleep(min(poll_interval, 0.05))
    new_text, offset = read_log_incremental(serial_log, offset)
    if new_text:
        text += new_text
    ready, _ = detect_readiness(text, patterns)
    return text, time.monotonic() - t0, ready, proc.poll() is None


def boot_image(
    image: str,
    *,
    kernel: "str | None" = None,
    uefi: bool = False,
    tpm: bool = False,
    ovmf: "object" = None,
    snapshot: bool = True,
    hostfwd: "tuple[str, ...] | list[str] | None" = None,
    ram: int = _argv.DEFAULT_RAM_MB,
    timeout: float = DEFAULT_TIMEOUT,
    settle: float = 0.0,
    expect: "tuple[str, ...] | list[str]" = (DEFAULT_EXPECT,),
    assertions: "tuple[Assertion, ...] | list[Assertion] | None" = None,
    kvm: "bool | None" = None,
    qemu_bin: str = _argv.DEFAULT_QEMU_BIN,
    workdir: "str | None" = None,
    swtpm_bin: str = SWTPM_BIN,
    swtpm_setup_bin: "str | None" = None,
    tpm_state_fixture: "str | None" = None,
    tpm_protocol: str = "auto",
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    terminate_grace: float = DEFAULT_TERMINATE_GRACE,
    ssh_checks: "tuple[SshCheck, ...] | list[SshCheck] | None" = None,
    ssh_target: "SshTarget | None" = None,
    ssh_runner: "object" = None,
) -> BootResult:
    """Boot ``image`` headless under QEMU and wait for readiness or timeout.

    See the module docstring for the full contract. The returned
    :class:`BootResult` is *always* populated for launch-phase problems (e.g.
    a missing QEMU binary) — those are reported via ``result.error`` rather
    than raised, so the harness can always emit a result (spec: "always
    writes JSON").

    Args:
        image: path to the disk image; must exist and be readable
            (:class:`ImageNotFound` otherwise).
        kernel/uefi/ovmf/ram/kvm/qemu_bin: the same knobs as
            :func:`.argv.build_argv` (bare-metal vs firmware boot, firmware
            selection, accelerator pin, QEMU binary override for tests).
        tpm: when True, an :class:`.tpm.Swtpm` is started in the workdir and
            its datapath socket attached to the argv; it is always torn down
            before returning. A swtpm start failure raises
            ``.tpm.SwtpmError`` (raised, not a BootResult, because the
            emulator never came up and there was nothing to boot).
        timeout: maximum seconds to wait for readiness; the harness never
            blocks past it (QEMU is terminated on the deadline).
        settle: seconds to keep reading the serial log after readiness is
            reached, so the login banner and the shell prompt that follow the
            login prompt land in the captured log. Default 0 (stop on
            readiness); :data:`DEFAULT_SETTLE_SECONDS` is what the CLI and the
            smoke runner pass. Counted in ``duration``.
        expect: the readiness regex list (default ``('login:',)``).
        assertions: the check set to run over the captured serial log once
            the boot is over (item 6's :class:`.assertions.Assertion` list).
            ``None`` (default) runs :func:`.assertions.default_assertions()`;
            an empty list runs no checks at all. A malformed check or an
            uncompilable pattern is validated *pre-flight* (``ValueError``,
            before QEMU launches); the checks themselves run over whatever
            was captured — a timeout or a launch failure still get their
            checks evaluated against the partial/empty log.
        workdir: directory for the serial log / VARS copy / swtpm state.
            Defaults to a fresh ``tempfile.mkdtemp`` directory. The workdir
            is **kept** after the boot (it holds the serial log evidence for
            later assertions and human inspection).
        snapshot: attach the guest disk through a QEMU throwaway overlay
            (default ``True``). The artifact is opened read-only and no guest
            write reaches it, so a verification boot cannot change the image
            (or its checksum). ``False`` writes through to the image.
        hostfwd: optional list of QEMU user-networking port-forward specs
            (e.g. ``["tcp::2222-:22"]``), passed through to
            :func:`.argv.build_argv` so a booted guest's services (sshd) can
            be reached from the host for live inspection.
        swtpm_bin: swtpm executable name/path (injectable for tests).
        swtpm_setup_bin: ``swtpm_setup`` executable for TPM state
            provisioning (``--tpm`` boots). ``None`` (default) auto-detects
            it on ``PATH`` and falls back to the committed state fixture;
            an empty string forces the fixture (tests). Injectable, like
            ``swtpm_bin``.
        tpm_state_fixture: explicit TPM state fixture directory to seed the
            swtpm state from (default: the committed repo fixture, used only
            when no ``swtpm_setup`` is available).
        tpm_protocol: the QEMU<->swtpm wiring protocol for ``--tpm`` boots
            (see :func:`.tpm.resolve_tpm_protocol`). ``"auto"`` (default)
            probes the resolved ``qemu_bin``'s version: QEMU >= 5.2 gets the
            PTM *control* channel (swtpm ``--ctrl``, no ``--server`` — the
            only wiring that does not deadlock a modern QEMU at startup);
            QEMU < 5.2 keeps the legacy raw-data-channel wiring (swtpm
            ``--server``). ``"control"`` / ``"legacy"`` override the
            detection. Anything else is a pre-flight ``ValueError`` (the
            "always writes JSON" path).
        poll_interval: seconds between serial-log polls.
        terminate_grace: SIGTERM grace in seconds before SIGKILL.
        ssh_checks: optional :class:`.sshchecks.SshCheck` list to run *inside the
            live guest* over its sshd, between readiness and teardown. These are
            the facts the serial log cannot show (`systemctl --failed`, a service
            state, the Flatpak stack). Results are appended to ``result.checks``
            as ordinary :class:`.assertions.CheckResult` objects, so reports and
            exit codes treat them exactly like log checks. Requires the guest to
            be reachable: a build with `PENGYOS_AUTHORIZED_KEYS_FILE` and a
            hostfwd into port 22 (auto-added for the target port when missing).
            Malformed checks raise :class:`.sshchecks.SshError` pre-flight.
        ssh_target: where the guest is reachable (see :class:`.sshchecks.SshTarget`);
            ``None`` uses the documented default (root@127.0.0.1:2222).
        ssh_runner: injectable command runner for tests (see
            :func:`.sshchecks.run_ssh_checks`). ``None`` uses the real ssh.

    Returns:
        The populated :class:`BootResult`.

    Raises:
        ImageNotFound: image missing/unreadable.
        ValueError: invalid ``expect`` / ``timeout`` / ``settle`` /
            ``tpm_protocol`` inputs, or a malformed / uncompilable entry in
            ``assertions`` (pre-flight).
        .argv.OvmfNotFound: ``uefi`` set and no firmware can be located.
        .tpm.SwtpmError: ``tpm`` set and swtpm failed to come up.
        .tpm_state.TpmStateError: ``tpm`` set and no usable TPM state could
            be provisioned (no existing state, no usable swtpm_setup, no
            fixture) — raised pre-flight, before QEMU is launched.
    """
    if not image or not os.path.isfile(image) or not os.access(image, os.R_OK):
        raise ImageNotFound(f"disk image not found or unreadable: {image!r}")
    patterns = tuple(str(p) for p in expect)
    if not patterns:
        raise ValueError("the readiness pattern list must not be empty")
    if timeout <= 0:
        raise ValueError(f"timeout must be positive (got {timeout!r})")
    if settle < 0:
        raise ValueError(f"settle must not be negative (got {settle!r})")
    assertion_list = tuple(assertions) if assertions is not None else default_assertions()
    _validate_assertions(assertion_list)  # pre-flight: nothing launched yet
    use_kvm = _argv.kvm_available() if kvm is None else bool(kvm)
    # Pre-flight: pick the QEMU<->swtpm wiring for the binary that will be
    # launched (a bad protocol name raises ValueError before the workdir is
    # created and QEMU starts, so the CLI's "always writes JSON" path covers
    # it).
    resolved_tpm_protocol = (
        _tpm.resolve_tpm_protocol(tpm_protocol, qemu_bin=qemu_bin) if tpm else ""
    )

    own_workdir = workdir is None
    if own_workdir:
        workdir = tempfile.mkdtemp(prefix="pengyos-verify-")
    else:
        os.makedirs(workdir, exist_ok=True)
    serial_log = os.path.join(workdir, DEFAULT_SERIAL_LOG_NAME)

    # SSH lane (see .sshchecks): resolve the target now that the workdir exists,
    # because the guest's throwaway host key is recorded *in the workdir* as
    # part of the run's evidence. Asking for SSH checks also guarantees the
    # port forward they need, so a missing --hostfwd cannot masquerade as a
    # broken image.
    ssh_list = tuple(ssh_checks or ())
    ssh_target_resolved: "SshTarget | None" = None
    if ssh_list:
        base_target = ssh_target if ssh_target is not None else SshTarget()
        if not isinstance(base_target, SshTarget):
            raise SshError(f"ssh_target must be an SshTarget (got {base_target!r})")
        ssh_target_resolved = replace(
            base_target,
            known_hosts_file=base_target.known_hosts_file
            or os.path.join(workdir, "guest-known_hosts"),
        )
        hostfwd = _ssh_hostfwd(list(hostfwd or []), ssh_target_resolved)

    # UEFI: QEMU persists NVRAM to the VARS pflash, so the guest must get a
    # *writable copy* of the located VARS (item 3 contract, owned here).
    firmware = _argv.locate_ovmf(ovmf) if uefi else None
    ovmf_args: "dict" = {}
    if firmware is not None:
        if firmware.vars:
            vars_copy = os.path.join(workdir, DEFAULT_VARS_COPY_NAME)
            shutil.copyfile(firmware.vars, vars_copy)
            firmware = _argv.OvmfFirmware(firmware.code, vars_copy)
        ovmf_args = {"ovmf": firmware}

    swtpm: "Swtpm | None" = None
    tpm_state: "TpmStateProvision | None" = None
    tpm_socket: "str | None" = None
    if tpm:
        # The Swtpm constructor is pure (no I/O): it resolves paths and
        # renders the argv only, so provisioning can reuse its state_dir —
        # exactly the directory swtpm will be given via --tpmstate dir=.
        # The protocol was resolved pre-flight (above) against the *actual*
        # QEMU binary, because a QEMU >= 5.2 deadlocks at startup on the
        # legacy --server wiring (its chardev is the PTM control channel).
        swtpm = Swtpm(
            workdir, swtpm_bin=swtpm_bin, protocol=resolved_tpm_protocol
        )
        tpm_state = provision_tpm_state(
            swtpm.state_dir,
            swtpm_setup_bin=swtpm_setup_bin,
            fixture_dir=tpm_state_fixture,
        )
        tpm_socket = swtpm.start()

    try:
        returncode: "int | None"
        timed_out = False
        ready = False
        error: "str | None" = None
        log_text = ""
        duration = 0.0

        launch_argv = _argv.build_argv(
            image,
            kernel=kernel,
            ram=ram,
            serial_log=serial_log,
            kvm=kvm,
            qemu_bin=qemu_bin,
            uefi=uefi,
            snapshot=snapshot,
            hostfwd=hostfwd,
            tpm_socket=tpm_socket,
            **ovmf_args,
        )

        t_start = time.monotonic()
        proc: "subprocess.Popen | None" = None
        # A boot that never launched still owes every requested check a FAIL
        # with a reason, so the report shape never depends on how far we got.
        ssh_results: "list[CheckResult]" = [
            CheckResult(c.name, FAIL_STATUS, "QEMU never launched") for c in ssh_list
        ]
        try:
            proc = subprocess.Popen(
                launch_argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=workdir,
            )
        except FileNotFoundError:
            error = f"QEMU binary not found: {qemu_bin!r}"
        except OSError as exc:
            error = f"failed to launch QEMU: {exc}"

        if proc is not None:
            log_text, duration, ready, timed_out = _poll_to_deadline(
                proc,
                serial_log,
                patterns,
                timeout,
                poll_interval,
                settle,
            )
            # The guest either reached the login prompt (item 6 will run the
            # assertions over the captured log) or the deadline/exit was hit —
            # either way the boot is over here: SIGTERM → SIGKILL on a grace
            # timeout, capturing the final exit code.
            # SSH lane: run while the guest is still up. A boot that reached the
            # deadline instead of the login prompt gets FAILed SSH results rather
            # than none, so a report always lists every check that was asked for.
            if ssh_list:
                if ready:
                    ssh_results = run_ssh_checks(
                        ssh_list, ssh_target_resolved, runner=ssh_runner
                    )
                else:
                    ssh_results = [
                        CheckResult(c.name, FAIL_STATUS, "guest never reached readiness")
                        for c in ssh_list
                    ]
            returncode = _terminate(proc, terminate_grace)

        _, markers = detect_readiness(log_text, patterns)
        # Item 6's runner, wired in: evaluate the check set over whatever
        # serial log was captured — a ready boot, a timeout (partial log),
        # or a launch failure (empty log) all get checks, so the item-7
        # emitters always have a populated ``checks`` list to report.
        checks = run_assertions(log_text, assertion_list) + ssh_results
        return BootResult(
            image=image,
            argv=launch_argv,
            kvm=use_kvm,
            ready=ready,
            timed_out=timed_out,
            returncode=returncode if proc is not None else None,
            # Covers the whole attempt (swtpm startup, launch, wait,
            # teardown), not just the poll loop.
            duration=time.monotonic() - t_start,
            serial_log=serial_log,
            markers=markers,
            checks=checks,
            error=error,
            tpm_state=tpm_state.state_dir if tpm_state else "",
            tpm_state_method=tpm_state.method if tpm_state else "",
            tpm_protocol=resolved_tpm_protocol,
            snapshot=snapshot,
        )
    finally:
        if swtpm is not None:
            swtpm.stop()
        # NOTE: the workdir (even a self-made temp one) is deliberately kept:
        # it holds the serial log + VARS copy, the evidence for item 6's
        # assertions and item 7's reports.
