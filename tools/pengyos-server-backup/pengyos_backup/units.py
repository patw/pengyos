"""Render the systemd timer + service the backup planner ships (``plan``).

Given a validated :class:`~pengyos_backup.config.BackupPlan` this module renders
the systemd units that ``pengyos-backup plan`` emits into its output directory:

``pengyos-backup.timer``
    An on-calendar timer. ``OnCalendar`` comes straight from the plan's
    schedule; ``RandomizedDelaySec`` (if set) is added so a burst of
    hosts waking at the same tick does not thrash the backup back-ends.
    ``Persistent=yes`` catches up a missed run after downtime.

``pengyos-backup.service``
    A ``Type=oneshot`` service that runs the **golden restic/rustic argv**:
    one ``backup`` per target, then a ``forget`` + ``prune`` per target
    (the retention policy from the plan). ``OnFailure`` points at the alert
    hook so a failed backup wakes a recovery path instead of failing silently.
    The service is ``WantedBy=multi-user.target`` — it is a user-triggered
    on-demand job, not a boot-time service.

``pengyos-backup-alert.service``
    The alert hook. A ``Type=oneshot`` service whose ``ExecStart`` runs the
    emitted alert script (``pengyos-backup-alert.sh`` — see below) when the
    backup service fails. See the module docstring of :data:`ALERT_SCRIPT_NAME`
    for the documented contract.

``pengyos-backup-alert.sh``
    The alert hook script itself: an executable, no-op-by-default ``/bin/sh``
    script that logs the failure and exits 0 so the ``OnFailure`` chain
    completes. An operator replaces this body (or points ``ExecStart`` elsewhere)
    to alert via mail / webhook / pager. It is emitted *as a file* (not inline
    in the ``.service``) because systemd rejects non-comment, non-``Key=Value``
    lines in a unit, so the shell body cannot live inside the ``.service``.

Naming
------
The emitted files are named ``pengyos-backup.timer`` / ``pengyos-backup.service``
/ ``pengyos-backup-alert.service``. The timer's ``Unit=`` and the service's
``OnFailure=`` reference these by name, so the units are self-linking: install
all three and systemd wires the failure path automatically.

Golden argv
-----------
The restic/rustic argv is built by :func:`_backup_argv` and :func:`_forget_argv`
and assembled in :func:`service_exec_starts`. It is the single source of truth
for the flags the emitted service runs, so the service and its tests can never
diverge. See ``spec.md`` / ``example.backup.yaml`` for the flag mapping.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from .config import BackupPlan, Retention, Target

# --- emitted unit names ------------------------------------------------------

TIMER_NAME = "pengyos-backup.timer"
SERVICE_NAME = "pengyos-backup.service"
ALERT_SERVICE_NAME = "pengyos-backup-alert.service"

# The alert hook the backup service's OnFailure= pulls in (by unit name, see
# render_service). This is the documented fail->alert boundary: the planner
# emits the *wiring* (OnFailure=pengyos-backup-alert.service) and the alert
# *unit* + *script*; the script is the operator's to wire to mail/webhook/
# journal. It ships with a no-op body so the failure path is real, not a
# dangling reference.
ALERT_SCRIPT_NAME = "pengyos-backup-alert"

# The ExecStart path the alert service runs. Mirrors the PengyOS Server backup
# convention (sibling pengyos-server-defconfig): the script is installed to
# /usr/local/bin/ and the units to /etc/systemd/system/. The emitted script's
# basename matches this path, so the operator just copies it into place.
DEFAULT_ALERT_PATH = f"/usr/local/bin/{ALERT_SCRIPT_NAME}"

# Hardening that applies to the backup service (mirrors the PengyOS service
# convention in the sibling systemd-units tool: unprivileged, no new
# privileges, protected filesystems).
_SERVICE_HARDENING = (
    "NoNewPrivileges=yes",
    "ProtectSystem=strict",
    "ProtectHome=yes",
    "PrivateTmp=yes",
)


# --- argv rendering ----------------------------------------------------------


def _quote(arg: str) -> str:
    """Quote *arg* for a systemd ``ExecStart`` line if it needs it.

    systemd does not do shell word-splitting: a token containing whitespace
    must be wrapped in double quotes (systemd strips one layer). Paths with
    spaces are the common case; bare tokens are left alone for readability.
    """

    if any(ch in arg for ch in " \t"):
        return f'"{arg}"'
    return arg


def _backup_argv(binary: str, sources: tuple[str, ...], target: Target) -> tuple[str, ...]:
    """The golden ``<binary> backup`` argv for a single *target*.

    ``restic backup /etc /home --repo /var/backups/pengyos --host backup-host``.
    ``--host`` is emitted only when the target carries one (it disambiguates
    snapshots taken from different machines in a shared repo).
    """

    argv: list[str] = [binary, "backup", *sources, "--repo", target.repo]
    if target.host:
        argv += ["--host", target.host]
    return tuple(argv)


def _forget_argv(
    binary: str, target: Target, retention: Retention
) -> tuple[str, ...]:
    """The golden ``<binary> forget --prune`` argv for a single *target*.

    The keep flags come from :meth:`pengyos_backup.config.Retention.prune_flags`
    — the single source of truth for the retention->forget-flag mapping — so the
    emitted service and the retention validator can never disagree. An empty
    retention renders as ``forget --repo <repo> --prune`` (keep-all), which is a
    valid, documented no-op prune.
    """

    argv: list[str] = [binary, "forget", "--repo", target.repo]
    argv += retention.prune_flags()
    argv.append("--prune")
    return tuple(argv)


@dataclass(frozen=True)
class ExecStart:
    """A single ``ExecStart=`` line: the golden argv tokens plus a comment.

    The comment explains what the line does (backup vs. forget); it is emitted
    inline after the ``ExecStart=`` value so the emitted unit is self-documenting
    (systemd treats everything after ``#`` on an option line as a comment).
    """

    argv: tuple[str, ...]
    note: str

    def render(self) -> str:
        return f"ExecStart={(' '.join(_quote(a) for a in self.argv))} # {self.note}"


def service_exec_starts(plan: BackupPlan, binary: str) -> tuple[ExecStart, ...]:
    """The ordered ``ExecStart`` list for the backup service.

    All target backups run first (one per target), then all forget+prune runs
    (one per target). This is the golden argv: back everything up, then enforce
    retention against each repo.
    """

    starts: list[ExecStart] = []
    for target in plan.targets:
        starts.append(
            ExecStart(
                _backup_argv(binary, plan.sources, target),
                f"backup {target.kind} target {target.repo}",
            )
        )
    for target in plan.targets:
        starts.append(
            ExecStart(
                _forget_argv(binary, target, plan.retention),
                f"forget+prune {target.kind} target {target.repo}",
            )
        )
    return tuple(starts)


# --- unit rendering ----------------------------------------------------------


def _header(title: str) -> str:
    return (
        "# ============================================================================\n"
        f"# PengyOS backup — {title}\n"
        "# ----------------------------------------------------------------------------\n"
        "# Emitted by `pengyos-backup plan`. Do not edit by hand — regenerate with\n"
        "# `pengyos-backup plan --config backup.yaml --out <dir>`.\n"
        "# ============================================================================\n"
    )


def render_timer(plan: BackupPlan, binary: str) -> str:
    """Render the ``pengyos-backup.timer`` unit for *plan* / *binary*.

    ``OnCalendar`` is the plan's schedule; ``RandomizedDelaySec`` is added only
    when set. The timer triggers ``pengyos-backup.service`` and is
    enabled into ``timers.target`` (systemd's own convention for on-calendar
    timers).
    """

    lines: list[str] = [_header(TIMER_NAME)]
    lines.append("[Unit]")
    lines.append(
        f"Description=PengyOS backup ({binary}) — {plan.schedule.on_calendar}"
    )
    lines.append("Documentation=file:/usr/local/bin/pengyos-backup-alert")
    lines.append("")
    lines.append("[Timer]")
    lines.append(f"OnCalendar={plan.schedule.on_calendar}")
    if plan.schedule.randomized_delay:
        lines.append(
            f"RandomizedDelaySec={plan.schedule.randomized_delay}"
        )
    lines.append("Persistent=yes")
    lines.append(f"Unit={SERVICE_NAME}")
    lines.append("")
    lines.append("[Install]")
    lines.append("WantedBy=timers.target")
    lines.append("")
    return "\n".join(lines)


def render_service(plan: BackupPlan, binary: str) -> str:
    """Render the ``pengyos-backup.service`` unit for *plan* / *binary*.

    ``Type=oneshot`` so systemd treats each backup as a finite, completed job.
    Deliberately no ``RemainAfterExit=yes``: it would leave the unit active
    after the first run, and the timer could not start it again that boot. The golden argv (see :func:`service_exec_starts`)
    runs one backup + one forget+prune per target. ``OnFailure`` points at the
    alert hook.
    """

    starts = service_exec_starts(plan, binary)
    lines: list[str] = [_header(SERVICE_NAME)]
    lines.append("[Unit]")
    lines.append(f"Description=PengyOS backup ({binary})")
    lines.append("Documentation=file:/usr/local/bin/pengyos-backup-alert")
    lines.append("After=network.target")
    lines.append("Wants=network.target")
    lines.append("")
    lines.append("[Service]")
    lines.append("Type=oneshot")
    for start in starts:
        lines.append(start.render())
    lines.append(f"OnFailure={ALERT_SERVICE_NAME}")
    lines.extend(_SERVICE_HARDENING)
    lines.append("")
    lines.append("[Install]")
    lines.append("WantedBy=multi-user.target")
    lines.append("")
    return "\n".join(lines)


def alert_script_text() -> str:
    """The body of the alert hook script (an executable, no-op by default).

    This is the documented fail->alert boundary. It logs to the journal and
    exits 0 so the ``OnFailure`` chain completes. An operator replaces the body
    (or points the alert service's ``ExecStart`` elsewhere) to send mail /
    webhook / pager. Emitted as a *file* (``ALERT_SCRIPT_NAME``), never inline
    in a ``.service`` (systemd rejects non-comment lines in a unit).
    """

    return (
        "#!/bin/sh\n"
        "# PengyOS backup alert hook — emitted by `pengyos-backup plan`.\n"
        "#\n"
        "# Invoked by pengyos-backup-alert.service when pengyos-backup.service\n"
        "# fails (OnFailure=). Replace this body to alert via mail / webhook /\n"
        "# pager; as shipped it logs to the journal and exits 0 so the failure\n"
        "# chain completes. The failed unit name is passed in $SYSTEMD_UNIT.\n"
        "set -eu\n"
        'echo "pengyos-backup: backup failed ($SYSTEMD_UNIT) — see journal" >&2\n'
        'logger -t pengyos-backup-alert "backup failed; alert hook fired"\n'
    )


def render_alert_service(plan: BackupPlan, binary: str) -> str:
    """Render the ``pengyos-backup-alert.service`` unit (the alert hook).

    A ``Type=oneshot`` service running :data:`DEFAULT_ALERT_PATH`. This is a
    real, runnable unit (not a dangling ``OnFailure`` reference): install all
    emitted units and systemd wires the failure path automatically. It has no
    ``[Install]`` section — OnFailure units are pulled in on demand, not
    enabled at boot.
    """

    lines: list[str] = [_header(ALERT_SERVICE_NAME)]
    lines.append("[Unit]")
    lines.append("Description=PengyOS backup failure alert")
    lines.append("Documentation=file:/usr/local/bin/pengyos-backup-alert")
    lines.append("")
    lines.append("[Service]")
    lines.append("Type=oneshot")
    lines.append(f"ExecStart={DEFAULT_ALERT_PATH}")
    lines.append("")
    return "\n".join(lines) + "\n"


# --- emission ----------------------------------------------------------------


@dataclass(frozen=True)
class EmittedUnits:
    """The artifacts emitted by :func:`plan`, keyed by filename.

    ``files`` maps each emitted filename to its rendered text (the three units
    plus the alert script). ``service_name`` / ``alert_service_name`` /
    ``timer_name`` are the canonical unit names for callers that need to
    reference them by name (e.g. to assert the ``OnFailure=`` / ``Unit=`` links).
    """

    files: dict[str, str]
    service_name: str = SERVICE_NAME
    alert_service_name: str = ALERT_SERVICE_NAME
    timer_name: str = TIMER_NAME

    def filenames(self) -> list[str]:
        return sorted(self.files)


def render_all(plan: BackupPlan, binary: str) -> EmittedUnits:
    """Render every artifact for *plan* / *binary* as ``{filename: text}``.

    The order is deterministic (timer, service, alert, script) so emitted
    output — and the tests that pin it — are stable.
    """

    return EmittedUnits(
        files={
            TIMER_NAME: render_timer(plan, binary),
            SERVICE_NAME: render_service(plan, binary),
            ALERT_SERVICE_NAME: render_alert_service(plan, binary),
            ALERT_SCRIPT_NAME: alert_script_text(),
        }
    )


def emit_units(plan: BackupPlan, binary: str, out_dir: Path) -> EmittedUnits:
    """Render *plan* and write the artifacts into *out_dir*.

    Returns the :class:`EmittedUnits` describing what was written. The output
    directory is created if it does not exist. Existing files are overwritten
    (``plan`` is idempotent: re-running it yields the same bytes). The alert
    script is written with mode 0755 so it is executable in place.
    """

    units = render_all(plan, binary)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in units.files.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        if name == ALERT_SCRIPT_NAME:
            # Make the alert hook executable in place (0755).
            path.chmod(0o755)
    return units
