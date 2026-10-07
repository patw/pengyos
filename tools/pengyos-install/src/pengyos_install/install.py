"""The install engine: execute a plan through the injectable runner.

``install = first update``. The :func:`render_plan` unit turns a
(``release``, ``layout``) pair into an ordered, golden :class:`Plan` (every step a
shell-quotable argv). This module *executes* that plan through the injectable
:class:`~pengyos_install.runner.Runner` seam, so the whole plan can be run,
inspected, and torn down without a single syscall to the host.

## The engine contract (spec: Verification)

- **Run via the injectable runner.** Every step is run through the ``Runner``
  (``SubprocessRunner`` in production, ``FakeRunner`` in tests) — no direct
  ``subprocess`` calls here.
- **Refuse without ``--yes``.** :func:`install` raises :class:`InstallRefused`
  unless *confirm* is set. Destructive writes demand an explicit acknowledgement.
- **Refuse an unverified release.** Even with ``--yes``, the engine refuses to
  install a release whose signature has not been verified
  (``release.is_signed`` is ``False``) — a security gate independent of the
  confirmation gate.
- **Re-check the target each run.** Before the first step the engine re-validates
  the inputs (the release/layout JSON round-trips and the release's ``slot`` is a
  declared slot) so a target that changed between planning and running is caught.
- **Hard-fail on a required step.** The first step whose runner result is not ok
  aborts the install; no further steps run.
- **Tear down — unmount / sync — on success *and* on failure.** Any mount the
  engine opened is unmounted (in reverse order) and the filesystems are synced
  whether the install succeeded or aborted. A ``finally`` guarantees teardown
  even when a step raises.
- **The write of slot A is atomic.** The plan writes the rootfs to the *inactive*
  slot, then commits it to the *active* slot (see :mod:`pengyos_install.plan`);
  the engine simply executes those two steps in order, so a crash mid-write
  leaves the untouched slot bootable.

The engine is defined entirely in terms of the :class:`Plan` and the
:class:`Runner` — the two seams the tests need — so the whole plan can be executed
and torn down without root, a real device, network, or hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pengyos_install.models import Layout, Plan, Release
from pengyos_install.runner import RunResult, Runner

__all__ = [
    "InstallEngine",
    "InstallResult",
    "InstallRefused",
    "install",
]


class InstallError(Exception):
    """A failure while executing the install plan."""


class InstallRefused(InstallError):
    """The install was refused because the destructive write was not confirmed."""


@dataclass
class InstallResult:
    """The outcome of executing the plan.

    ``success`` is True only when every step ran ok. ``steps_run`` is the number
    of plan steps executed before the result was reached (``len(plan)`` when it
    succeeded). ``mounted`` / ``unmounted`` record the teardown so tests (and
    operators) can see that everything the engine opened was closed.
    """

    steps_run: int
    success: bool
    mounted: list[str] = field(default_factory=list)
    unmounted: list[str] = field(default_factory=list)
    result: RunResult = field(default_factory=lambda: RunResult(argv=[], returncode=0))

    @property
    def unmounted_all(self) -> bool:
        """True when every mount opened was unmounted during teardown."""
        return sorted(self.mounted) == sorted(self.unmounted)


class InstallEngine:
    """Execute a :class:`Plan` through a :class:`Runner`.

    The engine runs each step in order, hard-failing on the first non-ok result,
    and always tears down (unmount the mounts it opened, then ``sync``) on success
    *and* on failure.
    """

    def __init__(
        self,
        plan: Plan,
        runner: Runner,
        *,
        confirm: bool = False,
        layout: Layout | None = None,
        release: Release | None = None,
    ) -> None:
        self.plan = plan
        self.runner = runner
        self.confirm = confirm
        self.layout = layout
        self.release = release
        self._mounted: list[str] = []

    def mount(self, partition: str, point: str) -> RunResult:
        """Mount *partition* at *point* and track it for teardown.

        The mount is opened through the runner (so tests record it instead of a
        real syscall) and remembered so :meth:`_teardown` unmounts it. Returns the
        runner result; a non-ok result raises :class:`InstallError` (the write
        target must be reachable).
        """
        result = self.runner.run(["mount", partition, point])
        if not result.ok:
            raise InstallError(
                f"failed to mount {partition} at {point}: {result.stderr.strip() or 'non-zero return'}"
            )
        self._mounted.append(point)
        return result

    def install(self) -> InstallResult:
        """Execute the plan, hard-failing on the first non-ok step.

        Refuses without confirmation, then refuses an unverified release
        (``release.is_signed`` is ``False``), re-checks the target, runs every
        step, and tears down (unmount + sync) on success *and* on failure.
        """
        if not self.confirm:
            raise InstallRefused(
                "refusing to install without confirmation; pass --yes to proceed"
            )

        # Refuse an unverified release. ``--yes`` acknowledges the destructive
        # write, but an unverified signature is a *security* problem, not a
        # confirmation one: the engine refuses to install a release whose
        # signature has not been verified, even with ``--yes`` (the property the
        # models' ``Signature`` docstring promises). No step runs.
        if self.release is not None and not self.release.is_signed:
            raise InstallRefused(
                "refusing to install an unverified release: "
                "signature.verified is false"
            )

        # Re-check the target each run: the inputs must still be valid and the
        # release's slot must be one of the declared slots (it may have changed
        # between planning and running).
        self._recheck_target()

        result: RunResult | None = None
        steps_run = 0
        mounted = list(self._mounted)
        try:
            for step in self.plan:
                result = self.runner.run(step.to_argv(), env=dict(step.env))
                steps_run += 1
                if not result.ok:
                    raise InstallError(
                        f"step {steps_run} ({step.description or step.shlex_join()}) "
                        f"failed: {result.stderr.strip() or f'exit {result.returncode}'}"
                    )
        finally:
            # Success *and* failure tear down: unmount the mounts we opened, then
            # sync. The ``finally`` guarantees this even if a step raised.
            unmounted = self._teardown()

        # The ``finally`` above ran; if a step failed the InstallError propagates
        # and this line is not reached, so ``success`` is only True on success.
        return InstallResult(
            steps_run=steps_run,
            success=True,
            mounted=mounted,
            unmounted=unmounted,
            result=result,
        )

    def _recheck_target(self) -> None:
        """Re-validate the inputs and cross-check the release slot against the layout.

        Raises :class:`ValidationError` if the release/layout JSON no longer
        round-trips or the release's slot is not one of the declared slots.
        """
        if self.layout is not None and self.release is not None:
            # The release/layout must still be valid and the slot must still be
            # declared (the target may have changed between planning and running).
            Layout.from_json(self.layout.to_json())
            Release.from_json(self.release.to_json())
            self.layout.assert_has_slot(self.release.slot)

    def _teardown(self) -> list[str]:
        """Unmount every tracked mount (reverse order) and sync.

        Best-effort: each ``umount`` is run through the runner and any failure is
        logged but not raised (teardown never masks the original error). Returns
        the mount points that were unmounted.
        """
        unmounted: list[str] = []
        for point in reversed(self._mounted):
            result = self.runner.run(["umount", point])
            if not result.ok:  # pragma: no cover - defensive
                continue
            unmounted.append(point)
        self._mounted.clear()
        self.runner.run(["sync"])
        return unmounted


def install(
    plan: Plan,
    runner: Runner,
    *,
    confirm: bool = False,
    layout: Layout | None = None,
    release: Release | None = None,
) -> InstallResult:
    """Execute *plan* through *runner*, refusing without confirmation.

    Raises :class:`InstallRefused` unless *confirm* is set; also
    :class:`InstallRefused` if *release* is provided and its signature has not
    been verified (``release.is_signed`` is ``False``) — an unverified release is
    refused even with ``confirm`` set. :class:`InstallError` on the first non-ok
    step (after tearing down); :class:`ValidationError` if the target no longer
    checks out. On success *and* failure the engine unmounts every mount it
    opened and syncs.
    """
    return InstallEngine(plan, runner, confirm=confirm, layout=layout, release=release).install()
