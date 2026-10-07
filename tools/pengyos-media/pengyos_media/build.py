"""Drive a medium plan through an injectable runner (spec.md,
item 4).

``build`` (item 4) is the executor half of the deliverable: it takes the
serialisable :class:`~pengyos_media.model.MediumPlan` that ``plan`` emitted,
turns it into the ordered, argv-per-step build recipe (a pure function of the
plan, :func:`pengyos_media.steps.build_steps`), and runs those steps through an
injectable :class:`~pengyos_media.runner.Runner`.

Two modes:

* **``--dry-run``** (the default) — render every step's argv and run *nothing*.
  It is side-effect free: no tool is spawned, no file is written, no loop
  device is attached. This is what makes the build reproducible and testable:
  the same plan always renders the same argv, and ``build --dry-run`` leaves the
  working tree untouched.
* **``--commit``** — actually run the steps through the runner. A step that
  exits non-zero is a build failure (:class:`BuildError`); a missing binary is
  a build failure too.

The runner is injectable (``build(…, runner=…)``): tests pass a
:class:`~pengyos_media.runner.RecordingRunner` so nothing is ever executed, and
the ``build`` CLI's ``--runner CMD`` flag wraps a runner around a command name
or path (see :mod:`pengyos_media.runner`).

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pengyos_media.model import MediumPlan
from pengyos_media.runner import Runner, RunResult, default_runner
from pengyos_media.steps import BuildStep, build_steps
from pengyos_media.validate import validate_plan


class BuildError(RuntimeError):
    """The build could not be driven (bad plan, missing tool, failed step).

    ``step`` names the failing step (when the failure is a non-zero exit);
    ``message`` is the human text. Raised by :func:`build` on ``--commit``.
    """

    def __init__(self, message: str, *, step: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.step = step


@dataclass(frozen=True)
class RenderedStep:
    """One build step, ready to run: its name, what it does, and its argv.

    This is :class:`~pengyos_media.steps.BuildStep` narrowed to what ``build``
    needs to execute it. The argv is the exact, shell-quotable invocation.
    """

    name: str
    description: str
    argv: list[str]

    @classmethod
    def from_step(cls, step: BuildStep) -> "RenderedStep":
        return cls(name=step.name, description=step.description, argv=list(step.argv))


@dataclass
class BuildOutcome:
    """The result of :func:`build`: the steps and (if committed) their results.

    ``dry_run`` is ``True`` when nothing was executed (``--dry-run``); then
    :attr:`results` is empty and every step is available in :attr:`steps`. When
    committed, :attr:`results` holds one :class:`RunResult` per step, in order.
    """

    kind: str
    steps: list[RenderedStep]
    results: list[RunResult] = field(default_factory=list)
    dry_run: bool = True

    @property
    def step_count(self) -> int:
        """The number of build steps."""
        return len(self.steps)

    def argv(self, index: int) -> list[str]:
        """The argv of step ``index`` (0-based)."""
        return list(self.steps[index].argv)


def build_plan_steps(plan: MediumPlan) -> list[RenderedStep]:
    """Render ``plan`` into the ordered, executable build steps.

    A pure function of the plan: the same plan always yields the same ordered
    argv. It is :func:`pengyos_media.steps.build_steps` narrowed to the fields
    :class:`BuildOutcome` needs.
    """
    return [RenderedStep.from_step(s) for s in build_steps(plan)]


def build(
    plan: MediumPlan,
    *,
    runner: Runner | None = None,
    dry_run: bool = True,
    cwd: str | None = None,
) -> BuildOutcome:
    """Drive ``plan`` through ``runner`` and return the :class:`BuildOutcome`.

    The plan is validated first (a malformed plan is a :class:`BuildError`,
    before any step is rendered or run). The ordered build steps are derived
    from the plan (a pure function of it). With ``dry_run=True`` (the default)
    the steps are rendered and *nothing* is executed — no runner call, no file
    written. With ``dry_run=False`` (``--commit``) every step is run through
    ``runner`` (defaulting to :func:`default_runner`); a step that exits
    non-zero raises :class:`BuildError` naming the step.

    ``runner`` is injectable: tests pass a :class:`~pengyos_media.runner.
    RecordingRunner` so nothing is ever executed.
    """
    errors = validate_plan(plan)
    if errors:
        raise BuildError(
            "cannot build an invalid plan: "
            + "; ".join(f"{e.code}: {e.message}" for e in errors)
        )

    steps = build_plan_steps(plan)
    outcome = BuildOutcome(kind=plan.kind.value, steps=steps, dry_run=dry_run)

    if dry_run:
        return outcome

    runner = runner if runner is not None else default_runner
    for step in steps:
        try:
            result = runner(step.argv, cwd=cwd)
        except FileNotFoundError as exc:
            tool = step.argv[0] if step.argv else "?"
            raise BuildError(
                f"build step '{step.name}' needs '{tool}', which is not on PATH "
                f"({getattr(exc, 'filename', tool)}); install its package",
                step=step.name,
            ) from exc
        outcome.results.append(result)
        if not result.ok:
            raise BuildError(
                f"build step '{step.name}' exited {result.returncode}"
                + (f": {result.stderr.strip()}" if result.stderr.strip() else ""),
                step=step.name,
            )
    return outcome


def render_build_plan_text(outcome: BuildOutcome) -> str:
    """A human-readable summary of a build outcome (name + argv per step)."""
    mode = "DRY-RUN (nothing executed)" if outcome.dry_run else "committed"
    lines = [f"build ({outcome.kind}) — {outcome.step_count} steps — {mode}"]
    for i, step in enumerate(outcome.steps, start=1):
        lines.append(f"  {i}. {step.name}: {step.description}")
        if step.argv:
            lines.append(f"      argv: {' '.join(_quote(a) for a in step.argv)}")
    if not outcome.dry_run and outcome.results:
        for step, result in zip(outcome.steps, outcome.results):
            status = "ok" if result.ok else f"FAILED (exit {result.returncode})"
            lines.append(f"  - {step.name}: {status}")
    return "\n".join(lines)


def _quote(arg: str) -> str:
    """Quota ``arg`` for display (single-quoted, embedded quotes escaped)."""
    if "'" not in arg:
        return f"'{arg}'"
    return f"'{arg.replace(chr(39), chr(39) + chr(92) + chr(39))}'"


def load_plan(path: str | Any) -> MediumPlan:
    """Load a :class:`MediumPlan` from a plan file (path or parsed JSON).

    Accepts a filesystem path (the ``--plan`` argument) *or* an already-parsed
    dict (tests can pass ``json.loads(...)`` directly). Raises
    :class:`BuildError` if the plan is missing/unreadable or not a plan object.
    """
    import json
    from pathlib import Path

    from pengyos_media.model import MediumPlan as _MediumPlan

    try:
        if isinstance(path, dict):
            data = path
        else:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read plan {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise BuildError(f"cannot read plan {path}: expected a JSON object")
    try:
        return _MediumPlan.from_dict(data)
    except (KeyError, ValueError) as exc:
        raise BuildError(f"cannot read plan {path}: not a medium plan ({exc})") from exc
