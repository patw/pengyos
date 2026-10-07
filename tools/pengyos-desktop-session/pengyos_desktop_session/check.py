"""The VM-testable session-fact check engine.

Given a COSMIC boot log, or
the captured stdout of an ssh probe from a running target, the check engine
verifies the subset of session facts QEMU+OVMF can actually observe:

======================  ===============================  ==================================
Fact                    How it is checked                Present pattern(s)
======================  ===============================  ==================================
compositor started      ``cosmic-comp`` in the log       ``cosmic-comp``
a wayland socket exists the wayland socket path present    the resolved wayland socket path
``cosmic-session`` up   ``cosmic-session`` in the log    ``cosmic-session``
pipewire running        pipewire / wireplumber           ``pipewire`` / ``wireplumber``
portals responding      xdg-desktop-portal / cosmic-portal ``xdg-desktop-portal`` / ``cosmic-portal``
======================  ===============================  ==================================

Each :class:`Fact` carries a human-readable name, its **present patterns** (at
least one of which must appear in the log for the fact to pass), and a short
note on how it is checked. The engine fails a fact when *none* of its present
patterns appear in the log — so a fact can never pass on a log that does not
actually show it (**no vacuous pass**).

Four classes of fact are **explicitly out of scope**:
GPU acceleration, audio devices, Bluetooth, and suspend/resume. They are
*real-hardware* facts that cannot be verified in QEMU+OVMF, so the engine never
tries — but it **reports them** (never silently omits them) as
"real-hardware, not verified here" (see :mod:`pengyos_desktop_session.oos`).

This module does no I/O and touches no network, display, or hardware: it only
takes a log string and returns findings. The ``check`` CLI reads the log (or
runs an ssh probe, item 5) and feeds the text here.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

from .model import CosmicSession
from .oos import OUT_OF_SCOPE, OUT_OF_SCOPE_LABEL, OutOfScopeFact, render_out_of_scope

__all__ = [
    "Fact",
    "FINDINGS_ORDER",
    "SESSION_FACTS",
    "CheckFinding",
    "CheckOutcome",
    "OUT_OF_SCOPE_LABEL",
    "OutOfScopeFact",
    "OUT_OF_SCOPE",
    "render_out_of_scope",
    "SSH_PROBE_ENABLED_ENV",
    "SSH_PROBE_ENABLED_VALUE",
    "SSH_PROBE_COMMAND",
    "check_boot_log",
    "check_ssh",
    "collect_ssh",
    "present_patterns",
    "render_findings",
    "ssh_probe_enabled",
]


@dataclass(frozen=True)
class Fact:
    """A single VM-testable session fact and how to verify it in a log.

    ``present`` is the list of patterns that must be searched for in the log;
    the fact passes when **at least one** of them is found. Keeping the
    patterns as plain strings (substring match) makes the committed boot-log
    fixtures readable and the intent obvious.
    """

    name: str
    present: tuple[str, ...]
    how: str

    def matches(self, log: str) -> bool:
        """True when at least one present pattern appears in ``log``."""
        return any(pattern in log for pattern in self.present)


# The VM-testable facts, in the canonical order they are reported. Each fact is
# derived from the emitted session model so the wayland-socket pattern tracks
# the operator-decided socket path (model.py / item 2).
SESSION_FACTS: tuple[Fact, ...] = (
    Fact(
        "cosmic-comp started",
        ("cosmic-comp",),
        "the cosmic-comp compositor appears in the boot log",
    ),
    Fact(
        "a wayland socket exists",
        (CosmicSession().wayland_socket(1000),),
        "the wayland socket path is present in the boot log",
    ),
    Fact(
        "cosmic-session / panel up",
        ("cosmic-session",),
        "the cosmic-session / cosmic-panel appears in the boot log",
    ),
    Fact(
        "pipewire / wireplumber running",
        ("pipewire", "wireplumber"),
        "pipewire and/or wireplumber appears in the boot log",
    ),
    Fact(
        "portals responding",
        ("xdg-desktop-portal", "cosmic-portal"),
        "an xdg-desktop-portal / cosmic-portal appears in the boot log",
    ),
)

# Canonical, stable ordering of the findings (== SESSION_FACTS order).
FINDINGS_ORDER: tuple[str, ...] = tuple(f.name for f in SESSION_FACTS)


def present_patterns(fact: Fact) -> tuple[str, ...]:
    """Return the present patterns that a fact is checked against."""
    return fact.present


@dataclass(frozen=True)
class CheckFinding:
    """The result of checking a single :class:`Fact` against a log."""

    name: str
    ok: bool
    patterns: tuple[str, ...]
    how: str


@dataclass(frozen=True)
class CheckOutcome:
    """The result of checking every :class:`Fact` against a log.

    ``ok`` is True only when *every* fact passes — a single missing present
    pattern fails the whole check (no vacuous pass).
    """

    ok: bool
    findings: tuple[CheckFinding, ...]
    source: str
    # Non-empty when the check was **skipped** (e.g. the ssh probe could not
    # run) — the reason is recorded honestly rather than reported as a bogus
    # PASS or a findings-based FAIL. Empty means the check ran.
    skip: str = ""
    # The explicitly-out-of-scope facts. These are
    # real-hardware facts the engine never verifies; they are reported (never
    # silently omitted) so a caller always sees what a passing check did *not*
    # verify. Defaults to :data:`OUT_OF_SCOPE`.
    out_of_scope: tuple[OutOfScopeFact, ...] = OUT_OF_SCOPE

    @property
    def passed(self) -> tuple[CheckFinding, ...]:
        """The findings that passed."""
        return tuple(f for f in self.findings if f.ok)

    @property
    def failed(self) -> tuple[CheckFinding, ...]:
        """The findings that failed (present pattern missing)."""
        return tuple(f for f in self.findings if not f.ok)


def check_boot_log(
    log_text: str,
    facts: "Sequence[Fact] | None" = None,
    source: str = "boot log",
    out_of_scope: "tuple[OutOfScopeFact, ...] | None" = None,
) -> CheckOutcome:
    """Check the VM-testable session facts against ``log_text``.

    Each fact passes when at least one of its present patterns appears in the
    log; the outcome ``ok`` is True only when every fact passes. A fact with no
    present pattern in the log fails — so an empty or wrong log cannot pass.

    The explicitly-out-of-scope facts (GPU/audio/Bluetooth/suspend) are carried on the outcome and reported by
    :func:`render_findings` as "real-hardware, not verified here"; they are
    never omitted.

    Parameters
    ----------
    log_text:
        The boot log (or ssh-probe stdout) to check.
    facts:
        The facts to check. Defaults to :data:`SESSION_FACTS`.
    source:
        A label for the log source, recorded on the outcome (``"boot log"`` or
        ``"ssh:HOST"``).
    out_of_scope:
        The out-of-scope facts to report. Defaults to :data:`OUT_OF_SCOPE`.
    """
    facts = tuple(facts) if facts is not None else SESSION_FACTS
    findings = tuple(
        CheckFinding(
            name=fact.name,
            ok=fact.matches(log_text),
            patterns=fact.present,
            how=fact.how,
        )
        for fact in facts
    )
    return CheckOutcome(
        ok=all(f.ok for f in findings),
        findings=findings,
        source=source,
        out_of_scope=tuple(out_of_scope) if out_of_scope is not None else OUT_OF_SCOPE,
    )


def _finding_line(finding: CheckFinding) -> str:
    """Render a single finding line for the human-readable report."""
    marker = "PASS" if finding.ok else "FAIL"
    joined = ", ".join(finding.patterns)
    return f"  [{marker}] {finding.name}  (patterns: {joined})"


def render_findings(outcome: CheckOutcome) -> str:
    """Render a human-readable, deterministic findings report.

    The facts are listed in canonical (:data:`FINDINGS_ORDER`) order; a fact is
    marked ``PASS`` / ``FAIL`` with the patterns it was checked against. The
    report ends with an overall verdict line.
    """
    lines = [f"session check over {outcome.source}:", ""]

    if outcome.skip:
        # The check could not run (e.g. the ssh probe was skipped). Report the
        # skip honestly — no findings to list, and never a bogus PASS/FAIL.
        lines.append(f"  [SKIP] {outcome.skip}")
        lines.append("")
        lines.append(f"RESULT: SKIP — {outcome.skip}")
    else:
        ordered = sorted(
            outcome.findings,
            key=lambda f: (FINDINGS_ORDER.index(f.name) if f.name in FINDINGS_ORDER else len(FINDINGS_ORDER)),
        )
        lines.extend(_finding_line(f) for f in ordered)
        lines.append("")
        if outcome.ok:
            lines.append("RESULT: PASS — all VM-testable session facts present")
        else:
            reasons = "; ".join(f.name for f in outcome.failed)
            lines.append(f"RESULT: FAIL — missing present pattern for: {reasons}")

    # The out-of-scope facts are reported on *every* verdict — PASS, FAIL, or
    # SKIP — so they are never silently omitted.
    lines.append("")
    lines.append(render_out_of_scope(outcome.out_of_scope))
    return "\n".join(lines)


def outcome_to_dict(outcome: CheckOutcome) -> "dict":
    """Serialize an :class:`CheckOutcome` to a JSON-safe dict (sorted keys).

    The ``out_of_scope`` facts are included (canonical order) so the JSON
    surface never drops them — the machine-readable report is as honest as the
    human-readable one.
    """
    return {
        "ok": outcome.ok,
        "source": outcome.source,
        "skip": outcome.skip,
        "out_of_scope": [
            {"category": f.category, "what": f.what, "why": f.why}
            for f in outcome.out_of_scope
        ],
        "findings": [
            {
                "name": f.name,
                "ok": f.ok,
                "patterns": list(f.patterns),
                "how": f.how,
            }
            for f in sorted(
                outcome.findings,
                key=lambda f: FINDINGS_ORDER.index(f.name) if f.name in FINDINGS_ORDER else len(FINDINGS_ORDER),
            )
        ],
    }


# --- ssh probe -----------------------------------

# The opt-in gate: the ssh probe only runs when this env var is exactly "1".
SSH_PROBE_ENABLED_ENV = "PENGYOS_IT"
SSH_PROBE_ENABLED_VALUE = "1"

# The command the ssh probe runs on the remote target. It gathers the same
# session state the check engine looks for in a boot log (systemd unit status,
# the compositor, pipewire/wireplumber, portals) and prints it, so the local
# engine can run its present/absent checks over the captured output. Keeping it
# as a module constant makes it easy to read and to assert against in tests.
SSH_PROBE_COMMAND = (
    'echo "== systemd =="; '
    'systemctl list-units --type=service --no-pager --all 2>/dev/null | '
    "grep -Ei 'cosmic-comp|cosmic-session|pipewire|wireplumber|xdg-desktop-portal|cosmic-portal' || true; "
    'echo "== processes =="; '
    "ps -eo comm 2>/dev/null | grep -Ei 'cosmic-comp|cosmic-session|pipewire|wireplumber|xdg-desktop-portal' || true; "
    'echo "== wayland =="; '
    "ls -1 /run/user/$(id -u)/wayland-* 2>/dev/null || true"
)


def ssh_probe_enabled() -> bool:
    """True when the ssh probe is opt-in enabled (``PENGYOS_IT=1``)."""
    return os.environ.get(SSH_PROBE_ENABLED_ENV) == SSH_PROBE_ENABLED_VALUE


def collect_ssh(
    host: str,
    *,
    ssh_runner: "Callable[[list[str]], subprocess.CompletedProcess] | None" = None,
) -> "tuple[str | None, str | None]":
    """Collect the target's session state over ssh.

    Returns a ``(probe_output, skip_reason)`` tuple:

    * On success: ``(output, None)`` — ``output`` is the captured ssh stdout
      (possibly empty, but the probe *ran*).
    * On skip: ``(None, reason)`` — one of:

      - the ssh probe is not enabled (``PENGYOS_IT != "1"``),
      - the host is blank/whitespace,
      - the remote command produced no output (nothing to verify),
      - ssh itself failed (missing binary, unreachable host, auth failure, …).

    The probe is honest about each of these: a skip is reported, never a bogus
    PASS or a findings-based FAIL.

    ``ssh_runner`` is injectable (defaults to :func:`subprocess.run`) so tests
    can supply a fake without touching the network, ssh, or a real target.
    """
    ssh_runner = ssh_runner or (lambda args: subprocess.run(args, capture_output=True, text=True))

    if not ssh_probe_enabled():
        return None, f"{SSH_PROBE_ENABLED_ENV} != {SSH_PROBE_ENABLED_VALUE!r} (opt-in probe disabled)"

    if not (host and host.strip()):
        return None, "no ssh host given"

    # ``user@host`` — ssh resolves the user from the local account by default.
    # BatchMode=yes never prompts (no TTY headless); a short connect timeout
    # bounds an unreachable host. ssh is only ever invoked when the opt-in gate
    # is open, so the unit suite never calls the real binary.
    result = ssh_runner(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{host}", SSH_PROBE_COMMAND]
    )

    if result.returncode != 0:
        return None, "ssh probe failed (unreachable host, auth failure, or no ssh)"

    output = (result.stdout or "").strip()
    if not output:
        return None, "ssh probe returned no output (nothing to verify)"

    return output, None


def check_ssh(
    host: str,
    *,
    ssh_runner: "Callable[[list[str]], subprocess.CompletedProcess] | None" = None,
) -> "CheckOutcome":
    """Check the VM-testable session facts over ssh from a running target.

    Collects the target's session state with :func:`collect_ssh` and runs the
    present/absent check engine over the captured output (so the same facts and
    the same no-vacuous-pass semantics apply). On a skip, returns a
    :class:`CheckOutcome` with ``ok=False`` and a populated ``skip`` reason —
    recorded honestly, never a bogus PASS/FAIL.
    """
    output, skip_reason = collect_ssh(host, ssh_runner=ssh_runner)
    if skip_reason is not None:
        return CheckOutcome(ok=False, findings=(), source=f"ssh:{host}", skip=skip_reason)

    return check_boot_log(output, source=f"ssh:{host}")
