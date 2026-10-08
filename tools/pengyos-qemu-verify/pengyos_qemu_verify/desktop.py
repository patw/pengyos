"""The Desktop session check: prove a COSMIC session actually runs.

Part of the PengyOS verification harness (SPEC §7). This is the check that a
login prompt cannot make: 26.10.3 passed `boot`'s login-prompt check while its
compositor died at every start and its console logins were refused. So instead
of looking for a line on the console, this boots the Desktop **installer stick**
with a real (virtualised) GPU under Xvfb, *types a command on the serial
console*, and reads back what the running system says about itself: the process
list and `systemctl --failed`. A screenshot is saved as evidence.

It lives here rather than in `scripts/` for the same reason the smoke matrix
does: the Desktop gate should be one tool with one exit code and one report, not
a script `release.sh` has to know about.

Two halves, deliberately separated:

* the **pure** half — argv, environment, and the parsing of the console report —
  is unit-testable with no QEMU, no display and no root (the fixtures under
  `tests/fixtures/` are real captures);
* the **live** half (:func:`run_desktop_check`) launches Xvfb, QEMU and ffmpeg.
  It needs KVM, OVMF, Xvfb and ffmpeg, and is what `release.sh` runs.

The technique (and why each QEMU flag is what it is) is documented in
`docs/qemu-testing.md` section 5.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass

from .assertions import FAIL_STATUS, PASS_STATUS, CheckResult

__all__ = [
    "REQUIRED_PROCESSES",
    "REPORT_START",
    "REPORT_SPLIT",
    "REPORT_END",
    "SESSION_PROBE",
    "DEFAULT_RAM_MB",
    "DEFAULT_SMP",
    "DEFAULT_SESSION_TIMEOUT",
    "DEFAULT_SETTLE_SECONDS",
    "DEFAULT_OVMF_CODE",
    "DEFAULT_OVMF_VARS",
    "DesktopCheckError",
    "SessionReport",
    "SessionOutcome",
    "session_argv",
    "session_env",
    "parse_session_report",
    "saw_readiness",
    "evaluate_session",
    "run_desktop_check",
]

# The processes whose presence *is* the Desktop session. `ps -eo comm` truncates
# to 15 characters, so these are comm-length names, not package names.
REQUIRED_PROCESSES: tuple[str, ...] = ("cosmic-comp", "cosmic-panel", "pengyos-install")

# Markers the probe prints so its output can be found in a serial log that also
# contains the whole boot.
REPORT_START: str = "==PS=="
REPORT_SPLIT: str = "==FAILED=="
REPORT_END: str = "==END=="

# One line, sent to the guest's console: the process list, then the failed units.
SESSION_PROBE: str = (
    f"echo {REPORT_START}; "
    "ps -eo comm | grep -E 'cosmic|pop-launcher|pengyos-install' | sort | uniq -c; "
    f"echo {REPORT_SPLIT}; systemctl --failed --no-legend; echo {REPORT_END}"
)

DEFAULT_RAM_MB: int = 4096  # a COSMIC session needs real headroom
DEFAULT_SMP: int = 4
DEFAULT_SESSION_TIMEOUT: float = 240.0  # seconds to wait for the login prompt
# After the login prompt, how long to let the session come up before probing it.
# The compositor, panel and applets start *after* getty, not before.
DEFAULT_SETTLE_SECONDS: float = 60.0
DEFAULT_READINESS_MARKERS: tuple[str, ...] = ("login:", "automatic login")

DEFAULT_OVMF_CODE: str = "/usr/share/OVMF/OVMF_CODE_4M.fd"
DEFAULT_OVMF_VARS: str = "/usr/share/OVMF/OVMF_VARS_4M.fd"
DEFAULT_XVFB_BIN: str = "Xvfb"
DEFAULT_FFMPEG_BIN: str = "ffmpeg"
DEFAULT_QEMU_BIN: str = "qemu-system-x86_64"
SCREEN_SIZE: str = "1920x1200"


class DesktopCheckError(Exception):
    """The live check could not run (a binary is missing, the image is absent)."""


@dataclass(frozen=True)
class SessionReport:
    """What the running system said about itself, parsed out of the serial log.

    Attributes:
        ps: the ``ps -eo comm | sort | uniq -c`` section, verbatim.
        failed: the ``systemctl --failed --no-legend`` section, verbatim.
    """

    ps: str
    failed: str


@dataclass(frozen=True)
class SessionOutcome:
    """The verdict of one session check, in the harness's check-result shape.

    Attributes:
        checks: one :class:`~.assertions.CheckResult` per fact (the session
            shell, each required process, no failed units).
        report: the parsed :class:`SessionReport`, or ``None`` when the probe
            never produced output (a refused console login).
        error: why the check could not be evaluated at all.
    """

    checks: "tuple[CheckResult, ...]" = ()
    report: "SessionReport | None" = None
    error: "str | None" = None

    @property
    def passed(self) -> bool:
        if self.error is not None:
            return False
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> "list[str]":
        return [c.name for c in self.checks if not c.passed]

    def to_dict(self) -> "dict":
        return {
            "result": "pass" if self.passed else "fail",
            "error": self.error,
            "checks": [c.to_dict() for c in self.checks],
            "report_present": self.report is not None,
        }


# --- pure: how the guest is launched ----------------------------------------


def session_argv(
    image: str,
    *,
    serial_socket: str,
    vars_path: str,
    qemu_bin: str = DEFAULT_QEMU_BIN,
    ovmf_code: str = DEFAULT_OVMF_CODE,
    ram_mb: int = DEFAULT_RAM_MB,
    smp: int = DEFAULT_SMP,
    accel: str = "kvm",
) -> "list[str]":
    """The QEMU argv for a Desktop installer-stick boot with a GPU.

    Pure: it builds the argv, it does not launch anything or touch the disk.

    Why these pieces (docs/qemu-testing.md §5): the stick is attached as
    removable USB storage with ``bootindex=1`` so firmware picks it over the
    (empty) disk; ``-vga none`` plus ``virtio-gpu-gl-pci`` gives a *rendering*
    GPU instead of the plain framebuffer, which is what COSMIC needs; the stick
    is opened ``snapshot=on`` so the check never writes to the artifact.
    """
    if not image:
        raise DesktopCheckError("session_argv: image must be a non-empty path")
    if not serial_socket:
        raise DesktopCheckError("session_argv: serial_socket must be a non-empty path")
    if ram_mb <= 0 or smp <= 0:
        raise DesktopCheckError(
            f"session_argv: ram_mb and smp must be positive (got {ram_mb}, {smp})"
        )
    return [
        qemu_bin,
        "-machine", "q35",
        "-accel", accel,
        "-cpu", "host",
        "-m", str(ram_mb),
        "-smp", str(smp),
        "-drive", f"if=pflash,format=raw,readonly=on,file={ovmf_code}",
        "-drive", f"if=pflash,format=raw,file={vars_path}",
        "-device", "qemu-xhci,id=xhci",
        "-device", "usb-tablet",
        "-device", "usb-kbd",
        "-drive", f"if=none,id=stick,file={image},format=raw,snapshot=on",
        "-device", "usb-storage,bus=xhci.0,drive=stick,removable=on,bootindex=1",
        "-vga", "none",
        "-device", "virtio-gpu-gl-pci",
        "-display", "sdl,gl=on",
        "-netdev", "user,id=n0",
        "-device", "virtio-net-pci,netdev=n0",
        # A socket, not a file: the check *types* on the console and reads the
        # reply, which is the only way to ask a running system a question.
        "-serial", f"unix:{serial_socket},server=on,wait=off",
        "-no-reboot",
    ]


def session_env(base_env: "dict[str, str] | None", display: str) -> "dict[str, str]":
    """The environment QEMU and ffmpeg run under: a virtual X display, X11 SDL,
    and no host Wayland or host library path leaking in.

    ``WAYLAND_DISPLAY`` would make SDL pick Wayland (and QEMU's sdl,gl=on then
    draws on the *host* desktop); ``LD_LIBRARY_PATH`` is Buildroot's own trap
    (a trailing ``:`` breaks its builds) and a host path is wrong in the guest
    anyway.
    """
    env = {
        k: v
        for k, v in (os.environ if base_env is None else base_env).items()
        if k not in ("WAYLAND_DISPLAY", "LD_LIBRARY_PATH")
    }
    env.update(
        DISPLAY=display,
        SDL_VIDEODRIVER="x11",
        SDL_VIDEO_WINDOW_POS="0,0",
        LIBGL_ALWAYS_SOFTWARE="1",
    )
    return env


# --- pure: what the guest said ------------------------------------------------


def parse_session_report(log_text: str) -> "SessionReport | None":
    """Pull the probe's report out of the captured serial log.

    Returns ``None`` when the markers are absent — which is itself the finding:
    the console never gave a shell, so nothing was ever asked. The *last*
    occurrence is used: a login loop can print the probe line more than once.
    """
    if not log_text:
        return None
    start = log_text.rfind(REPORT_START)
    end = log_text.rfind(REPORT_END)
    if start == -1 or end == -1 or end < start:
        return None
    report = log_text[start:end]
    if REPORT_SPLIT not in report:
        return None
    ps, failed = report.split(REPORT_SPLIT, 1)
    return SessionReport(ps=ps, failed=failed)


def missing_processes(report: "SessionReport | None", required: "tuple[str, ...]" = REQUIRED_PROCESSES) -> "list[str]":
    """Required processes that the running system did not report."""
    if report is None:
        return list(required)
    return [name for name in required if name not in report.ps]


def failed_units(report: "SessionReport | None") -> "list[str]":
    """Units the running system reported as failed (its own words, not ours)."""
    if report is None:
        return []
    units = []
    for line in report.failed.splitlines():
        text = line.strip()
        if not text or REPORT_START in text or REPORT_SPLIT in text or REPORT_END in text:
            continue
        units.append(text)
    return units


def saw_readiness(log: bytes, markers: "tuple[str, ...]" = DEFAULT_READINESS_MARKERS) -> bool:
    """True when any readiness marker appears in the captured console bytes.

    The log is bytes (the console is not guaranteed to be clean UTF-8) and the
    markers are text, so the conversion happens here rather than inline.
    """
    return any(marker.encode() in log for marker in markers)


def evaluate_session(
    log_text: str,
    required: "tuple[str, ...]" = REQUIRED_PROCESSES,
) -> SessionOutcome:
    """Turn a captured session log into the harness's check results.

    The facts, in report order: the console gave a shell (the probe answered),
    each required process is running, and the system reported no failed units.
    """
    report = parse_session_report(log_text)
    if report is None:
        return SessionOutcome(
            error=(
                "no shell output on the serial console: the probe never answered, "
                "so the console login was refused or the session never came up"
            )
        )
    checks = [
        CheckResult(
            "session-shell",
            PASS_STATUS,
            f"the console answered the probe between {REPORT_START} and {REPORT_END}",
        )
    ]
    for name in required:
        checks.append(
            CheckResult(
                f"{name}-running",
                PASS_STATUS if name in report.ps else FAIL_STATUS,
                _ps_line(report.ps, name),
            )
        )
    units = failed_units(report)
    checks.append(
        CheckResult(
            "no-failed-units",
            PASS_STATUS if not units else FAIL_STATUS,
            units[0] if units else None,
        )
    )
    return SessionOutcome(checks=tuple(checks), report=report)


def _ps_line(ps_section: str, name: str) -> "str | None":
    """The ``ps`` line that mentions ``name`` (evidence for a passing check)."""
    for line in ps_section.splitlines():
        if name in line and line.strip():
            return line.strip()[:120]
    return None


# --- live: Xvfb + QEMU + the console + a screenshot --------------------------


def run_desktop_check(
    image: str,
    out_prefix: str,
    *,
    timeout: float = DEFAULT_SESSION_TIMEOUT,
    settle: float = DEFAULT_SETTLE_SECONDS,
    screenshot: bool = True,
    xvfb_bin: str = DEFAULT_XVFB_BIN,
    ffmpeg_bin: str = DEFAULT_FFMPEG_BIN,
    qemu_bin: str = DEFAULT_QEMU_BIN,
    ovmf_code: str = DEFAULT_OVMF_CODE,
    ovmf_vars: str = DEFAULT_OVMF_VARS,
    ram_mb: int = DEFAULT_RAM_MB,
    smp: int = DEFAULT_SMP,
    probe: str = SESSION_PROBE,
    readiness: "tuple[str, ...]" = DEFAULT_READINESS_MARKERS,
    required: "tuple[str, ...]" = REQUIRED_PROCESSES,
) -> SessionOutcome:
    """Boot the installer stick and ask the running system what is up.

    `required` is the process set that counts as the session. The installer stick
    must be running the installer GUI; the plain live image has no payload to run it
    from, so it is checked with `required` minus `pengyos-install` — the session is
    the claim either way. Both must reach a session: 26.10.15 shipped a plain live
    image that stopped at the greeter, and only the installer stick was ever
    session-checked, so nothing noticed.

    Writes ``<out_prefix>.png`` (the screenshot), ``<out_prefix>.serial.log``
    and ``<out_prefix>.qemu.log``, then returns the verdict. Raises
    :class:`DesktopCheckError` when the live check cannot run at all (no image,
    no OVMF, no Xvfb).
    """
    if not image or not os.path.isfile(image):
        raise DesktopCheckError(f"installer image not found: {image!r}")
    for binary, label in ((xvfb_bin, "Xvfb"), (ffmpeg_bin, "ffmpeg"), (qemu_bin, "QEMU")):
        if shutil.which(binary) is None:
            raise DesktopCheckError(f"{label} ({binary!r}) is not installed; the session check needs it")
    if not os.path.isfile(ovmf_code) or not os.path.isfile(ovmf_vars):
        raise DesktopCheckError(f"OVMF firmware not found ({ovmf_code!r} / {ovmf_vars!r})")

    out_dir = os.path.dirname(out_prefix) or "."
    os.makedirs(out_dir, exist_ok=True)
    # Unix socket paths are capped at 108 bytes, so this cannot live in the
    # (deep) build tree.
    sock = os.path.join("/tmp", f"pengyos-session-{os.getpid()}.sock")
    if os.path.exists(sock):
        os.unlink(sock)
    vars_copy = os.path.join(out_dir, "vars.fd")
    shutil.copyfile(ovmf_vars, vars_copy)
    display = f":{100 + os.getpid() % 400}"
    env = session_env(os.environ, display)

    log = bytearray()
    qemu = xvfb = None
    try:
        xvfb = subprocess.Popen(
            [xvfb_bin, display, "-screen", "0", f"{SCREEN_SIZE}x24"],
            stderr=subprocess.DEVNULL,
        )
        time.sleep(2)  # Xvfb needs a moment before QEMU's SDL window can map
        with open(out_prefix + ".qemu.log", "w") as qemu_log:
            qemu = subprocess.Popen(
                session_argv(
                    image,
                    serial_socket=sock,
                    vars_path=vars_copy,
                    qemu_bin=qemu_bin,
                    ovmf_code=ovmf_code,
                    ram_mb=ram_mb,
                    smp=smp,
                ),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=qemu_log,
            )
        for _ in range(int(timeout / 4) or 1):
            if os.path.exists(sock):
                break
            time.sleep(0.2)
        conn = socket.socket(socket.AF_UNIX)
        conn.connect(sock)
        conn.settimeout(1)

        def read_for(seconds: float) -> None:
            end = time.time() + seconds
            while time.time() < end:
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if chunk:
                    log.extend(chunk)

        deadline = time.time() + timeout
        while time.time() < deadline and not saw_readiness(bytes(log), readiness):
            read_for(2)
        read_for(settle)  # the session starts after getty, not before
        conn.sendall(b"\n")
        read_for(2)
        conn.sendall(probe.encode() + b"\n")
        read_for(8)
        if screenshot:
            subprocess.run(
                [
                    ffmpeg_bin, "-loglevel", "error", "-y",
                    "-f", "x11grab", "-video_size", SCREEN_SIZE,
                    "-i", display, "-frames:v", "1", out_prefix + ".png",
                ],
                env=env,
            )
        conn.close()
    except FileNotFoundError as exc:
        raise DesktopCheckError(f"failed to launch a required binary: {exc}") from exc
    except (ConnectionError, OSError) as exc:
        raise DesktopCheckError(f"the guest serial console was unreachable: {exc}") from exc
    finally:
        with open(out_prefix + ".serial.log", "wb") as handle:
            handle.write(bytes(log))
        for proc in (qemu, xvfb):
            if proc is None:
                continue
            proc.terminate()
            try:
                proc.wait(10)
            except Exception:
                proc.kill()
        if os.path.exists(sock):
            try:
                os.unlink(sock)
            except OSError:
                pass

    return evaluate_session(bytes(log).decode("utf-8", "replace"), required)
