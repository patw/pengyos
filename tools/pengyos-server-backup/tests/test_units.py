"""Tests for pengyos_backup.units — the systemd timer/service renderer.

These exercise the *plan* output directly (render_timer / render_service /
render_alert_service / service_exec_starts / render_all) and the argv builders,
plus the emitted alert script. They need no network, root, or restic: they
assert on the rendered text.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from pengyos_backup.config import Retention, Schedule, Target, validate
from pengyos_backup.units import (
    ALERT_SCRIPT_NAME,
    ALERT_SERVICE_NAME,
    SERVICE_NAME,
    TIMER_NAME,
    alert_script_text,
    render_all,
    render_alert_service,
    render_service,
    render_timer,
    service_exec_starts,
)

# A representative, valid config reused across the tests.
VALID_YAML = textwrap.dedent(
    """
    version: 1

    sources:
      - /etc
      - /home
      - /var/lib/pengyos

    targets:
      local:
        path: /var/backups/pengyos
      remote:
        url: s3:example.com/pengyos
        host: backup-host

    schedule:
      on_calendar: "Mon..Fri *-*-* 02:30:00"
      randomized_delay: "10min"

    retention:
      keep_last: 7
      keep_daily: 7
      keep_weekly: 4
      keep_monthly: 6
      keep_yearly: 0
      keep_within: "720h"
    """
)


def _valid_plan() -> object:
    import yaml

    return validate(yaml.safe_load(VALID_YAML))


def _parse_unit(text: str) -> dict[str, list[tuple[str, str]]]:
    """Parse a systemd unit into ``{section: [(key, value), ...]}``.

    Handles ``[Section]`` headers, full-line ``#``/``;`` comments, and inline
    ``#`` comments (systemd strips everything after ``#`` on an option line).
    """

    sections: dict[str, list[tuple[str, str]]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if "#" in line:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        if "=" in line and current is not None:
            key, _, value = line.partition("=")
            sections[current].append((key.strip(), value.strip()))
    return sections


def _option(sections: dict, section: str, key: str) -> str | None:
    for found_key, found_value in sections.get(section, []):
        if found_key == key:
            return found_value
    return None


def _all_values(sections: dict, section: str, key: str) -> list[str]:
    return [v for k, v in sections.get(section, []) if k == key]


# --- timer -------------------------------------------------------------------


def test_timer_has_on_calendar():
    plan = _valid_plan()
    text = render_timer(plan, "restic")
    assert "OnCalendar=Mon..Fri *-*-* 02:30:00" in text


def test_timer_wants_timers_target():
    # systemd's own convention for on-calendar timers.
    sections = _parse_unit(render_timer(_valid_plan(), "restic"))
    assert _option(sections, "Install", "WantedBy") == "timers.target"


def test_timer_is_persistent():
    sections = _parse_unit(render_timer(_valid_plan(), "restic"))
    assert _option(sections, "Timer", "Persistent") == "yes"


def test_timer_points_at_service():
    sections = _parse_unit(render_timer(_valid_plan(), "restic"))
    assert _option(sections, "Timer", "Unit") == SERVICE_NAME


def test_timer_randomized_delay_when_set():
    sections = _parse_unit(render_timer(_valid_plan(), "restic"))
    assert _option(sections, "Timer", "RandomizedDelaySec") == "10min"


def test_timer_omits_randomized_delay_when_unset():
    plan = validate(
        {
            "version": 1,
            "sources": ["/etc"],
            "targets": {"local": {"path": "/var/backups"}},
            "schedule": {"on_calendar": "daily"},
        }
    )
    text = render_timer(plan, "restic")
    assert "RandomizedDelaySec" not in text


def test_timer_deterministic():
    plan = _valid_plan()
    assert render_timer(plan, "restic") == render_timer(plan, "restic")


# --- service -----------------------------------------------------------------


def test_service_is_oneshot():
    sections = _parse_unit(render_service(_valid_plan(), "restic"))
    assert _option(sections, "Service", "Type") == "oneshot"
    assert _option(sections, "Service", "RemainAfterExit") == "yes"


def test_service_onfailure_points_at_alert():
    sections = _parse_unit(render_service(_valid_plan(), "restic"))
    assert _option(sections, "Service", "OnFailure") == ALERT_SERVICE_NAME


def test_service_wants_multi_user_target():
    # The backup service is a user-triggered on-demand job, not a boot service.
    sections = _parse_unit(render_service(_valid_plan(), "restic"))
    assert _option(sections, "Install", "WantedBy") == "multi-user.target"


def test_service_has_hardening():
    sections = _parse_unit(render_service(_valid_plan(), "restic"))
    assert _option(sections, "Service", "NoNewPrivileges") == "yes"
    assert _option(sections, "Service", "ProtectSystem") == "strict"
    assert _option(sections, "Service", "ProtectHome") == "yes"
    assert _option(sections, "Service", "PrivateTmp") == "yes"


def test_service_has_no_inline_shell():
    # The alert script is emitted as a separate file, never inline in the
    # .service (systemd rejects non-comment lines in a unit).
    text = render_service(_valid_plan(), "restic")
    assert "#!/bin/sh" not in text


def test_alert_service_has_no_install_section():
    # OnFailure units are pulled in on demand, not enabled at boot.
    sections = _parse_unit(render_alert_service(_valid_plan(), "restic"))
    assert "Install" not in sections


def test_alert_service_execstart_runs_script():
    sections = _parse_unit(render_alert_service(_valid_plan(), "restic"))
    assert (
        _option(sections, "Service", "ExecStart")
        == "/usr/local/bin/pengyos-backup-alert"
    )


def test_alert_script_is_executable_shell():
    body = alert_script_text()
    assert body.startswith("#!/bin/sh\n")
    assert body.rstrip().endswith(
        'logger -t pengyos-backup-alert "backup failed; alert hook fired"'
    )


# --- golden argv -------------------------------------------------------------


def test_exec_starts_backups_then_forgets():
    plan = _valid_plan()
    starts = service_exec_starts(plan, "restic")
    notes = [s.note for s in starts]
    # All backups precede all forgets.
    backup_idx = [i for i, n in enumerate(notes) if n.startswith("backup")]
    forget_idx = [i for i, n in enumerate(notes) if n.startswith("forget")]
    assert max(backup_idx) < min(forget_idx)
    # One backup + one forget per target (2 targets).
    assert len(backup_idx) == 2
    assert len(forget_idx) == 2


def test_backup_argv_includes_sources_repo_and_host():
    plan = _valid_plan()
    starts = service_exec_starts(plan, "restic")
    backup_notes = [s for s in starts if s.note.startswith("backup")]
    local = [s for s in backup_notes if "local target" in s.note][0]
    assert local.argv == (
        "restic",
        "backup",
        "/etc",
        "/home",
        "/var/lib/pengyos",
        "--repo",
        "/var/backups/pengyos",
    )
    remote = [s for s in backup_notes if "remote target" in s.note][0]
    assert remote.argv == (
        "restic",
        "backup",
        "/etc",
        "/home",
        "/var/lib/pengyos",
        "--repo",
        "s3:example.com/pengyos",
        "--host",
        "backup-host",
    )


def test_backup_argv_omits_host_when_unset():
    plan = validate(
        {
            "version": 1,
            "sources": ["/etc"],
            "targets": {"local": {"path": "/var/backups"}},
            "schedule": {"on_calendar": "daily"},
        }
    )
    starts = service_exec_starts(plan, "restic")
    (backup,) = [s for s in starts if s.note.startswith("backup")]
    assert backup.argv == (
        "restic",
        "backup",
        "/etc",
        "--repo",
        "/var/backups",
    )


def test_forget_argv_maps_retention_flags():
    plan = _valid_plan()
    starts = service_exec_starts(plan, "restic")
    forget = [s for s in starts if s.note.startswith("forget")][0]
    assert "--keep-last 7" in " ".join(forget.argv)
    assert "--keep-daily 7" in " ".join(forget.argv)
    assert "--keep-weekly 4" in " ".join(forget.argv)
    assert "--keep-monthly 6" in " ".join(forget.argv)
    assert "--keep-yearly 0" in " ".join(forget.argv)
    assert "--keep-within 720h" in " ".join(forget.argv)
    assert forget.argv[-1] == "--prune"


def test_forget_argv_empty_retention_is_prune_only():
    plan = validate(
        {
            "version": 1,
            "sources": ["/etc"],
            "targets": {"local": {"path": "/var/backups"}},
            "schedule": {"on_calendar": "daily"},
        }
    )
    starts = service_exec_starts(plan, "restic")
    (forget,) = [s for s in starts if s.note.startswith("forget")]
    # Empty retention = keep-all prune (a valid no-op).
    assert forget.argv == (
        "restic",
        "forget",
        "--repo",
        "/var/backups",
        "--prune",
    )


def test_rustic_binary_used_in_argv():
    plan = _valid_plan()
    starts = service_exec_starts(plan, "rustic")
    backup = [s for s in starts if s.note.startswith("backup")][0]
    assert backup.argv[0] == "rustic"


# --- render_all / emitted set ------------------------------------------------


def test_render_all_has_three_units_and_script():
    units = render_all(_valid_plan(), "restic")
    assert set(units.files) == {TIMER_NAME, SERVICE_NAME, ALERT_SERVICE_NAME, ALERT_SCRIPT_NAME}


def test_render_all_filenames_deterministic():
    units = render_all(_valid_plan(), "restic")
    assert units.filenames() == sorted(units.files)


def test_render_all_is_deterministic():
    plan = _valid_plan()
    assert render_all(plan, "restic").files == render_all(plan, "restic").files
