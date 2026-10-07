"""Record a build manifest for a PengyOS target.

A build manifest is a declarative, reproducible receipt of what went into a
build and what came out of it.  It is written *after* a target has been
configured and (optionally) built, and records:

* **inputs** — the declarative inputs and their identity:
  - the target name;
  - the pinned ``br_version``;
  - the ``defconfig`` name **and its SHA-256** (so a config change is visible
    in the manifest);
  - the ``BR2_BOARD`` board (only when the target defines one);
  - the ``BR2_EXTERNAL`` tree path **and its ``git describe``** (so an
    external-tree change is visible in the manifest).
* **outputs** — the SHA-256 of every output image Buildroot produced under
  ``<output>/images`` (Buildroot's default ``BINARIES_DIR`` with ``O=…``),
  keyed by path relative to the images dir, so a rebuilt image that changes
  bytes is visible in the manifest.
* **timing** — a ``started_at`` / ``finished_at`` window derived from the
  artifacts' mtimes (earliest to latest of the build log + images), plus a
  wall-clock ``generated_at`` for when the manifest itself was written.
* **versioning** — the ``pengyos-build`` tool version that produced the
  manifest, and a ``schema_version`` for forward compatibility.

Design constraints (spec):

* the manifest is a **pure read** of the build outputs and the external
  tree — it never runs make, never touches the network, and never writes
  anything except the manifest file itself;
* output is **deterministic**: JSON with sorted keys and a fixed indent, so
  two runs over identical artifacts produce byte-identical files (``sha256``
  and ``git describe`` are deterministic; the only time-dependent fields are
  the mtime-derived window and the injectable ``generated_at`` clock);
* the ``git describe`` of the external tree and the image/defconfig hashing
  are the reproducible inputs the manifest exists to pin.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from . import __version__
from .acquire import default_build_root
from .build import build_log_path, detect_stage
from .configure import LOGS_SUBDIR, output_dir
from .targets import Target

#: Name of the default manifest file (inside the per-target output dir).
MANIFEST_NAME = "build-manifest.json"

#: First on-disk manifest schema (bump to change the manifest shape).
SCHEMA_VERSION = 1

#: Buildroot's default output-images directory, relative to the ``O=`` dir.
IMAGES_SUBDIR = "images"


class ManifestError(RuntimeError):
    """Manifest generation failed (missing external tree / defconfig, I/O)."""


#: A git-runner callable: ``runner(argv) -> CompletedProcess``.  Injectable
#: so the external-tree ``git describe`` is testable without real git.
GitRunner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _sha256_file(path: Path) -> str:
    """SHA-256 hex digest of a file's contents (streamed, not loaded)."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def defconfig_path(target: Target) -> Path:
    """Path of the target's defconfig inside the external tree's ``configs/``.

    Buildroot resolves a bare defconfig name in ``$BR2_EXTERNAL/configs/``,
    so the manifest hashes exactly the file ``make`` would pick up.
    """
    return target.external_tree / "configs" / target.defconfig


def collect_images(output: Path) -> dict[str, str]:
    """Map each file under ``<output>/images`` to its SHA-256.

    Keys are the file's path **relative to the images dir** (POSIX separators),
    sorted by path; a missing images dir yields an empty mapping (the target
    simply hasn't built images yet).
    """
    images_dir = output / IMAGES_SUBDIR
    if not images_dir.is_dir():
        return {}
    result: dict[str, str] = {}
    for path in sorted(images_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(images_dir).as_posix()
            result[rel] = _sha256_file(path)
    return result


def external_tree_describe(
    external_tree: Path | str, *, runner: GitRunner | None = None
) -> str | None:
    """Return the external tree's ``git describe --always --dirty`` string.

    ``None`` when the tree is not a git work tree or ``git describe`` fails —
    a non-git external tree still yields a valid manifest (the ``git
    describe`` field is simply absent from the identity, and the tree's
    contents are still pinned by the defconfig hash).  The runner is
    injectable so tests run hermetically.
    """
    external_tree = Path(external_tree)
    if not (external_tree / ".git").exists():
        return None
    git = runner if runner is not None else _default_git_runner
    proc = git(["git", "-C", str(external_tree), "describe", "--always", "--dirty"])
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def build_window(output: Path) -> tuple[str | None, str | None]:
    """Derive ``(started_at, finished_at)`` ISO-8601 UTC timestamps.

    The window is derived from the artifacts on disk: the earliest mtime of
    the build log and every output image is ``started_at``; the latest is
    ``finished_at``.  ``None`` for both when there is nothing to measure
    (an unconfigured / unbuilt target).  mtimes are the only durable build
    timing the output dir keeps, so the manifest is self-contained and never
    needs the build to have recorded a wall-clock duration.
    """
    candidates: list[float] = []
    log = output / LOGS_SUBDIR / "build.log"
    if log.is_file():
        candidates.append(log.stat().st_mtime)
    images_dir = output / IMAGES_SUBDIR
    if images_dir.is_dir():
        for path in images_dir.rglob("*"):
            if path.is_file():
                candidates.append(path.stat().st_mtime)
    if not candidates:
        return (None, None)
    started = datetime.fromtimestamp(min(candidates), tz=timezone.utc)
    finished = datetime.fromtimestamp(max(candidates), tz=timezone.utc)
    return (started.isoformat(), finished.isoformat())


def utc_iso(clock: Callable[[], datetime] | None) -> str:
    """Wall-clock ``generated_at`` as ISO-8601 UTC (injectable clock)."""
    now = (clock or _now_utc)()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).isoformat()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def build_manifest(
    target: Target,
    build_root: Path | str | None = None,
    *,
    env: dict[str, str] | None = None,
    clock: Callable[[], datetime] | None = None,
    git_runner: GitRunner | None = None,
) -> dict:
    """Assemble the manifest dict for ``target`` (pure read of the outputs).

    Raises :class:`ManifestError` if the external tree or its defconfig is
    missing (the manifest must hash a real config to be meaningful).
    """
    env = os.environ if env is None else env
    root = Path(build_root) if build_root is not None else default_build_root(env)

    external = target.external_tree
    if not external.is_dir():
        raise ManifestError(
            f"external tree not found at {external}; fetch it before writing "
            "the manifest"
        )
    dcfg = defconfig_path(target)
    if not dcfg.is_file():
        raise ManifestError(
            f"defconfig not found at {dcfg}; the external tree must contain "
            f"configs/{target.defconfig}"
        )

    output = output_dir(root, target)
    started, finished = build_window(output)

    inputs: dict = {
        "br_version": target.br_version,
        "defconfig": target.defconfig,
        "defconfig_sha256": _sha256_file(dcfg),
        "external_tree": str(external),
        "external_tree_describe": external_tree_describe(
            external, runner=git_runner
        ),
    }
    if target.board:
        inputs["board"] = target.board

    log = build_log_path(root, target)
    stage = detect_stage(log.read_text(encoding="utf-8")) if log.is_file() else "not_started"

    return {
        "schema_version": SCHEMA_VERSION,
        "target": target.name,
        "pengyos_build_version": __version__,
        "generated_at": utc_iso(clock),
        "inputs": inputs,
        "build": {
            "started_at": started,
            "finished_at": finished,
            "stage": stage,
            "images": collect_images(output),
        },
    }


def manifest_default_path(output: Path) -> Path:
    """Default manifest location: ``<output>/build-manifest.json``."""
    return output / MANIFEST_NAME


def write_manifest(
    target: Target,
    build_root: Path | str | None = None,
    *,
    out_path: Path | str | None = None,
    env: dict[str, str] | None = None,
    clock: Callable[[], datetime] | None = None,
    git_runner: GitRunner | None = None,
) -> Path:
    """Build the manifest and write it as pretty, deterministic JSON.

    ``out_path`` defaults to the per-target
    ``<output>/build-manifest.json``; parents are created as needed.  Keys are
    sorted and the indent is fixed so identical inputs yield identical bytes.
    Returns the path written.
    """
    root = Path(build_root) if build_root is not None else default_build_root(
        env or os.environ
    )
    data = build_manifest(
        target,
        root,
        env=env,
        clock=clock,
        git_runner=git_runner,
    )
    destination = (
        Path(out_path)
        if out_path is not None
        else manifest_default_path(output_dir(root, target))
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return destination


def _default_git_runner(argv: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True, check=False)
