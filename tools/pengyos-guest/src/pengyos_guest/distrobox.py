"""Wrap the ``distrobox`` binary behind a :class:`Runner`.

Everything that touches the outside world goes through the mockable
:class:`~pengyos_guest.runner.Runner`. Production uses
:class:`~pengyos_guest.runner.SubprocessRunner`; tests swap in
:class:`~pengyos_guest.runner.FakeRunner` to assert on the exact argv that would
be executed and to avoid needing a real container runtime, network, or root.

:class:`Distrobox` builds the high-level guest commands (create / enter /
destroy / export / list). Low-level ``podman`` lifecycle lives in
:class:`pengyos_guest.podman.Podman`.
"""

from __future__ import annotations

from typing import Sequence

from pengyos_guest.runner import Runner, RunResult
from pengyos_guest.podman import SubprocessRunner

BINARY = "distrobox"


class Distrobox:
    """A thin, argv-building wrapper around the ``distrobox`` CLI.

    Every method builds the exact argv for a ``distrobox`` subcommand and hands
    it to the injected :class:`Runner`. The argv is the contract tests assert
    on; the :class:`Runner` is what keeps this testable offline.
    """

    def __init__(self, runner: Runner | None = None) -> None:
        # Default to a real subprocess runner when none is injected, so
        # production callers get real execution and tests always pass one in.
        self._runner = runner if runner is not None else SubprocessRunner()

    @property
    def runner(self) -> Runner:
        return self._runner

    def _run(
        self,
        args: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        stream: bool = False,
    ) -> RunResult:
        """Build ``distrobox <args>`` and execute it through the runner."""
        return self._runner.run([BINARY, *args], env=env, stream=stream)

    def create(
        self,
        name: str,
        image: str,
        mounts: Sequence[str] = (),
        *,
        nvidia: bool = False,
        stream: bool = False,
    ) -> RunResult:
        """``distrobox create --yes --name <name> --image <image> [--nvidia]
        [--volume ...]``.

        ``--yes`` pulls the image without asking. *mounts* are ``host:guest``
        (optional ``:mode``) bind-mount specs, each passed as ``--volume``.
        ``--nvidia`` shares the host's NVIDIA driver (libraries, devices) with
        the guest.
        """
        args: list[str] = ["create", "--yes", "--name", name, "--image", image]
        if nvidia:
            args.append("--nvidia")
        for mount in mounts:
            args += ["--volume", mount]
        return self._run(args, stream=stream)

    def enter(
        self,
        name: str,
        command: Sequence[str] | None = None,
        *,
        stream: bool = False,
    ) -> RunResult:
        """``distrobox enter <name>`` or ``distrobox enter <name> -- <cmd...>``.

        When *command* is given it is appended after ``--`` so it runs inside
        the guest. Without it, an interactive shell is launched (needs
        ``stream=True``).
        """
        args = ["enter", name]
        if command:
            args += ["--", *command]
        return self._run(args, stream=stream)

    def destroy(self, name: str) -> RunResult:
        """``distrobox rm --force <name>`` (stops it first; non-interactive)."""
        return self._run(["rm", "--force", name])

    def list(self) -> RunResult:
        """``distrobox list``."""
        return self._run(["list"])

    def export_app(self, name: str, app: str) -> RunResult:
        """``distrobox enter <name> -- distrobox-export --app <app>``.

        Puts the guest application's desktop entry (and icon) on the host, so
        it appears in the host's app launcher and starts inside the guest.
        """
        return self.enter(name, ["distrobox-export", "--app", app])

    def run(
        self,
        args: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        stream: bool = False,
    ) -> RunResult:
        """Raw ``distrobox <args>`` passthrough for anything not covered above."""
        return self._run(args, env=env, stream=stream)
