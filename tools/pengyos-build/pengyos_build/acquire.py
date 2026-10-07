"""Acquire a pinned Buildroot source tree.

Given a Buildroot release version (``br_version`` from ``targets.toml``,
e.g. ``2026.02.3``), ensure a source tree exists at a deterministic
location, pinned to the matching upstream git tag:

* **fresh** — ``git clone --filter=blob:none --no-checkout`` (blobless:
  blobs are fetched lazily from the upstream remote, which is already the
  transport the first builds used) followed by ``git checkout <tag>``;
* **re-entry** — if the tree already exists and is a git repository:
  - HEAD already equals the tag's commit → do nothing (``up-to-date``);
  - the tag is known locally but HEAD differs → ``git checkout <tag>``;
  - the tag is not known locally → ``git fetch --tags`` then checkout.

The tree lives at ``<build-root>/buildroot-<version>`` where the build
root defaults to ``$PENGYOS_BUILD_ROOT`` or ``~/build/pengyos``.  Pinning
is per *version*: a target that pins a different version gets a
different directory, so a pin change is a fresh acquisition, never an
in-place upgrade of a tree that may hold in-progress build state.

A path that exists but is not a git repository is an error — acquisition
never deletes or overwrites user data.

``acquire_buildroot`` takes an injectable ``runner`` (default
``subprocess.run``) so the command sequence is unit-testable without
network, root, or git in the test environment; a PENGYOS_IT=1
integration test exercises real git against a local file:// remote.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .targets import Target

#: Upstream Buildroot git remote (spec: canonical source of truth).
BUILDROOT_REMOTE = "https://git.buildroot.net/buildroot.git"

#: Environment variable overriding the default build root.
BUILD_ROOT_ENV = "PENGYOS_BUILD_ROOT"


class AcquireError(RuntimeError):
    """Buildroot acquisition failed (bad path, git failure, pin mismatch)."""


@dataclass(frozen=True)
class AcquireResult:
    """Outcome of an acquisition step.

    ``action`` is one of ``"cloned"``, ``"checked-out"``,
    ``"fetched-checked-out"`` or ``"up-to-date"``; ``commands`` is the
    exact argv of every git invocation executed (empty when the tree was
    already up to date) — the seed for later ``--dry-run`` and manifest
    reporting.
    """

    path: Path
    tag: str
    commit: str
    action: str
    commands: tuple[tuple[str, ...], ...] = field(default=())

    def __str__(self) -> str:  # convenient one-line summary for the CLI
        return f"{self.action}: {self.path} @ {self.tag} ({self.commit[:12]})"


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def buildroot_tag(version: str) -> str:
    """Buildroot release tags are the bare version (e.g. ``2026.02.3``)."""
    return version


def default_build_root(env: dict[str, str] | None = None) -> Path:
    """Build root: ``$PENGYOS_BUILD_ROOT`` if set, else ``~/build/pengyos``."""
    env = os.environ if env is None else env
    override = env.get(BUILD_ROOT_ENV)
    if override:
        return Path(override)
    return Path.home() / "build" / "pengyos"


def source_dir(build_root: Path | str, version: str) -> Path:
    """Deterministic per-version source directory: ``buildroot-<version>``."""
    return Path(build_root) / f"buildroot-{version}"


def _run(runner: Runner, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    proc = runner(list(argv))
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        detail = f": {stderr}" if stderr else ""
        raise AcquireError(
            f"git command failed (exit {proc.returncode}): "
            f"{' '.join(str(a) for a in argv)}{detail}"
        )
    return proc


def _run_quiet(
    runner: Runner, argv: Sequence[str]
) -> subprocess.CompletedProcess[str]:
    """Like :func:`_run` but a non-zero exit is not an error (``--quiet``)."""
    return runner(list(argv))


def acquire_buildroot(
    version: str,
    dest: Path | str,
    *,
    remote: str = BUILDROOT_REMOTE,
    runner: Runner | None = None,
) -> AcquireResult:
    """Ensure ``dest`` holds a Buildroot checkout pinned to ``version``.

    Returns an :class:`AcquireResult` describing what happened; idempotent
    re-entry executes zero git commands.  Raises :class:`AcquireError` if
    ``dest`` exists but is not a git work tree, or any git command fails.
    """
    runner = _default_runner if runner is None else runner
    dest = Path(dest)
    tag = buildroot_tag(version)
    commands: list[tuple[str, ...]] = []
    action: str

    if dest.is_file():
        raise AcquireError(f"{dest} exists and is not a directory")
    if dest.exists():
        if not (dest / ".git").exists():
            raise AcquireError(
                f"{dest} exists but is not a git repository; "
                "refusing to overwrite it (remove it or choose another path)"
            )
        # Read-only probes use throwaway lists: ``commands`` records only
        # mutating invocations, so "up-to-date" leaves it empty.
        head = _current_head(runner, dest, [])
        tag_commit = _local_tag_commit(runner, dest, tag, [])
        if tag_commit is not None and head == tag_commit:
            action = "up-to-date"
        elif tag_commit is not None:
            _run(runner, ["git", "-C", str(dest), "checkout", tag])
            commands.append(("git", "-C", str(dest), "checkout", tag))
            action = "checked-out"
        else:
            _run(
                runner,
                ["git", "-C", str(dest), "fetch", "--filter=blob:none", "--tags", remote],
            )
            commands.append(
                ("git", "-C", str(dest), "fetch", "--filter=blob:none", "--tags", remote)
            )
            _run(runner, ["git", "-C", str(dest), "checkout", tag])
            commands.append(("git", "-C", str(dest), "checkout", tag))
            action = "fetched-checked-out"
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _run(
            runner,
            ["git", "clone", "--filter=blob:none", "--no-checkout", remote, str(dest)],
        )
        commands.append(
            ("git", "clone", "--filter=blob:none", "--no-checkout", remote, str(dest))
        )
        _run(runner, ["git", "-C", str(dest), "checkout", tag])
        commands.append(("git", "-C", str(dest), "checkout", tag))
        action = "cloned"

    commit = _verify_head_is_tag(runner, dest, tag)
    return AcquireResult(
        path=dest, tag=tag, commit=commit, action=action, commands=tuple(commands)
    )


def acquire_target_tree(
    target: Target,
    build_root: Path | str | None = None,
    *,
    remote: str = BUILDROOT_REMOTE,
    runner: Runner | None = None,
) -> AcquireResult:
    """Acquire the Buildroot tree for a parsed :class:`Target`."""
    root = Path(build_root) if build_root is not None else default_build_root()
    return acquire_buildroot(
        target.br_version, source_dir(root, target.br_version), remote=remote, runner=runner
    )


def _default_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), capture_output=True, text=True, check=False)


def _current_head(
    runner: Runner, dest: Path, commands: list[tuple[str, ...]]
) -> str:
    """HEAD commit of the work tree, or ``""`` if it has no resolvable HEAD."""
    argv = ["git", "-C", str(dest), "rev-parse", "--quiet", "HEAD"]
    proc = _run_quiet(runner, argv)
    commands.append(tuple(argv))
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _local_tag_commit(
    runner: Runner, dest: Path, tag: str, commands: list[tuple[str, ...]]
) -> str:
    """Commit the tag resolves to *locally*, or ``None`` if the tag is unknown."""
    argv = ["git", "-C", str(dest), "rev-parse", "--quiet", "--verify", f"refs/tags/{tag}^{{commit}}"]
    proc = _run_quiet(runner, argv)
    commands.append(tuple(argv))
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def _verify_head_is_tag(runner: Runner, dest: Path, tag: str) -> str:
    """Return HEAD, failing hard if it is not the pinned tag's commit."""
    head = _run(runner, ["git", "-C", str(dest), "rev-parse", "HEAD"])
    commit = (head.stdout or "").strip()
    if commit != _local_tag_commit(runner, dest, tag, []):
        raise AcquireError(
            f"{dest} is not at the pinned tag {tag} after acquisition "
            f"(HEAD is {commit[:12]})"
        )
    return commit
