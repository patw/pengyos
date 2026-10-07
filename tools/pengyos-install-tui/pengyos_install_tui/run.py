"""Render the wizard on stdin/stdout — the interactive ``run`` front-end.

This is the guided Core/Server installer wizard over console, serial, or SSH
(spec.md: ``run``). It drives the pure :class:`~pengyos_install_tui.model.Wizard`
state machine by *rendering* each step and *applying* the answer read from
stdin:

* the current step is rendered with a progress header, a human title, the valid
  values (where the step has a closed set), and a prompt;
* an answer read from stdin is validated and applied through the wizard, which
  advances the cursor (an invalid answer is re-prompted, never a crash);
* the ``confirm`` step renders the derived plan and asks for a final ``yes``;
* a ``back`` command returns the wizard to the prior step (review / re-edit).

The whole thing is **scriptable**: :func:`run` accepts a ``--answers`` JSON answer
set (the same shape the interactive wizard stores — see the ``run`` schema in
spec.md) which is replayed through the wizard instead of prompting, so the suite
can drive the interactive path deterministically and offline.

The module is deliberately thin on the concerns it does not own: reading/parsing
the answers file and error reporting live in :mod:`pengyos_install_tui.cli`
(through :class:`ConfigError` / :class:`ValidationError`), exactly as ``auto``
does. :func:`run` only *applies* answers and *renders* lines.

Public surface
--------------
* :data:`REPLAY_ORDER` — the five answerable steps ``run`` applies.
* :data:`BACK_COMMAND` — the stdin token that triggers back-navigation.
* :data:`STEP_TITLES` / :data:`STEP_HINTS` — the per-step rendering data.
* :func:`render_step` — the human text for one step (for rendering/tests).
* :func:`render_plan` — the human text for the final plan review.
* :func:`run_replay` — apply an answer set through the wizard (no I/O).
* :class:`InteractiveRunner` — the stdin/stdout prompt loop (injectable streams).
* :func:`run` — the top-level orchestration (prompt or replay) returning a plan.
"""

from __future__ import annotations

import json
import sys
from typing import Callable, Optional, TextIO, Tuple

from pengyos_install_tui.auto import ConfigError
from pengyos_install_tui.model import (
    Edition,
    Layout,
    NetworkMode,
    Plan,
    StepState,
    ValidationError,
    Wizard,
)

__all__ = [
    "REPLAY_ORDER",
    "BACK_COMMAND",
    "STEP_TITLES",
    "STEP_HINTS",
    "render_step",
    "render_plan",
    "preview_plan",
    "run_replay",
    "load_answers",
    "run",
    "InteractiveRunner",
]

#: The five answerable steps ``run`` applies, in wizard order (excludes
#: ``confirm`` — there is nothing to prompt for the review step).
REPLAY_ORDER: Tuple[str, ...] = ("target", "edition", "layout", "user", "network")

#: The stdin token that triggers back-navigation at any data-step prompt. Kept
#: as a named constant so the interactive path and tests agree on it.
BACK_COMMAND: str = "back"

#: Human titles for each step (the ``confirm`` step reviews the plan).
STEP_TITLES: dict[str, str] = {
    "target": "Target disk",
    "edition": "Edition",
    "layout": "Disk layout",
    "user": "Initial admin user",
    "network": "Network",
    "confirm": "Confirm",
}

#: Prompt hints for each data step. The closed-set steps name their valid
#: values; the open steps (target, user) point at the accepted shape.
STEP_HINTS: dict[str, str] = {
    "target": "Enter the target block device (e.g. /dev/sdb)",
    "edition": "Choose an edition (core, server)",
    "layout": "Choose a layout (single-root, ab-split)",
    "user": "Enter the initial admin username",
    "network": "Choose a network mode (dhcp, static) or a mapping",
    "confirm": "Type 'yes' to install with the choices above",
}


def _closed_set(step: str) -> Optional[Tuple[str, ...]]:
    """The closed value set for a step, or ``None`` for an open-ended step."""
    return {
        "edition": tuple(Edition.ALL),
        "layout": tuple(Layout.ALL),
        "network": tuple(NetworkMode.ALL),
    }.get(step)


def render_step(state: StepState) -> str:
    """Render the human-readable prompt for one step.

    The header shows the progress (``Step i/total``) and the step title; the
    body shows the prompt hint and, for a closed-set step, the valid values.
    """
    done = state.index + 1
    lines = [
        f"Step {done}/{state.total}: {STEP_TITLES.get(state.key, state.key)}",
    ]
    hint = STEP_HINTS.get(state.key, "")
    if hint:
        lines.append(f"  {hint}:")
    closed = _closed_set(state.key)
    if closed:
        lines.append(f"  valid values: {', '.join(closed)}")
    # If the step already has an answer (e.g. after back-navigation), echo it so
    # the operator sees the current value before re-editing.
    if state.answer is not None:
        lines.append(f"  current: {state.answer}")
    return "\n".join(lines)


def render_plan(plan: Plan) -> str:
    """Render the final review of the confirmed plan (the confirm step)."""
    body = "\n".join(f"  {line}" for line in plan.as_lines())
    return "\n".join(
        [
            "Step 6/6: Confirm",
            body,
            "  Type 'yes' to install with the choices above",
        ]
    )


def preview_plan(wizard: Wizard) -> Plan:
    """Build the plan from the wizard's current answers (for the review).

    The confirm step reviews what has been decided *before* the terminal
    :meth:`Wizard.confirm` — this constructs the same :class:`Plan` the
    interactive path produces without marking the wizard ``done`` (so a
    ``back`` from the review still re-edits cleanly).
    """
    return Plan(
        target=str(wizard.get_answer("target")),
        edition=str(wizard.get_answer("edition")),
        layout=str(wizard.get_answer("layout")),
        user=wizard.get_answer("user"),
        network=wizard.get_answer("network"),
    )


def run_replay(
    answers: dict,
    probe: Optional[Callable[[str], Optional[str]]] = None,
) -> Plan:
    """Apply an answer set through the wizard and return the confirmed plan.

    This is the non-interactive path behind ``--answers``: it applies each field
    through :meth:`Wizard.answer` (validating as it goes) and then confirms. The
    cursor position is irrelevant — :meth:`Wizard.confirm` only requires every
    step answered — so answers may be supplied in any order (though the suite
    supplies them in wizard order). A missing/invalid field raises
    :class:`ConfigError` / :class:`ValidationError` (a reported error, never a
    crash).
    """
    wizard = Wizard(probe=probe)  # type: ignore[arg-type]
    for step in REPLAY_ORDER:
        if step not in answers:
            raise ConfigError(f"missing required field: '{step}'")
        wizard.answer(step, answers[step])
    return wizard.confirm()


def load_answers(path: str) -> dict:
    """Read a ``--answers`` JSON answer set into a plain dict.

    The ``--answers`` file is the JSON twin of ``install.toml`` (spec.md: the
    ``run`` schema) — a table of the five answerable fields, each accepting the
    same bare-scalar-or-mapping values the interactive wizard stores. Raises
    :class:`ConfigError` (a reported error, never a crash) when the file is
    missing/unreadable, is not valid JSON, or is not a table of fields.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"answers file not found: {path}", path=path) from exc
    except OSError as exc:
        raise ConfigError(
            f"cannot read answers file '{path}': {exc}", path=path
        ) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in '{path}': {exc}", path=path) from exc

    if not isinstance(data, dict):
        raise ConfigError(
            f"invalid answers '{path}': top level must be a table of fields",
            path=path,
        )
    return data


class InteractiveRunner:
    """The interactive prompt loop (see :func:`run`).

    Owns the stdin/stdout rendering and the ``back`` navigation. Constructed
    with injectable streams so it is fully offline-testable; the CLI builds one
    with the real streams.
    """

    def __init__(
        self,
        in_stream: TextIO,
        out_stream: TextIO,
        err_stream: TextIO,
        probe: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        self._in = in_stream
        self._out = out_stream
        self._err = err_stream
        self._wizard = Wizard(probe=probe)  # type: ignore[arg-type]

    def _write(self, text: str) -> None:
        self._out.write(text)
        self._out.write("\n")
        self._out.flush()

    def _prompt(self, message: str) -> Optional[str]:
        """Write ``message`` and read one line; return ``None`` on EOF."""
        self._write(message)
        line = self._in.readline()
        if line == "":  # EOF
            return None
        return line.rstrip("\n")

    def run(self) -> Optional[Plan]:
        """Run the interactive loop; return the confirmed plan, or ``None``.

        A blank line at a data step is ignored (re-prompt); an EOF at any prompt
        ends the loop without a plan (returns ``None``). An invalid answer is
        reported to stderr and re-prompted; ``back`` returns to the prior step.
        """
        while True:
            state = self._wizard.current
            key = state.key

            if key == "confirm":
                # The confirm step is a review, not a prompt: render the plan
                # (which carries its own header) and ask for the final yes.
                self._write(render_plan(preview_plan(self._wizard)))
                answer = self._prompt(f"[{BACK_COMMAND}/yes]> ")
                if answer is None:  # EOF before a yes -> no plan
                    return None
                if answer.strip().lower() == BACK_COMMAND:
                    self._wizard.back()
                    continue
                if answer.strip().lower() != "yes":
                    continue  # re-prompt until a clear yes
                return self._wizard.confirm()

            self._write(render_step(state))
            answer = self._prompt("  > ")
            if answer is None:  # EOF at a data prompt -> no plan
                return None
            if answer.strip().lower() == BACK_COMMAND:
                self._wizard.back()
                continue
            if answer.strip() == "":
                continue  # ignore blank input, re-prompt
            try:
                self._wizard.answer(key, answer.strip())
            except ValidationError as exc:
                self._err.write(f"pengyos-install-tui: {exc.message}\n")
                self._err.flush()
                continue  # re-prompt the same step


def run(
    answers: Optional[dict] = None,
    *,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    probe: Optional[Callable[[str], Optional[str]]] = None,
) -> Optional[Plan]:
    """Drive the wizard, returning the confirmed :class:`Plan` (or ``None``).

    * If ``answers`` is given, :func:`run_replay` applies them (no I/O); this is
      the scriptable path behind ``--answers``.
    * Otherwise the interactive loop renders each step, reads an answer from
      ``stdin``, applies it, and supports ``back`` navigation, until the plan is
      confirmed. An EOF at a prompt ends the loop without a plan (returns
      ``None``); an invalid answer is re-prompted.

    ``stdin``/``stdout``/``stderr`` default to the real streams so the CLI can
    drive it; tests pass in-memory :class:`io.StringIO` streams.
    """
    if answers is not None:
        return run_replay(answers, probe=probe)

    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout
    err_stream = stderr or sys.stderr
    return InteractiveRunner(in_stream, out_stream, err_stream, probe=probe).run()
