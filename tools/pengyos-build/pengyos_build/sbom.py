"""Generate a signed SBOM for a built PengyOS target (SPEC provenance / US-CORE-6).

Buildroot already knows everything that went into an image; this step wires its
own manifests into the ``pengyos-sbom`` tool so every image can ship a
verifiable bill of materials:

1. ``make legal-info``  → ``<out>/legal-info/`` (licenses, sources, manifest)
2. ``make show-info``   → ``<out>/images/<target>.show-info.json``
3. ``pengyos-sbom generate`` → ``<out>/images/<target>.spdx.json`` (+ ``.cdx.json``)
4. optional ``pengyos-sbom sign`` with a build key → ``<spdx>.sig``

With ``embed=True`` the build make is re-run afterwards so the board's
post-build script bakes the SBOM (and signature) into the rootfs under
``/etc/pengyos/`` — i.e. the image carries its own ingredient list.

Steps 1–2 run through the same GNU host shim as the build (a uutils ``install``
host would otherwise break ``make legal-info``). The ``pengyos-sbom`` tool is
located next to this repo (``tools/pengyos-sbom``) when ``repo_root`` is given,
else ``pengyos-sbom`` is expected on ``PATH``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import hostshim
from .acquire import default_build_root, source_dir
from .build import BuildError, build_target, default_jobs
from .configure import HOSTSHIM_DIRNAME, LOGS_SUBDIR, output_dir
from .targets import Target

#: Buildroot goal names driven here.
LEGAL_INFO_GOAL = "legal-info"
SHOW_INFO_GOAL = "show-info"

#: Where the sibling SBOM tool lives, relative to the repo root.
SBOM_TOOL_PARTS: tuple[str, ...] = ("tools", "pengyos-sbom")

Spawner = Callable[..., subprocess.CompletedProcess]


class SbomError(RuntimeError):
    """SBOM generation failed (missing tree/config, make error, tool error)."""


@dataclass(frozen=True)
class SbomResult:
    """Outcome of the SBOM step."""

    target: str
    output: Path
    legal_info: Path
    show_info: Path
    spdx: Path
    cdx: Path
    signature: "Path | None"
    embedded: bool

    def __str__(self) -> str:  # one-line summary for the CLI
        extra = " (signed)" if self.signature else ""
        where = "embedded in image + " if self.embedded else ""
        return (
            f"SBOM for {self.target}: {where}{self.spdx}{extra} "
            f"(+{self.cdx.name})"
        )


def _make_argv(
    source: Path, output: Path, target: Target, jobs: int, goal: str, *, parallel: bool
) -> tuple[str, ...]:
    """Exact make argv for a Buildroot metadata goal (``legal-info``/``show-info``)."""
    argv = [
        "make",
        "--no-print-directory",
        "-C",
        str(source),
        f"O={output}",
        f"BR2_EXTERNAL={target.external_tree}",
    ]
    if target.board:
        argv.append(f"BR2_BOARD={target.board}")
    if parallel:
        argv.append(f"-j{jobs}")
    argv.append(goal)
    return tuple(argv)


def _sbom_tool(repo_root: "Path | None") -> tuple[list[str], "Path | None"]:
    """Resolve how to invoke pengyos-sbom: ``(argv_prefix, tool_dir_or_None)``."""
    if repo_root is not None:
        tool_dir = Path(repo_root).joinpath(*SBOM_TOOL_PARTS)
        if (tool_dir / "pengyos_sbom").is_dir():
            return [sys.executable, "-m", "pengyos_sbom"], tool_dir
    return ["pengyos-sbom"], None


def generate_sbom(
    target: Target,
    build_root: "Path | str | None" = None,
    *,
    jobs: int | None = None,
    embed: bool = False,
    sign_key: "Path | str | None" = None,
    repo_root: "Path | str | None" = None,
    env: "dict[str, str] | None" = None,
    gnu_dir: Path | str = hostshim.DEFAULT_GNU_DIR,
    resolver: Callable[[str], str | None] = hostshim.default_resolver,
    runner: Spawner | None = None,
    tool_runner: Spawner | None = None,
) -> SbomResult:
    """Generate (and optionally sign/embed) the SBOM for a built ``target``.

    Preconditions mirror :func:`pengyos_build.build.build_target`: the tree must
    be acquired and the target configured. Run a build first, or use
    ``build_target`` then this step. Raises :class:`SbomError` on any failure.
    """
    env = os.environ if env is None else env
    root = Path(build_root) if build_root is not None else default_build_root(env)
    try:
        resolved = default_jobs() if jobs is None else jobs
    except BuildError as exc:
        raise SbomError(str(exc)) from exc
    if resolved < 1:
        raise SbomError(f"--jobs must be a positive integer, got {resolved}")

    source = source_dir(root, target.br_version)
    if not source.is_dir():
        raise SbomError(
            f"Buildroot source tree not found at {source}; "
            f"run 'pengyos-build acquire {target.name}' first"
        )
    output = output_dir(root, target)
    if not output.is_dir():
        raise SbomError(
            f"target '{target.name}' is not configured (no output dir at {output}); "
            f"run 'pengyos-build configure {target.name}' first"
        )

    images = output / "images"
    images.mkdir(parents=True, exist_ok=True)
    logs = output / LOGS_SUBDIR
    logs.mkdir(parents=True, exist_ok=True)
    legal_info = output / "legal-info"
    show_info = images / f"{target.name}.show-info.json"
    spdx = images / f"{target.name}.spdx.json"
    cdx = images / f"{target.name}.cdx.json"
    signature = Path(str(spdx) + ".sig") if sign_key else None
    shim_dir = output / HOSTSHIM_DIRNAME
    spawner = runner if runner is not None else _default_runner

    # 1. legal-info (builds deps + collects licenses/sources).
    _make(
        _make_argv(source, output, target, resolved, LEGAL_INFO_GOAL, parallel=True),
        shim_dir, gnu_dir, resolver, env, spawner,
        log=logs / "legal-info.log", what="legal-info",
    )
    if not legal_info.is_dir():
        raise SbomError(
            f"make legal-info exited 0 but produced no {legal_info} "
            "(is this a configured Buildroot 2026.08.x tree?)"
        )

    # 2. show-info (metadata JSON on stdout).
    _make(
        _make_argv(source, output, target, resolved, SHOW_INFO_GOAL, parallel=False),
        shim_dir, gnu_dir, resolver, env, spawner,
        log=logs / "show-info.log", capture=show_info, what="show-info",
    )

    # An image embeds every SBOM file in images/ (post-build copies them all), so
    # drop any left behind under another name — a renamed target kept shipping
    # its old-named SBOM next to the new one.
    _remove_stale(images, target.name)

    # 3. -> SBOM (+ CycloneDX).
    _sbom(
        repo_root,
        ["generate", "--legal-info", str(legal_info), "--show-info", str(show_info),
         "--out", str(spdx), "--cdx", str(cdx)],
        env, tool_runner,
    )

    # 4. optional detached signature.
    if sign_key is not None:
        _sbom(repo_root, ["sign", str(spdx), "--key", str(sign_key)], env, tool_runner)

    # 5. embed: re-run the build so post-build bakes the SBOM into the rootfs.
    embedded = False
    if embed:
        build_target(target, build_root, jobs=resolved, env=env, gnu_dir=gnu_dir,
                     resolver=resolver, runner=runner)
        embedded = True

    return SbomResult(
        target=target.name, output=output, legal_info=legal_info,
        show_info=show_info, spdx=spdx, cdx=cdx, signature=signature,
        embedded=embedded,
    )


#: The SBOM artifacts this step writes into ``images/``, by suffix.
SBOM_SUFFIXES = (".spdx.json", ".spdx.json.sig", ".cdx.json", ".show-info.json")


def _remove_stale(images: Path, keep: str) -> list[Path]:
    """Delete SBOM artifacts in ``images`` that are not named after ``keep``."""
    removed = []
    for f in sorted(images.iterdir()):
        for suffix in SBOM_SUFFIXES:
            if f.name.endswith(suffix) and f.name != keep + suffix:
                f.unlink()
                removed.append(f)
                break
    return removed


def _make(
    argv: Sequence[str], shim_dir: Path, gnu_dir, resolver, env, spawner: Spawner,
    *, log: Path, what: str, capture: "Path | None" = None,
) -> None:
    """Run a Buildroot metadata goal through the host shim."""
    log.parent.mkdir(parents=True, exist_ok=True)

    def runner(a: Sequence[str], e: dict) -> subprocess.CompletedProcess:
        if capture is not None:
            with capture.open("w", encoding="utf-8") as out_f, log.open("w", encoding="utf-8") as log_f:
                return spawner(list(a), env=e, stdout=out_f, stderr=log_f, text=True)
        with log.open("w", encoding="utf-8") as log_f:
            return spawner(list(a), env=e, stdout=log_f, stderr=subprocess.STDOUT, text=True)

    try:
        proc = hostshim.run_build_with_host_shim(
            argv, shim_dir, gnu_dir=gnu_dir, resolver=resolver, env=env, runner=runner
        )
    except FileNotFoundError as exc:
        raise SbomError(
            f"make not found on PATH ({getattr(exc, 'filename', '') or 'make'}); "
            "install build-essential"
        ) from exc
    if getattr(proc, "returncode", 0) != 0:
        raise SbomError(f"{what} failed (make exited {proc.returncode}); see log: {log}")


def _sbom(repo_root, args: list[str], env: dict, tool_runner: "Spawner | None") -> None:
    """Invoke the pengyos-sbom subcommand (``generate``/``sign``)."""
    prefix, tool_dir = _sbom_tool(repo_root)
    argv = [*prefix, *args]
    child_env = dict(env)
    if tool_dir is not None:
        child_env["PYTHONPATH"] = (
            str(tool_dir) + os.pathsep + child_env.get("PYTHONPATH", "")
        ).rstrip(os.pathsep)
    runner = tool_runner if tool_runner is not None else _default_runner
    try:
        proc = runner(argv, env=child_env, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise SbomError(
            f"pengyos-sbom not found ({argv[0]!r}); install it or pass --repo-root "
            "pointing at the PengyOS repo (tools/pengyos-sbom)"
        ) from exc
    if getattr(proc, "returncode", 0) != 0:
        detail = (getattr(proc, "stderr", "") or "").strip()
        raise SbomError(f"pengyos-sbom {args[0]} failed ({proc.returncode}): {detail}")


def _default_runner(argv: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), check=False, **kwargs)
