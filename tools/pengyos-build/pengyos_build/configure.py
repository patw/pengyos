"""Configure a PengyOS target against a pinned Buildroot tree.

The configure step is the first make invocation of a Buildroot build:

    make -C <source> O=<output> BR2_EXTERNAL=<tree>[ BR2_BOARD=<board>] <defconfig>

Layout under the build root (``$PENGYOS_BUILD_ROOT`` or ``~/build/pengyos``)::

    buildroot-<version>        acquired source tree (see acquire.py)
    output/<target>            O= output dir for ONE target (per target: a
                                target's output never mixes with another's)
    output/<target>/logs/      our make output logs (configure.log now;
                                build.log later) — inside the output dir so
                                ``clean`` (scoped to the output dir) wipes them
    output/<target>/.hostbin   GNU-over-uutils host shim (hostshim.py) — also
                                inside the output dir, so every write of a
                                build lives under ``output/<target>``

Design constraints (spec):

* configure **never touches the network** and never auto-acquires: if the
  source tree for the target's ``br_version`` is missing, it raises a
  :class:`ConfigureError` telling the user to run
  ``pengyos-build acquire <target>`` first;
* make's stdout and stderr are captured **merged, in order** (stderr is
  redirected onto the log file handle), because the later ``status``
  subcommand reports the build stage by scanning this log;
* the child environment goes through
  :func:`pengyos_build.hostshim.run_build_with_host_shim` so Buildroot sees
  GNU coreutils even on a uutils host (SPEC §7 gotcha);
* the process runner is injectable (a ``subprocess.run``-like callable), so
  unit tests run hermetically with a fake make.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import hostshim, stale
from .acquire import default_build_root, source_dir
from .targets import Target

#: Build-root subdirectory holding per-target build outputs.
OUTPUT_SUBDIR = "output"
#: Directory inside an output dir that holds our make output logs.
LOGS_SUBDIR = "logs"
#: Name of the configure log (the build log will live next to it).
CONFIGURE_LOG = "configure.log"
#: Host-shim directory inside the output dir (GNU coreutils over uutils).
HOSTSHIM_DIRNAME = ".hostbin"


class ConfigureError(RuntimeError):
    """The configure step failed (missing tree, make not found, make error)."""


@dataclass(frozen=True)
class ConfigureResult:
    """Outcome of a successful configure step.

    ``argv`` is the exact make invocation executed (the seed for the later
    ``--dry-run``); ``log`` is where make's merged stdout/stderr was captured.
    """

    target: str
    source: Path
    output: Path
    log: Path
    argv: tuple[str, ...]
    # Build dirs removed because the new .config changed their options (see
    # stale.py), and packages the new .config turned off.
    rebuilt: tuple[str, ...] = ()
    disabled: tuple[str, ...] = ()

    def __str__(self) -> str:  # convenient one-line summary for the CLI
        lines = [f"configured {self.target}: log {self.log}"]
        if self.rebuilt:
            lines.append("options changed, will rebuild: " + " ".join(self.rebuilt))
        if self.disabled:
            lines.append(
                "disabled (files stay in target/ until a clean build): "
                + " ".join(self.disabled)
            )
        return "\n".join(lines)


#: A ``subprocess.run``-like callable: ``runner(argv, env=…, stdout=…,
#: stderr=…, text=…) -> CompletedProcess``.  Injectable so tests can fake
#: make without spawning a process.
Spawner = Callable[..., subprocess.CompletedProcess]


def output_dir(build_root: Path | str, target: Target) -> Path:
    """Per-target O= output directory: ``<build-root>/output/<target name>``."""
    return Path(build_root) / OUTPUT_SUBDIR / target.name


def configure_argv(
    source: Path | str, output: Path | str, target: Target
) -> tuple[str, ...]:
    """Exact make argv for the configure step.

    ``BR2_BOARD`` is included only when the target defines a board; the bare
    defconfig name is resolved by make inside ``$BR2_EXTERNAL/configs/``.
    """
    argv = [
        "make",
        "-C",
        str(source),
        f"O={output}",
        f"BR2_EXTERNAL={target.external_tree}",
    ]
    if target.board:
        argv.append(f"BR2_BOARD={target.board}")
    argv.append(target.defconfig)
    return tuple(argv)


def configure_target(
    target: Target,
    build_root: Path | str | None = None,
    *,
    env: dict[str, str] | None = None,
    gnu_dir: Path | str = hostshim.DEFAULT_GNU_DIR,
    resolver: Callable[[str], str | None] = hostshim.default_resolver,
    runner: Spawner | None = None,
    dry_run: bool = False,
    show_info: Callable[[Path, Path, dict[str, str]], str | None] | None = None,
) -> ConfigureResult:
    """Run the configure make invocation for ``target``.

    The Buildroot source tree must already exist (acquired); a missing tree
    is a :class:`ConfigureError` suggesting ``pengyos-build acquire``, and a
    missing external tree likewise.  Raises :class:`ConfigureError` if make
    is not found or exits non-zero (the message names the captured log).

    With ``dry_run=True`` the step resolves the exact make invocation and
    returns it WITHOUT running make: the same precondition checks apply
    (so the printed invocation is trustworthy), but no output dir is
    created, no log is written, and no host shim is applied.
    """
    env = os.environ if env is None else env
    root = Path(build_root) if build_root is not None else default_build_root(env)

    source = source_dir(root, target.br_version)
    if not source.is_dir():
        raise ConfigureError(
            f"Buildroot source tree not found at {source}; "
            f"run 'pengyos-build acquire {target.name}' first"
        )
    external = target.external_tree
    if not external.is_dir():
        raise ConfigureError(
            f"external tree not found at {external}; "
            "fetch it before configuring"
        )

    output = output_dir(root, target)
    argv = configure_argv(source, output, target)
    log = output / LOGS_SUBDIR / CONFIGURE_LOG
    if dry_run:
        # Print-only: no make, no output dir, no log, no host shim.
        return ConfigureResult(
            target=target.name, source=source, output=output, log=log, argv=argv
        )
    shim_dir = output / HOSTSHIM_DIRNAME
    spawner = runner if runner is not None else _default_runner
    config = output / ".config"
    old_config = config.read_text(encoding="utf-8") if config.is_file() else None
    info = show_info if show_info is not None else _show_info
    # Dependency snapshot of the build as it stands (stale.dependency_changes).
    old_info = info(source, output, env) if old_config is not None else None

    def _capturing(argv_: Sequence[str], env_: dict[str, str]) -> None:
        """Hostshim runner: run make with stdout+stderr merged into ``log``."""
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as log_file:
            try:
                proc = spawner(
                    list(argv_),
                    env=env_,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            except FileNotFoundError as exc:
                raise ConfigureError(
                    f"make not found on PATH "
                    f"({getattr(exc, 'filename', '') or 'make'}); "
                    "install build-essential"
                ) from exc
        if proc.returncode != 0:
            raise ConfigureError(
                f"configure failed (make exited {proc.returncode}); "
                f"see log: {log}"
            )

    try:
        hostshim.run_build_with_host_shim(
            argv,
            shim_dir,
            gnu_dir=gnu_dir,
            resolver=resolver,
            env=env,
            runner=_capturing,
        )
    except ConfigureError:
        raise
    except FileNotFoundError as exc:
        raise ConfigureError(
            f"make not found on PATH ({getattr(exc, 'filename', '') or 'make'}); "
            "install build-essential"
        ) from exc

    report = stale.StaleReport()
    rebuild: tuple[Path, ...] = ()
    if old_config is not None and config.is_file():
        report = stale.stale_packages(
            old_config, config.read_text(encoding="utf-8"), output / "build"
        )
        rebuild = report.rebuild
        new_info = info(source, output, env) if old_info is not None else None
        if old_info is not None and new_info is not None:
            try:
                dep_stale = stale.dependency_changes(
                    stale.parse_show_info(old_info),
                    stale.parse_show_info(new_info),
                    output / "build",
                )
            except ValueError:
                dep_stale = ()
            rebuild = tuple(sorted(set(rebuild) | set(dep_stale)))
        stale.remove_build_dirs(rebuild)

    return ConfigureResult(
        target=target.name,
        source=source,
        output=output,
        log=log,
        argv=argv,
        rebuilt=tuple(p.name for p in rebuild),
        disabled=report.disabled,
    )


def _show_info(source: Path, output: Path, env: dict[str, str]) -> str | None:
    """``make show-info`` for the configured output dir (None if it fails)."""
    child_env = {k: v for k, v in env.items() if k != "LD_LIBRARY_PATH"}
    try:
        proc = subprocess.run(
            ["make", "-s", "-C", str(source), f"O={output}", "show-info"],
            env=child_env, capture_output=True, text=True, check=False, timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _default_runner(argv: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), check=False, **kwargs)
