"""Run the ``flatpak``/``ostree`` CLI behind a mockable interface.

This module wraps the external ``flatpak`` and ``ostree`` commands the mirror
CLI needs: initialise a repo, add a remote, mirror a pinned ref, list refs,
verify signatures, and prune. It owns exactly two concerns:

1. **Command construction** — every operation builds a deterministic, *golden*
   argv (spec: "command construction ... golden argv"). The argv is the contract
   the tests assert against, so it is the single, stable, documented surface.
2. **Execution** — argv is handed to an injectable ``runner`` callable that
   defaults to :func:`subprocess.run`. Tests inject a fake that records argv and
   returns a canned :class:`Result`; no real flatpak/ostree, network, or root
   needed.

The higher-level orchestration (which refs to sync, how to interpret
``list``/``verify`` output, how to prune down to ``keep`` commits) lives in
later subtasks; this one guarantees the argv is correct and the execution
boundary is mockable.

Golden argv (documented here so the CLI can mirror them exactly)::

    init   flatpak build-update-repo --init <repo>
    sync   flatpak remote-add --if-not-exists <remote-name> <remote>
           flatpak update --user --noninteractive --commit=<commit> <id>@<arch>/<branch>
    pull   flatpak update --user --noninteractive --collection=<collection-id> <remote-name>
    list   ostree --repo=<repo> refs
    verify ostree --repo=<repo> verify --gpg-key=<key>
    prune  flatpak update --prune            (see NOTE on --keep below)

Note on ``prune``: neither ``flatpak update --prune`` nor ``ostree prune``
accepts a "keep N per ref" flag, so ``prune`` issues ``flatpak update --prune``
(now prunes old deployments) and records the requested ``keep`` on the returned
:class:`Result` as ``keep``. A real per-ref ``--keep`` flag is a follow-up.

Note on ``collection``: pulling a whole collection is a best-effort mirror —
``flatpak update --user --noninteractive --collection=<collection-id>
<remote-name>`` pulls every ref in the named collection from the registered
remote. The golden argv is pinned to the collection id and remote name, so
re-running issues the same call (idempotent).
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

__all__ = [
    "Result",
    "CommandError",
    "Runner",
    "run",
]


@dataclass(frozen=True)
class Result:
    """The outcome of a single external command.

    Attributes:
        argv: The exact argv that was run (the golden contract).
        returncode: The process exit code (0 == success).
        stdout: Captured standard output.
        stderr: Captured standard error.
        keep: For :meth:`Runner.prune`, the requested commits-to-keep count.
            ``None`` for every other operation.
    """

    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    keep: int | None = None


class CommandError(RuntimeError):
    """Raised when an external command exits non-zero.

    Carries the :class:`Result` so the CLI can print ``stderr`` and exit 1
    without a traceback. The CLI catches this and turns it into a clean
    exit-1 message.
    """

    def __init__(self, result: Result):
        self.result = result
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        super().__init__(f"{' '.join(result.argv)} failed: {detail}")


# The runner callable: takes an argv list, returns something normalisable to a
# Result. In production that is :func:`subprocess.run` (returns a
# CompletedProcess); in tests it is a fake that returns a Result.
RunnerFn = Callable[[list[str]], Any]


def _to_result(argv: list[str], outcome: Any, *, keep: int | None = None) -> Result:
    """Normalise ``outcome`` (a Result, CompletedProcess, int, or tuple) to a Result."""
    if isinstance(outcome, Result):
        # Re-wrap so the caller's ``keep`` (prune) is applied even when the
        # injected runner returns a pre-built Result with keep=None.
        return Result(
            argv=outcome.argv,
            returncode=outcome.returncode,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            keep=keep,
        )
    if isinstance(outcome, subprocess.CompletedProcess):
        return Result(
            argv=argv,
            returncode=outcome.returncode,
            stdout=outcome.stdout or "",
            stderr=outcome.stderr or "",
            keep=keep,
        )
    if isinstance(outcome, tuple):
        # Convenience for tests: (returncode, stdout, stderr).
        returncode, stdout, stderr = outcome
        return Result(argv=argv, returncode=returncode, stdout=stdout, stderr=stderr, keep=keep)
    if isinstance(outcome, int):
        return Result(argv=argv, returncode=outcome, keep=keep)
    raise TypeError(
        "runner must return a Result, subprocess.CompletedProcess, "
        f"(returncode, stdout, stderr) tuple, or int (got {type(outcome).__name__})"
    )


class Runner:
    """Run ``flatpak``/``ostree`` behind an injectable, mockable boundary.

    The default :attr:`runner` is :func:`subprocess.run`, so ``Runner()`` works
    against a real system. Tests pass a fake ``runner`` (and usually a frozen
    ``now``) to capture argv and canned results without touching the network,
    root, or a real flatpak install.

    Args:
        runner: Callable receiving an argv list and returning a normalisable
            outcome (see :func:`_to_result`). Defaults to :func:`subprocess.run`.
        now: Callable returning the current monotonic time; injected so
            :meth:`log` output is deterministic in tests.
        flatpak / ostree: The executable name for each tool (overridable in
            tests that point at a fake binary).
        logger: Optional callable ``msg -> None`` used by :meth:`log` so the
            CLI can surface progress without printing in tests.
    """

    def __init__(
        self,
        *,
        runner: RunnerFn = subprocess.run,
        now: Callable[[], float] = time.time,
        flatpak: str = "flatpak",
        ostree: str = "ostree",
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self._runner = runner
        self._now = now
        self.flatpak = flatpak
        self.ostree = ostree
        self._log = logger

    def _run(self, argv: list[str], *, keep: int | None = None) -> Result:
        """Run ``argv`` through the injected runner and raise on non-zero exit."""
        outcome = self._runner(argv)
        result = _to_result(argv, outcome, keep=keep)
        if result.returncode != 0:
            raise CommandError(result)
        return result

    def log(self, message: str) -> None:
        """Emit a progress line through the injected logger (no-op by default)."""
        if self._log is not None:
            self._log(message)

    # --- operations ---------------------------------------------------------

    def init_repo(self, repo: str) -> Result:
        """Initialise an empty OSTree repo to mirror into.

        Golden argv: ``flatpak build-update-repo --init <repo>``.
        """
        argv = [self.flatpak, "build-update-repo", "--init", repo]
        self.log(f"init: {' '.join(argv)}")
        return self._run(argv)

    def add_remote(self, remote_name: str, remote: str) -> Result:
        """Register the upstream remote (idempotent).

        Golden argv: ``flatpak remote-add --if-not-exists <remote-name> <remote>``.
        """
        argv = [self.flatpak, "remote-add", "--if-not-exists", remote_name, remote]
        self.log(f"remote: {' '.join(argv)}")
        return self._run(argv)

    def mirror_ref(self, app_id: str, arch: str, branch: str, commit: str) -> Result:
        """Mirror a single pinned ref at an exact commit (idempotent).

        Golden argv:
        ``flatpak update --user --noninteractive --commit=<commit> <id>@<arch>/<branch>``
        """
        ref = f"{app_id}@{arch}/{branch}"
        argv = [
            self.flatpak,
            "update",
            "--user",
            "--noninteractive",
            f"--commit={commit}",
            ref,
        ]
        self.log(f"sync: {' '.join(argv)}")
        return self._run(argv)

    def pull_collection(self, collection_id: str, remote_name: str) -> Result:
        """Pull every ref in a collection from the registered remote (idempotent).

        Golden argv:
        ``flatpak update --user --noninteractive --collection=<collection-id> <remote-name>``
        """
        argv = [
            self.flatpak,
            "update",
            "--user",
            "--noninteractive",
            f"--collection={collection_id}",
            remote_name,
        ]
        self.log(f"sync: {' '.join(argv)}")
        return self._run(argv)

    def list_refs(self, repo: str) -> Result:
        """List refs in the local repo.

        Golden argv: ``ostree --repo=<repo> refs``.
        """
        argv = [self.ostree, f"--repo={repo}", "refs"]
        self.log(f"list: {' '.join(argv)}")
        return self._run(argv)

    def verify(self, repo: str, gpg_key: str) -> Result:
        """Verify the repo's GPG signatures.

        Golden argv: ``ostree --repo=<repo> verify --gpg-key=<key>``.
        """
        argv = [self.ostree, f"--repo={repo}", "verify", f"--gpg-key={gpg_key}"]
        self.log(f"verify: {' '.join(argv)}")
        return self._run(argv)

    def prune(self, repo: str, keep: int) -> Result:
        """Prune old deployments in the local repo.

        Golden argv: ``flatpak update --prune`` (records ``keep`` on the
        result; a per-ref ``--keep`` flag is a follow-up — see module docstring).
        """
        if keep < 0:
            raise ValueError(f"--keep must be non-negative (got {keep})")
        argv = [self.flatpak, "update", "--prune"]
        self.log(f"prune: {' '.join(argv)} (keep={keep})")
        return self._run(argv, keep=keep)


def run(argv: Iterable[str]) -> Result:
    """One-shot convenience: run ``argv`` and return a :class:`Result`.

    Thin wrapper over :class:`Runner` for callers that only need to execute an
    argv without tracking state. Raises :class:`CommandError` on non-zero exit.
    """
    return Runner()._run(list(argv))
