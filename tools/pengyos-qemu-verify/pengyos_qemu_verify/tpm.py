"""swtpm TPM2 process management for the PengyOS boot-test harness.

The *lifecycle* of an
optional ``swtpm`` (sofTPM, the TPM2 emulator) process that a headless boot
can hang a guest TPM2 device off of.

It is deliberately split in two so each half is directly testable without a
real ``swtpm`` binary:

* :func:`build_swtpm_argv` — a *pure* builder that renders the exact
  ``swtpm socket ...`` command line for a given unix socket, protocol and
  state directory. No subprocess, no I/O.
* :class:`Swtpm` — a context manager that actually ``Popen``s that command
  line, waits until the QEMU-facing socket exists (readiness), and tears the
  process down on exit. Everything is injectable (``swtpm_bin``), so the test
  suite drives the real code path against a tiny *fake* swtpm that just opens
  the requested socket and sleeps — no ``swtpm`` installed, no network, no
  root, no hardware.

**QEMU version protocol** (discovered 2026-09-13, the PENGYOS_IT real-TPM
boot failure): the socket a QEMU ``-tpmdev emulator`` chardev points at is
not a constant across QEMU versions:

* **``legacy`` (QEMU < 5.2)** — the chardev socket is the raw TPM *data*
  channel; swtpm is started with ``--server type=unixio,path=<sock>``.
* **``control`` (QEMU >= 5.2)** — the chardev socket is a *control* channel
  speaking the PTM protocol (``tpm_ioctl.h``): QEMU connects, passes its own
  data-channel fd to swtpm via ``CMD_SET_DATAFD`` (an ``SCM_RIGHTS``
  ancillary message), then drives the TPM over that new fd. swtpm is started
  with ``--ctrl type=unixio,path=<sock>`` and **no** ``--server``.

Wiring the ``legacy`` (``--server``) socket into a modern QEMU deadlocks at
QEMU *startup*: QEMU blocks forever on the ``CMD_SET_DATAFD`` handshake
before it even creates the serial file, the guest never starts, and the boot
harness times out with an empty log. :func:`qemu_version_tuple` +
:func:`resolve_tpm_protocol` pick the right wiring for the QEMU binary that
will actually be launched.

The QEMU side of the wiring (``-chardev``/``-tpmdev``) lives in
:func:`.argv.tpm_qemu_args` / :func:`.argv.build_argv`; its rendering is
identical under both protocols (same socket *path*; only its *meaning*
changes). This module owns the ``swtpm`` process and hands the caller the
QEMU-facing *socket path* to feed into ``build_argv(tpm_socket=...)``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

__all__ = [
    "SWTPM_BIN",
    "DEFAULT_QEMU_BIN",
    "PROTOCOL_LEGACY",
    "PROTOCOL_CONTROL",
    "QEMU_CONTROL_PROTOCOL_MIN",
    "SwtpmError",
    "SwtpmStartError",
    "qemu_version_tuple",
    "protocol_for_qemu_version",
    "resolve_tpm_protocol",
    "build_swtpm_argv",
    "Swtpm",
]

# Default emulator binary; overridable per-instance for tests/fakes.
SWTPM_BIN: str = "swtpm"
# The QEMU binary the protocol auto-detection probes when no explicit one is
# given (same default the argv/boot layers use).
DEFAULT_QEMU_BIN: str = "qemu-system-x86_64"

# The two supported QEMU<->swtpm wiring protocols (see the module docstring).
PROTOCOL_LEGACY: str = "legacy"
PROTOCOL_CONTROL: str = "control"
# QEMU 5.2 (October 2021) introduced backends/tpm/tpm_emulator.c, which turned
# the emulator chardev into the PTM *control* channel. Every QEMU release
# since (5.2+, i.e. everything from 2022 on) speaks the control protocol; the
# raw-data-channel wiring only works below it.
QEMU_CONTROL_PROTOCOL_MIN: "tuple[int, ...]" = (5, 2)

# The ``qemu-system-x86_64 --version`` output, e.g.
#   "QEMU emulator version 10.2.1 (Debian 1:10.2.1+ds-1ubuntu3.2)"
_QEMU_VERSION_RE = re.compile(r"version\s+(\d+(?:\.\d+)*)")


class SwtpmError(RuntimeError):
    """Base error for swtpm lifecycle failures."""


class SwtpmStartError(SwtpmError):
    """Raised when swtpm fails to come up (dies early or never opens the
    datapath socket within the readiness timeout)."""


def qemu_version_tuple(binary: "str | None" = None) -> "tuple[int, ...] | None":
    """Return the numeric version of a QEMU binary as an int tuple.

    Runs ``<binary> --version`` and parses the first ``version X[.Y[.Z]]``
    from its output (e.g. ``10.2.1`` from
    ``"QEMU emulator version 10.2.1 (Debian ...)"``).

    Returns ``None`` — never raises — when the binary cannot be run (missing,
    not executable, times out) or its output does not carry a parseable
    version, so callers can fall back to a documented default.

    Args:
        binary: QEMU executable name/path. ``None`` resolves
            :data:`DEFAULT_QEMU_BIN` on ``PATH``.
    """
    if binary is None:
        binary = shutil.which(DEFAULT_QEMU_BIN) or DEFAULT_QEMU_BIN
    try:
        proc = subprocess.run(
            [binary, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    match = _QEMU_VERSION_RE.search(proc.stdout.decode("utf-8", "replace"))
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def protocol_for_qemu_version(
    version: "tuple[int, ...] | None",
) -> str:
    """The QEMU<->swtpm wiring protocol for a given QEMU version tuple.

    ``>= :data:`QEMU_CONTROL_PROTOCOL_MIN`` (5.2) is :data:`PROTOCOL_CONTROL`
    (the chardev is the PTM control channel); below it :data:`PROTOCOL_LEGACY`
    (the chardev is the raw data channel). An *unknown* version (``None``)
    resolves to :data:`PROTOCOL_CONTROL`: every QEMU release since 2022 is
    >= 5.2, so the modern wiring is the safe default — the legacy one is
    kept only for pre-5.2 binaries.
    """
    if version is None:
        return PROTOCOL_CONTROL
    return PROTOCOL_CONTROL if version >= QEMU_CONTROL_PROTOCOL_MIN else PROTOCOL_LEGACY


def resolve_tpm_protocol(
    protocol: str = "auto",
    qemu_bin: "str | None" = None,
) -> str:
    """Resolve the QEMU<->swtpm wiring protocol for a boot.

    ``"auto"`` (the default) probes :func:`qemu_version_tuple` for
    ``qemu_bin`` and picks per :func:`protocol_for_qemu_version`; an explicit
    :data:`PROTOCOL_LEGACY` / :data:`PROTOCOL_CONTROL` is returned verbatim
    (a caller with an out-of-band reason to override the detection).

    Raises:
        ValueError: an unknown protocol name (typo guard — a bad value must
            never silently pick a wiring).
    """
    if protocol in (PROTOCOL_LEGACY, PROTOCOL_CONTROL):
        return protocol
    if protocol == "auto":
        return protocol_for_qemu_version(qemu_version_tuple(qemu_bin))
    raise ValueError(
        f"unknown TPM protocol {protocol!r}; expected 'auto', "
        f"{PROTOCOL_LEGACY!r} or {PROTOCOL_CONTROL!r}"
    )


def build_swtpm_argv(
    socket_path: str,
    ctrl_path: "str | None" = None,
    state_dir: "str | None" = None,
    *,
    swtpm_bin: str = SWTPM_BIN,
    pidfile: "str | None" = None,
    protocol: str = PROTOCOL_CONTROL,
    extra: "tuple[str, ...] | list[str] | None" = None,
) -> "list[str]":
    """Build the ``swtpm socket`` command line for a QEMU-facing TPM2 emulator.

    ``socket_path`` is the unix socket QEMU's ``-tpmdev emulator`` chardev
    connects to, and what it *means* depends on ``protocol``:

    * **``control`` (default; QEMU >= 5.2)** — it is the PTM *control*
      channel: ``--ctrl type=unixio,path=<socket_path>`` with **no**
      ``--server``. QEMU passes swtpm its data-channel fd via
      ``CMD_SET_DATAFD``. A separate ``ctrl_path`` is meaningless and
      rejected.
    * **``legacy`` (QEMU < 5.2)** — it is the raw TPM *data* channel:
      ``--server type=unixio,path=<socket_path>``, with an *optional*
      separate unix-socket ``--ctrl`` control channel for out-of-band
      ``TPM2_*``/reset control.

    Args:
        socket_path: the QEMU-facing unix socket. Required, non-empty.
        ctrl_path: separate control-channel socket; only honoured in the
            legacy protocol. ``None`` (default) omits ``--ctrl`` there;
            non-``None`` under the control protocol raises.
        state_dir: optional TPM state directory (``--tpmstate dir=<state_dir>``).
            When ``None`` the argument is omitted (swtpm falls back to its
            default/``TPM_PATH``).
        swtpm_bin: path/name of the swtpm executable (default
            :data:`SWTPM_BIN`). Injectable so tests use a fake.
        pidfile: optional path swtpm writes its PID to (``--pid file=...``).
        protocol: the wiring protocol (default :data:`PROTOCOL_CONTROL`);
            see above for the two wirings.
        extra: optional extra tokens appended verbatim at the end (e.g.
            ``--daemon``, ``--flags not-need-init``, ``--key file=...``).

    Returns:
        The command vector, including ``swtpm_bin`` as ``argv[0]``.

    Raises:
        ValueError: if ``socket_path`` or ``swtpm_bin`` is empty, the
            protocol is unknown, or a separate ``ctrl_path`` is combined
            with the control protocol.
    """
    if not socket_path:
        raise ValueError("socket_path must be a non-empty string")
    if not swtpm_bin:
        raise ValueError("swtpm_bin must be a non-empty string")
    if protocol not in (PROTOCOL_LEGACY, PROTOCOL_CONTROL):
        raise ValueError(
            f"unknown TPM protocol {protocol!r}; expected {PROTOCOL_LEGACY!r} "
            f"or {PROTOCOL_CONTROL!r}"
        )

    argv: "list[str]" = [swtpm_bin, "socket", "--tpm2"]
    if protocol == PROTOCOL_CONTROL:
        if ctrl_path:
            raise ValueError(
                "with the control protocol the QEMU-facing socket IS the "
                "control channel; pass ctrl_path=None"
            )
        argv += ["--ctrl", f"type=unixio,path={socket_path}"]
    else:
        argv += ["--server", f"type=unixio,path={socket_path}"]
        if ctrl_path:
            argv += ["--ctrl", f"type=unixio,path={ctrl_path}"]
    if state_dir:
        argv += ["--tpmstate", f"dir={state_dir}"]
    if pidfile:
        argv += ["--pid", f"file={pidfile}"]
    if extra:
        argv += list(extra)
    return argv


class Swtpm:
    """A context-managed ``swtpm`` process backed by a unix-socket datapath.

    Usage::

        with Swtpm(workdir) as tpm:
            argv = build_argv(image, uefi=True, ovmf=fw, tpm_socket=tpm.socket_path)
            # ... launch QEMU with `argv` ...

    On ``__enter__``/:meth:`start` the process is spawned and we block until
    its datapath socket appears (or :class:`SwtpmStartError` is raised). On
    ``__exit__``/:meth:`stop` the process is terminated (SIGTERM, then
    SIGKILL on a grace timeout) and the socket/control files are cleaned up.

    All paths default into ``workdir``; the swtpm binary and every timeout are
    injectable, so the test suite runs the real code path against a fake
    swtpm that merely opens the requested socket.
    """

    def __init__(
        self,
        workdir: str,
        *,
        swtpm_bin: str = SWTPM_BIN,
        socket_name: str = "tpm.sock",
        ctrl_name: str = "tpm.ctrl",
        state_subdir: str = "tpmstate",
        pidfile_name: str = "swtpm.pid",
        use_ctrl: bool = True,
        protocol: str = PROTOCOL_CONTROL,
        ready_timeout: float = 10.0,
        stop_timeout: float = 5.0,
        poll_interval: float = 0.05,
        extra: "tuple[str, ...] | list[str] | None" = None,
    ):
        """Resolve the swtpm paths and render its argv (pure: no I/O).

        ``protocol`` picks the wiring (see :func:`build_swtpm_argv`):
        :data:`PROTOCOL_CONTROL` (default) for QEMU >= 5.2, where the QEMU-
        facing socket ``tpm.sock`` is the PTM control channel and there is no
        separate control file; :data:`PROTOCOL_LEGACY` for QEMU < 5.2, where
        ``tpm.sock`` is the raw data channel (``--server``) and ``use_ctrl``
        optionally adds a separate ``--ctrl`` socket.
        """
        if not workdir:
            raise ValueError("workdir must be a non-empty string")
        self.workdir = workdir
        self.swtpm_bin = swtpm_bin
        self.socket_path = os.path.join(workdir, socket_name)
        self.protocol = protocol
        if protocol == PROTOCOL_CONTROL:
            # The QEMU-facing socket IS the control channel; there is no
            # separate control file in this wiring.
            self.ctrl_path = None
        else:
            self.ctrl_path = os.path.join(workdir, ctrl_name) if use_ctrl else None
        self.state_dir = os.path.join(workdir, state_subdir)
        self.pidfile = os.path.join(workdir, pidfile_name)
        self._out = os.path.join(workdir, "swtpm.out")
        self._err = os.path.join(workdir, "swtpm.err")
        self.extra = tuple(extra) if extra else ()
        self.ready_timeout = ready_timeout
        self.stop_timeout = stop_timeout
        self.poll_interval = poll_interval

        self.argv = build_swtpm_argv(
            self.socket_path,
            self.ctrl_path,
            self.state_dir,
            swtpm_bin=swtpm_bin,
            pidfile=self.pidfile,
            protocol=protocol,
            extra=self.extra,
        )
        self._proc: "subprocess.Popen | None" = None

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> str:
        """Spawn swtpm and block until its datapath socket is ready.

        Returns the datapath socket path (feed it to ``build_argv`` as
        ``tpm_socket=``).

        Raises:
            SwtpmStartError: if the process dies before the socket appears or
                does not open the socket within :attr:`ready_timeout`.
            SwtpmError: if swtpm is already running.
        """
        if self._proc is not None:
            raise SwtpmError("swtpm is already running")

        os.makedirs(self.workdir, exist_ok=True)
        if self.state_dir:
            os.makedirs(self.state_dir, exist_ok=True)
        for stale in self._managed_paths():
            if os.path.exists(stale):
                os.remove(stale)

        try:
            with open(self._out, "wb") as out, open(self._err, "wb") as err:
                self._proc = subprocess.Popen(
                    self.argv,
                    stdout=out,
                    stderr=err,
                    cwd=self.workdir,
                )
        except FileNotFoundError as exc:
            self._proc = None
            raise SwtpmError(f"swtpm binary not found: {self.swtpm_bin!r}") from exc

        try:
            self._wait_ready()
        except Exception:
            self.stop()
            raise
        return self.socket_path

    def _managed_paths(self) -> "list[str]":
        paths = [self.socket_path, self.pidfile]
        if self.ctrl_path:
            paths.append(self.ctrl_path)
        return paths

    def _wait_ready(self) -> None:
        proc = self._proc
        assert proc is not None
        deadline = time.monotonic() + self.ready_timeout
        while True:
            if os.path.exists(self.socket_path) and proc.poll() is None:
                return
            code = proc.poll()
            if code is not None:
                raise SwtpmStartError(
                    f"swtpm exited before opening its socket (code={code}); "
                    f"see {self._err}"
                )
            if time.monotonic() >= deadline:
                raise SwtpmStartError(
                    f"swtpm did not open {self.socket_path!r} within "
                    f"{self.ready_timeout}s; see {self._err}"
                )
            time.sleep(self.poll_interval)

    def stop(self) -> None:
        """Terminate swtpm (idempotent) and remove its socket/control files."""
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=self.stop_timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
                proc.wait()
        for path in self._managed_paths():
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

    @property
    def running(self) -> bool:
        """True while a swtpm process has been started and not yet stopped."""
        return self._proc is not None and self._proc.poll() is None

    # -- context manager ----------------------------------------------------
    def __enter__(self) -> "Swtpm":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


def find_swtpm_bin() -> "str | None":
    """Return the path to a real ``swtpm`` binary if one is on ``PATH``.

    Convenience for the opt-in integration path; returns ``None`` (never
    raises) when swtpm is not installed so callers can degrade gracefully.
    The unit suite does not use this — it passes a fake ``swtpm_bin``.
    """
    return shutil.which("swtpm")
