"""``run`` — launch an extracted AppImage's binary under a bubblewrap sandbox.

This module *runs* an extracted AppImage (the output of ``pengyos-appimage
extract``), but it does so in a way that respects the project's single security
invariant:

> **It never executes an AppImage's embedded hooks.**

The AppImage's embedded ``AppRun`` script is exactly the kind of untrusted hook
we refuse to run. Instead of launching the untrusted wrapper, :func:`run_app`
parses the extracted payload's ``.desktop`` ``Exec`` line to find the *real*
application binary and runs that binary directly — inside a
[bubblewrap](https://github.com/containers/bubblewrap) sandbox.

The sandbox profile (see :func:`build_sandbox_argv`) is a documented, minimal
bubblewrap invocation:

* ``--ro-bind / /`` — the host filesystem, read-only, so the app's shared
  libraries and the binaries it calls (e.g. ``/bin/sh``) are available.
* ``--bind <app_dir> <app_dir>`` — the app's own files, read-write (this
  ``--bind`` overrides the read-only ``/`` for the app's path).
* ``--proc /proc`` / ``--dev /dev`` — a private, sane ``/proc`` and ``/dev``.
* ``--dir /home`` — a private (empty) home directory.
* ``--bind /etc/resolv.conf /etc/resolv.conf`` — keep DNS working.
* ``-- <binary> <extra args>`` — the run command after the ``--`` separator.

The app is launched by its *host* absolute path (visible through the read-only
``/`` bind), never via the payload's ``AppRun`` hook.

The whole bwrap argv is produced by the pure, side-effect-free
:func:`build_sandbox_argv`, so it is fully testable as a *golden* without ever
shelling out. The single seam that does run a subprocess is
:class:`BwrapTool.run` (mirroring :class:`pengyos_appimage.squashfs.SquashfsTool`);
unit tests replace it, so no real ``bwrap``, root, network, or display is
needed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import desktop

# Exit codes used by the CLI.
EXIT_OK = 0
EXIT_ERROR = 1

# Directory search order for resolving a bare program name from an ``Exec``
# line against the extracted payload's filesystem layout.
_PROGRAM_SEARCH_DIRS = (
    "usr/bin",
    "bin",
    "usr/local/bin",
    "usr/local/sbin",
    "sbin",
    "usr/sbin",
)

# The ``Exec`` tokens that mark a program argument as *not* a program name (per
# the Desktop Entry Specification's Exec format). We take the first token as the
# program, so these only matter for defensive parsing of absolute paths.
_EXEC_OPTION_TOKENS = frozenset(
    {"%f", "%F", "%u", "%U", "%i", "%d", "%D", "%n", "%N", "%v", "%m"}
)


class RunError(RuntimeError):
    """Raised when an extracted app cannot be launched."""


@dataclass
class RunResult:
    """The outcome of running an extracted app."""

    #: The resolved application binary that was launched.
    binary: str | None = None
    #: The ``Exec`` program name that was resolved (e.g. ``sample``).
    program: str | None = None
    #: Whether the process ran inside the bubblewrap sandbox.
    sandboxed: bool = False
    #: The exact argv that was launched (bwrap argv, or a direct exec when
    #: ``sandbox=False``).
    argv: list[str] = field(default_factory=list)
    #: The child process return code (``None`` when it was never launched).
    returncode: int | None = None
    #: Captured stdout (``""`` when not captured).
    stdout: str = ""
    #: Captured stderr (``""`` when not captured).
    stderr: str = ""
    #: Human-readable notes (e.g. a missing binary); never fatal on their own.
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable view of the result."""
        return {
            "binary": self.binary,
            "program": self.program,
            "sandboxed": self.sandboxed,
            "argv": list(self.argv),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "warnings": list(self.warnings),
        }


def _program_from_exec(exec_value: str | None) -> str | None:
    """Return the program name from an ``Exec`` value (first token).

    Per the Desktop Entry Specification, the ``Exec`` value is a command line
    whose first token is the program to run; the remaining tokens are arguments
    and magic tokens (``%F``, ``%U`` ...). We return only the first token.
    """
    if not exec_value:
        return None
    tokens = exec_value.split()
    if not tokens:
        return None
    return tokens[0]


def resolve_binary(app_dir: str | Path, exec_value: str | None) -> Path | None:
    """Resolve the program named in an *exec_value* to a file inside *app_dir*.

    The value comes from an extracted payload's ``.desktop`` ``Exec`` line. We
    never run the payload's ``AppRun`` hook; we locate the *actual* binary the
    ``Exec`` line points at.

    * An absolute path (``/usr/bin/sample``) is looked up verbatim under
      *app_dir*.
    * A bare name (``sample``) is searched for under the common bin directories
      (``usr/bin``, ``bin`` ...).
    """
    program = _program_from_exec(exec_value)
    if not program or program in _EXEC_OPTION_TOKENS:
        return None

    root = Path(app_dir)
    if not root.is_dir():
        return None

    # Absolute Exec path -> look it up under the payload root.
    if program.startswith("/"):
        candidate = root / program.lstrip("/")
        return candidate if candidate.is_file() else None

    # Bare name -> search the common bin directories, shallowest first.
    for relative in _PROGRAM_SEARCH_DIRS:
        candidate = root / relative / program
        if candidate.is_file():
            return candidate

    # Fall back to any executable file with that basename (covers odd layouts).
    matches = [
        p
        for p in root.rglob(program)
        if p.is_file() and os.access(p, os.X_OK)
    ]
    return matches[0] if matches else None


def build_sandbox_argv(
    app_dir: str | Path,
    binary: str | Path,
    extra_args: list[str] | None = None,
    *,
    bind_roots: dict[str, str] | None = None,
) -> list[str]:
    """Build the bubblewrap argv that runs *binary* inside a sandbox.

    This is a pure, side-effect-free function: it only assembles a list of
    arguments, so it is fully testable as a *golden* without shelling out.

    The documented sandbox profile:

    * ``--ro-bind / /`` — the host filesystem, read-only, so the app's shared
      libraries and the binaries it calls (e.g. ``/bin/sh``) are available.
    * ``--bind <app_dir> <app_dir>`` — the app's own files, read-write (this
      ``--bind`` overrides the read-only ``/`` for the app's path, so it comes
      after it).
    * ``--proc /proc`` / ``--dev /dev`` — a private, sane ``/proc``/``/dev``.
    * ``--dir /home`` — a private (empty) home.
    * ``--bind /etc/resolv.conf /etc/resolv.conf`` — keep DNS resolving.
    * ``-- <binary> <extra args>`` — the run command after the ``--`` separator.

    Args:
        app_dir: The extracted application directory (bind read-write into the
            sandbox).
        binary: The resolved application binary to launch (by host path).
        extra_args: Extra arguments forwarded to the application.
        bind_roots: Optional extra ``host_path -> container_path`` read-only
            bind mounts (used by tests / advanced profiles).
    """
    app_dir = str(app_dir)
    binary = str(binary)
    argv: list[str] = ["bwrap"]

    # The host filesystem, read-only: this is what makes the app's shared
    # libraries and the binaries it calls (e.g. ``/bin/sh``) available inside
    # the sandbox.
    argv += ["--ro-bind", "/", "/"]

    # The app's own files, read-write. This ``--bind`` overrides the read-only
    # ``/`` bind for the app's path (so it must come after it).
    argv += ["--bind", app_dir, app_dir]

    # A private, sane /proc and /dev.
    argv += ["--proc", "/proc"]
    argv += ["--dev", "/dev"]

    # A private (empty) home directory.
    argv += ["--dir", "/home"]

    # Keep DNS working by binding the resolver config read-only.
    resolv = "/etc/resolv.conf"
    if os.path.exists(resolv):
        argv += ["--ro-bind", resolv, resolv]

    # Honour any extra read-only bind roots (tests / advanced profiles).
    for host_path, container_path in (bind_roots or {}).items():
        argv += ["--ro-bind", str(host_path), str(container_path)]

    # Everything after ``--`` is passed to the sandboxed program verbatim.
    argv += ["--", binary, *list(extra_args or [])]
    return argv


def _direct_exec_argv(binary: str | Path, extra_args: list[str]) -> list[str]:
    """The argv used when the sandbox is disabled (run without bwrap)."""
    return [str(binary), *extra_args]


def _find_desktop_exec(app_dir: Path) -> str | None:
    """Return the ``Exec`` value from the extracted payload's ``.desktop``.

    Returns ``None`` if no ``.desktop`` file (or no ``Exec`` key) is present.
    """
    matches = sorted(app_dir.rglob("*.desktop"), key=lambda p: len(p.parts))
    for desktop_file in matches:
        try:
            doc = desktop.parse_desktop_file(desktop_file)
        except desktop.DesktopParseError:
            continue
        entry = doc.main
        if entry is not None:
            return entry.get("Exec")
    return None


def run_app(
    app_dir: str | Path,
    extra_args: list[str] | None = None,
    *,
    sandbox: bool = True,
    tool: "BwrapTool | None" = None,
    capture: bool = True,
) -> RunResult:
    """Run the extracted app at *app_dir*, optionally under a bubblewrap sandbox.

    The AppImage's embedded ``AppRun`` hook is never executed: the real binary
    named by the payload's ``.desktop`` ``Exec`` line is launched instead, and
    (when *sandbox* is ``True``) inside a bubblewrap sandbox.

    Args:
        app_dir: The extracted application directory.
        extra_args: Extra arguments forwarded to the application.
        sandbox: When ``True`` (default), launch under bubblewrap. When
            ``False``, exec the binary directly (insecure).
        tool: Optional :class:`BwrapTool` (the ``bwrap`` seam). Defaults to the
            real ``bwrap`` on ``PATH``.
        capture: When ``True``, capture the child's stdout/stderr.

    Returns:
        A :class:`RunResult`.

    Raises:
        RunError: If no ``.desktop`` ``Exec`` line is found, the program cannot
            be resolved, or ``bwrap`` is not installed (when sandboxing).
    """
    # bwrap requires absolute paths (a relative app dir fails to mount), so
    # normalise to an absolute path up front.
    app_path = Path(os.path.abspath(app_dir))
    if not app_path.is_dir():
        raise RunError(f"not a directory: {app_path}")

    exec_value = _find_desktop_exec(app_path)
    if not exec_value:
        raise RunError(
            "no Exec line found in the extracted app's .desktop file; refusing "
            "to run the embedded AppRun hook"
        )

    program = _program_from_exec(exec_value)
    binary = resolve_binary(app_path, exec_value)
    if binary is None:
        raise RunError(
            f"could not resolve the '{program}' binary from {app_path}; refusing "
            "to run the embedded AppRun hook"
        )

    warnings: list[str] = []
    extra_args = list(extra_args or [])

    if sandbox:
        # Sandbox: launch under the real bwrap (unless a fake tool is injected).
        executor = tool or default_tool()
        argv = build_sandbox_argv(app_path, binary, extra_args)
        sandboxed = True
    else:
        warnings.append(
            "running WITHOUT the bubblewrap sandbox (--no-sandbox is insecure)"
        )
        # No sandbox: exec the binary directly through the same seam.
        executor = tool or BwrapTool()
        argv = _direct_exec_argv(binary, extra_args)
        sandboxed = False

    completed = executor.run(argv, capture=capture)
    return RunResult(
        binary=str(binary),
        program=program,
        sandboxed=sandboxed,
        argv=argv,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        warnings=warnings,
    )


class BwrapTool:
    """Base class for the ``bwrap`` execution seam.

    :meth:`run` is the single seam tests patch. The default implementation
    shells out to the real ``bwrap`` executable, which requires it to be
    installed; unit tests replace :meth:`run` with a canned fake so no real
    ``bwrap``, root, network, or display is needed.
    """

    name = "bwrap"

    def available(self) -> bool:
        return shutil.which(self.name) is not None

    def run(
        self,
        argv: list[str],
        capture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``bwrap <argv>``. The seam tests patch."""
        return subprocess.run(
            argv,
            capture_output=capture,
            text=True,
            check=False,
        )


def default_tool() -> BwrapTool:
    """Return the real :class:`BwrapTool` (raising if ``bwrap`` is absent)."""
    tool = BwrapTool()
    if not tool.available():
        raise RunError(
            "bwrap is not installed; install bubblewrap to use the sandbox, "
            "or pass --no-sandbox to run without it"
        )
    return tool
