"""Load and validate the planner's ``backup.yaml``.

This module owns the backup planner's input schema: what to back up
(:data:`BackupPlan.sources`), where to store it
(:data:`BackupPlan.targets`), when to run (:data:`BackupPlan.schedule`), and
how many snapshots to keep (:data:`BackupPlan.retention`).

The data model is deliberately plain :mod:`dataclasses` so the ``plan`` and
``render`` subcommands can consume it directly (no YAML left in the pipeline).

Schema
------
A ``backup.yaml`` looks like::

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

Validation
----------
:func:`load_config` raises :class:`~pengyos_backup.errors.ConfigError` with a
human-readable message when the document is missing, unreadable, not a mapping,
or violates the schema. The rules are:

* ``version`` is present and equals the supported schema version (``1``).
* ``sources`` is a non-empty list of non-empty strings.
* ``targets`` has at least one entry; ``local`` must carry a ``path`` and
  ``remote`` must carry a ``url`` (both non-empty strings).
* ``schedule`` carries an ``on_calendar`` string.
* ``retention`` fields, when present, are non-negative integers. The
  "never zero kept snapshots" semantic rule (at least one keep count must be
  positive when any keep policy is named) is enforced here too.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError

#: The only schema version this planner understands.
SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# YAML loading (PyYAML with a bundled minimal fallback reader)
# ---------------------------------------------------------------------------


def _load_yaml(text: str) -> Any:
    """Parse YAML *text*, preferring PyYAML and falling back to a minimal reader.

    The planner declares PyYAML as its only runtime dependency, but a tiny
    fallback reader keeps ``load_config`` usable in bare environments (and in
    tests that don't want to depend on PyYAML being importable). The fallback
    understands the subset of YAML this schema needs: block mappings, block
    sequences, quoted/unquoted scalars, comments, and ``key: value`` pairs.
    """

    try:
        import yaml  # type: ignore
    except Exception:
        return _minimal_yaml_load(text)
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:  # pragma: no cover - depends on PyYAML
        raise ConfigError(f"invalid YAML: {exc}") from exc


def _minimal_yaml_load(text: str) -> Any:
    """A tiny block-YAML reader for the subset this schema uses.

    This is *not* a general YAML parser. It supports nested block mappings,
    block sequences of scalars, comments, blank lines, and scalar values that
    are either quoted (``"..."`` / ``'...'``) or bare. It is only used when
    PyYAML is unavailable.
    """

    lines = text.splitlines()
    return _parse_block(lines, 0, 0)[0]


def _strip_comment(line: str) -> str:
    """Remove a trailing ``#`` comment from *line* (outside quotes)."""

    out = []
    in_single = in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            # A comment must be at a word boundary to count.
            if i == 0 or line[i - 1] in " \t":
                break
        out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _parse_scalar(raw: str) -> Any:
    """Interpret a scalar token (strip quotes, coerce ints/bools/null)."""

    s = raw.strip()
    if not s:
        return None
    if (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'"):
        return s[1:-1]
    low = s.lower()
    if low in ("null", "~", ""):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if s.lstrip("-").isdigit():
        return int(s)
    return s


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_block(lines: list[str], start: int, base_indent: int):
    """Recursively parse a block starting at *start*; return (value, next_i)."""

    # Peek at the first non-empty line to decide mapping vs. sequence.
    idx = start
    first_key = None
    first_indent = None
    while idx < len(lines):
        raw = _strip_comment(lines[idx])
        if raw.strip() == "":
            idx += 1
            continue
        first_key = raw
        first_indent = _indent(raw)
        break

    if first_indent is None:  # empty block
        return None, start

    stripped = first_key.strip()
    if stripped.startswith("- "):
        return _parse_sequence(lines, idx, first_indent)

    return _parse_mapping(lines, idx, first_indent)


def _parse_sequence(lines: list[str], start: int, base_indent: int):
    items: list[Any] = []
    idx = start
    while idx < len(lines):
        raw = _strip_comment(lines[idx])
        if raw.strip() == "":
            idx += 1
            continue
        if _indent(raw) < base_indent:
            break
        stripped = raw.strip()
        if not stripped.startswith("- "):
            break
        value = stripped[2:].strip()
        if ":" in value and not (
            (value[0] == '"' and value[-1] == '"')
            or (value[0] == "'" and value[-1] == "'")
        ):
            # A mapping item on one line: "- key: value"
            key, _, val = value.partition(":")
            items.append({key.strip(): _parse_scalar(val)})
            idx += 1
        else:
            items.append(_parse_scalar(value))
            idx += 1
    return items, idx


def _parse_mapping(lines: list[str], start: int, base_indent: int):
    result: dict[str, Any] = {}
    idx = start
    while idx < len(lines):
        raw = _strip_comment(lines[idx])
        if raw.strip() == "":
            idx += 1
            continue
        if _indent(raw) < base_indent:
            break
        stripped = raw.strip()
        if stripped.startswith("- "):
            break
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "":
            # Nested block: consume deeper-indented lines.
            child, idx = _parse_block(lines, idx + 1, _next_indent(lines, idx + 1))
            result[key] = child
        else:
            result[key] = _parse_scalar(val)
            idx += 1
    return result, idx


def _next_indent(lines: list[str], start: int) -> int:
    for line in lines[start:]:
        if line.strip() == "":
            continue
        return _indent(line)
    return 10**9


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _clean(mapping: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *mapping* with all ``None`` values removed.

    Used by the ``*_dict`` serializers so unset optional fields do not appear
    as JSON ``null``. Insertion order is preserved.
    """

    return {key: value for key, value in mapping.items() if value is not None}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """A single backup repository target.

    ``kind`` is either ``"local"`` (``path`` holds the repo directory) or
    ``"remote"`` (``url`` holds the restic/rustic repository URL).
    """

    kind: str
    path: str | None = None
    url: str | None = None
    host: str | None = None

    @property
    def repo(self) -> str:
        """The repository identifier passed to restic/rustic as ``--repo``."""

        if self.kind == "local":
            assert self.path is not None
            return self.path
        assert self.url is not None
        return self.url

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form of this target.

        Only the fields meaningful to the target's ``kind`` are emitted
        (``path`` for ``local``, ``url`` for ``remote``); ``host`` is included
        only when set. Used by the ``render`` subcommand.
        """

        d: dict[str, Any] = {"kind": self.kind}
        if self.kind == "local":
            d["path"] = self.path
        else:
            d["url"] = self.url
        if self.host is not None:
            d["host"] = self.host
        return d


@dataclass(frozen=True)
class Schedule:
    """The systemd timer schedule.

    ``on_calendar`` is systemd ``OnCalendar`` syntax (e.g. ``"daily"`` or
    ``"Mon..Fri *-*-* 02:30:00"``). ``randomized_delay`` maps onto the systemd
    ``RandomizedDelaySec`` timer option.
    """

    on_calendar: str
    randomized_delay: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form of this schedule.

        ``randomized_delay`` is omitted when unset (see :func:`_clean`).
        """

        return _clean(
            {
                "on_calendar": self.on_calendar,
                "randomized_delay": self.randomized_delay,
            }
        )


@dataclass(frozen=True)
class Retention:
    """A prune/keep policy.

    These map onto restic/rustic ``prune`` flags:
    ``--keep-last``, ``--keep-daily``, ``--keep-weekly``, ``--keep-monthly``,
    ``--keep-yearly`` and ``--keep-within``. All are optional individually;
    the "never zero kept snapshots" semantic rule (at least one keep count must
    be positive when any keep policy is named) is enforced by
    :func:`_validate_never_zero_kept`. :meth:`prune_flags` encodes the policy as
    the restic/rustic ``forget`` argv tokens.
    """

    keep_last: int | None = None
    keep_daily: int | None = None
    keep_weekly: int | None = None
    keep_monthly: int | None = None
    keep_yearly: int | None = None
    keep_within: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form of this retention policy.

        Every field is ``None`` when unset, so :func:`_clean` drops them and
        an empty :class:`Retention` renders as ``{}``.
        """

        return _clean(
            {
                "keep_last": self.keep_last,
                "keep_daily": self.keep_daily,
                "keep_weekly": self.keep_weekly,
                "keep_monthly": self.keep_monthly,
                "keep_yearly": self.keep_yearly,
                "keep_within": self.keep_within,
            }
        )

    def prune_flags(self) -> list[str]:
        """The restic/rustic ``forget`` keep flags for this policy, as argv.

        This is the retention policy *encoded as data*: the tokens restic/rustic
        ``forget`` consumes, in flag order — ``--keep-last N``, ``--keep-daily
        N``, ``--keep-weekly N``, ``--keep-monthly N``, ``--keep-yearly N``,
        and (last) ``--keep-within D``. A field is emitted only when it is set
        (not ``None``); a field that is explicitly ``0`` is still emitted (a
        zero keep is a real value, not "unset"). An empty policy returns ``[]``
        (keep-all), so a caller builds ``forget --repo <repo> --prune``.

        This is the single source of truth for the keep-flag mapping; the plan's
        forget argv (``pengyos_backup.units._forget_argv``) consumes it rather
        than re-deriving the flag table.
        """

        flags: list[str] = []
        for flag, name in (
            ("--keep-last", "keep_last"),
            ("--keep-daily", "keep_daily"),
            ("--keep-weekly", "keep_weekly"),
            ("--keep-monthly", "keep_monthly"),
            ("--keep-yearly", "keep_yearly"),
        ):
            value = getattr(self, name)
            if value is not None:
                flags += [flag, str(value)]
        if self.keep_within is not None:
            flags += ["--keep-within", self.keep_within]
        return flags


@dataclass(frozen=True)
class BackupPlan:
    """The parsed, validated backup planner configuration."""

    version: int
    sources: tuple[str, ...]
    targets: tuple[Target, ...]
    schedule: Schedule
    retention: Retention

    @property
    def local_targets(self) -> tuple[Target, ...]:
        return tuple(t for t in self.targets if t.kind == "local")

    @property
    def remote_targets(self) -> tuple[Target, ...]:
        return tuple(t for t in self.targets if t.kind == "remote")

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form of this plan.

        The keys mirror the ``backup.yaml`` sections (``version``, ``sources``,
        ``targets``, ``schedule``, ``retention``) and are emitted in that order
        so the ``render`` subcommand produces stable, readable output.
        """

        return {
            "version": self.version,
            "sources": list(self.sources),
            "targets": [t.to_dict() for t in self.targets],
            "schedule": self.schedule.to_dict(),
            "retention": self.retention.to_dict(),
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"missing required field '{key}' in {where}")
    return mapping[key]


def _validate_targets(raw: Any) -> tuple[Target, ...]:
    if not isinstance(raw, dict):
        raise ConfigError("'targets' must be a mapping with 'local'/'remote'")

    targets: list[Target] = []
    if "local" in raw:
        loc = raw["local"]
        if not isinstance(loc, dict):
            raise ConfigError("'targets.local' must be a mapping")
        path = loc.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ConfigError("'targets.local.path' must be a non-empty string")
        host = loc.get("host")
        if host is not None and not isinstance(host, str):
            raise ConfigError("'targets.local.host' must be a string")
        targets.append(
            Target(kind="local", path=path, host=host)
        )

    if "remote" in raw:
        rem = raw["remote"]
        if not isinstance(rem, dict):
            raise ConfigError("'targets.remote' must be a mapping")
        url = rem.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ConfigError("'targets.remote.url' must be a non-empty string")
        host = rem.get("host")
        if host is not None and not isinstance(host, str):
            raise ConfigError("'targets.remote.host' must be a string")
        targets.append(
            Target(kind="remote", url=url, host=host)
        )

    if not targets:
        raise ConfigError(
            "'targets' must declare at least one of 'local' or 'remote'"
        )
    return tuple(targets)


def _validate_schedule(raw: Any) -> Schedule:
    if not isinstance(raw, dict):
        raise ConfigError("'schedule' must be a mapping")
    on_calendar = _require(raw, "on_calendar", "schedule")
    if not isinstance(on_calendar, str) or not on_calendar.strip():
        raise ConfigError("'schedule.on_calendar' must be a non-empty string")
    randomized_delay = raw.get("randomized_delay")
    if randomized_delay is not None and not isinstance(randomized_delay, str):
        raise ConfigError(
            "'schedule.randomized_delay' must be a string when present"
        )
    return Schedule(on_calendar=on_calendar.strip(), randomized_delay=randomized_delay)


def _validate_retention(raw: Any) -> Retention:
    if raw is None:
        # Retention is optional at the schema layer; an absent policy renders as
        # a keep-all ``forget --prune`` (valid, intentional no-op). The
        # "never zero kept snapshots" rule is applied once counts are parsed.
        return Retention()
    if not isinstance(raw, dict):
        raise ConfigError("'retention' must be a mapping")

    int_fields = (
        "keep_last",
        "keep_daily",
        "keep_weekly",
        "keep_monthly",
        "keep_yearly",
    )
    parsed: dict[str, Any] = {}
    for name in int_fields:
        if name not in raw:
            continue
        value = raw[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"'retention.{name}' must be an integer")
        if value < 0:
            raise ConfigError(f"'retention.{name}' must be non-negative")
        parsed[name] = value

    keep_within = raw.get("keep_within")
    if keep_within is not None:
        if isinstance(keep_within, bool) or not isinstance(keep_within, str):
            raise ConfigError(
                "'retention.keep_within' must be a string when present"
            )
        parsed["keep_within"] = keep_within

    _validate_never_zero_kept(parsed)

    return Retention(**parsed)


# The keep-* counts, in canonical order. ``keep_within`` is a duration, not a
# count, so it does not count toward "keeping snapshots".
_KEEP_FIELDS: tuple[str, ...] = (
    "keep_last",
    "keep_daily",
    "keep_weekly",
    "keep_monthly",
    "keep_yearly",
)


def _validate_never_zero_kept(parsed: dict[str, Any]) -> None:
    """Enforce the "never zero kept snapshots" semantic rule.

    A retention policy that keeps zero snapshots is a footgun: restic/rustic
    ``forget --prune`` would delete every snapshot in the repo. This rule makes
    the restore proof have teeth — a policy that would delete *all* snapshots is
    rejected at planning time.

    The rule is: if any keep count is present (i.e. the operator named at least
    one keep policy), at least one of them must be positive. An *absent* policy
    (every count ``None`` — no ``retention`` section, or an empty one) is left
    alone: that renders as a keep-all ``forget --prune``, a valid, documented
    no-op that the plan task treats as intentional, not a zero-keep trap.
    """

    present = [parsed[name] for name in _KEEP_FIELDS if name in parsed]
    if present and all(count == 0 for count in present):
        raise ConfigError(
            "retention keeps zero snapshots: set at least one of "
            + ", ".join(f"'retention.{name}'" for name in _KEEP_FIELDS)
            + " to a positive integer"
        )


def _validate_plan(raw: Any) -> BackupPlan:
    if raw is None:
        raise ConfigError("config document is empty")
    if not isinstance(raw, dict):
        raise ConfigError("config must be a YAML mapping at the top level")

    version = _require(raw, "version", "config")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ConfigError("'version' must be an integer")
    if version != SCHEMA_VERSION:
        raise ConfigError(
            f"unsupported schema version {version}; "
            f"expected {SCHEMA_VERSION}"
        )

    sources_raw = _require(raw, "sources", "config")
    if not isinstance(sources_raw, list) or not sources_raw:
        raise ConfigError("'sources' must be a non-empty list")
    sources: list[str] = []
    for item in sources_raw:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError("every entry in 'sources' must be a non-empty string")
        sources.append(item)

    targets_raw = _require(raw, "targets", "config")
    targets = _validate_targets(targets_raw)

    schedule_raw = _require(raw, "schedule", "config")
    schedule = _validate_schedule(schedule_raw)

    retention = _validate_retention(raw.get("retention"))

    return BackupPlan(
        version=version,
        sources=tuple(sources),
        targets=targets,
        schedule=schedule,
        retention=retention,
    )


def load_config(path: str | os.PathLike[str]) -> BackupPlan:
    """Load, parse, and validate ``backup.yaml`` at *path*.

    Raises :class:`~pengyos_backup.errors.ConfigError` if the file is missing,
    unreadable, invalid YAML, or does not satisfy the schema.
    """

    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config not found: {p}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read config {p}: {exc}") from exc

    raw = _load_yaml(text)
    return _validate_plan(raw)


def validate(raw: Any) -> BackupPlan:
    """Validate an already-parsed config document (dict or other)."""

    return _validate_plan(copy.deepcopy(raw))
