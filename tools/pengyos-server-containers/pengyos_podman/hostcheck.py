"""Host prerequisite checks for rootless podman (report-only).

Rootless podman — the mode this project targets — needs the *current user* to
have a subordinate UID and GID range, plus a container storage configuration.
This module **probes the host** to report on those three prerequisites. It reads
files and may run read-only commands (``getent``, ``id``) — it **never runs
podman/systemd and never modifies the host**.

The three prerequisites (documented here so they stay frozen / golden-testable):

1. **subuid** — a subordinate UID range for the user, from ``/etc/subuid`` or
   ``~/.config/subuid`` (or ``getent subuid <user>``).
2. **subgid** — a subordinate GID range for the user, from ``/etc/subgid`` or
   ``~/.config/subgid`` (or ``getent subgid <user>``).
3. **storage.conf** — a container storage configuration, found under
   ``~/.config/containers/storage.conf`` or ``/etc/containers/storage.conf``.

Every probe is injectable (file-reader, command-runner, path lists) so the
checks are fully testable with fixtures and no root / network / podman.
"""

from __future__ import annotations

import getpass
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

# --- Prerequisite identifiers -------------------------------------------------
# Stable string identifiers (kept as module constants so the report and the
# tests can refer to them without importing internals).
PREREQ_SUBUID = "subuid"
PREREQ_SUBGID = "subgid"
PREREQ_STORAGE_CONF = "storage.conf"

# --- Finding statuses ---------------------------------------------------------
STATUS_OK = "OK"
STATUS_MISSING = "MISSING"
STATUS_UNKNOWN = "UNKNOWN"

# --- Default host locations ---------------------------------------------------
# Subordinate-UID / -GID files. The per-user file (``~/.config/subuid``) is what
# a rootless setup typically adds and is authoritative; the system file applies
# to every user.
DEFAULT_SUBUID_PATHS = ("/etc/subuid", "~/.config/subuid")
DEFAULT_SUBGID_PATHS = ("/etc/subgid", "~/.config/subgid")

# Container storage configuration locations podman reads (first match wins for
# the "found" report; we still list every location we inspected).
DEFAULT_STORAGE_CONF_PATHS = (
    "~/.config/containers/storage.conf",
    "/usr/local/etc/containers/storage.conf",
    "/etc/containers/storage.conf",
)

# Command runner used for the optional ``getent`` fallback. Injectable so tests
# never spawn a real process (and never need podman/systemd).
Runner = Callable[[Sequence[str]], Optional[str]]


@dataclass(frozen=True)
class Finding:
    """One prerequisite's result.

    Attributes:
        name: The prerequisite identifier (see the ``PREREQ_*`` constants).
        status: ``OK`` / ``MISSING`` / ``UNKNOWN``.
        message: A human-readable one-line summary of the result.
        detail: Supplementary detail (the entries found, or the paths checked).
    """

    name: str
    status: str
    message: str
    detail: str = ""


def _current_user() -> str:
    """Best-effort current username (never raises)."""

    for candidate in (os.environ.get("USER"), os.environ.get("LOGNAME")):
        if candidate:
            return candidate
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "unknown")


def _expand(path: str) -> str:
    """Expand ``~`` and environment variables in a path."""

    return os.path.expanduser(os.path.expandvars(path))


def _parse_range_line(line: str, user: str) -> Optional[str]:
    """Return ``start:count`` if ``line`` grants ``user`` a subordinate range.

    subuid/subgid lines are ``user:start:count``. Returns ``None`` for blank
    lines, comments, malformed lines, or lines that do not belong to ``user``.
    """

    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split(":")
    if len(parts) < 3 or parts[0] != user:
        return None
    start, count = parts[1], parts[2]
    if not (start.isdigit() and count.isdigit()):
        return None
    return f"{start}:{count}"


def _read_matching_entries(
    path: str,
    user: str,
    read_file: Callable[[str], Optional[str]],
) -> list[str]:
    """Return the ``start:count`` ranges for ``user`` from ``path`` (or []).

    ``read_file`` receives the *configured* path (e.g. ``~/.config/subuid``) and
    is responsible for expanding + reading it — so tests can inject a reader
    keyed on logical paths.
    """

    text = read_file(path)
    if text is None:
        return []
    return [
        matched
        for line in text.splitlines()
        if (matched := _parse_range_line(line, user)) is not None
    ]


def _default_read_file(path: str) -> Optional[str]:
    """Read a file from disk, returning ``None`` if it cannot be read."""

    try:
        with open(_expand(path), "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _default_run(argv: Sequence[str]) -> Optional[str]:
    """Run a command, returning stdout, or ``None`` on any failure."""

    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _getent_range(
    database: str,
    user: str,
    run: Runner,
) -> list[str]:
    """Return ``user``'s ranges via ``getent <database> <user>`` (or []).

    ``run`` is injectable so tests never spawn a process; the default runner
    returns ``None`` when ``getent`` is unavailable or the database is unknown
    (as on minimal images where ``getent subuid`` is unsupported).
    """

    result = run(["getent", database, user])
    if result is None:
        return []
    return [
        matched
        for line in result.splitlines()
        if (matched := _parse_range_line(line, user)) is not None
    ]


def get_subuid(
    user: str,
    *,
    paths: Sequence[str] = DEFAULT_SUBUID_PATHS,
    read_file: Callable[[str], Optional[str]],
    run: Runner,
) -> list[str]:
    """Return the subordinate-UID ranges granted to ``user`` (may be empty).

    Checks every configured file (per-user first, then system-wide) and falls
    back to ``getent subuid``. Returns the deduplicated ``start:count`` ranges.
    """

    ranges: list[str] = []
    seen: set[str] = set()
    for path in paths:
        for matched in _read_matching_entries(path, user, read_file):
            if matched not in seen:
                seen.add(matched)
                ranges.append(matched)
    for matched in _getent_range("subuid", user, run):
        if matched not in seen:
            seen.add(matched)
            ranges.append(matched)
    return ranges


def get_subgid(
    user: str,
    *,
    paths: Sequence[str] = DEFAULT_SUBGID_PATHS,
    read_file: Callable[[str], Optional[str]],
    run: Runner,
) -> list[str]:
    """Return the subordinate-GID ranges granted to ``user`` (may be empty)."""

    ranges: list[str] = []
    seen: set[str] = set()
    for path in paths:
        for matched in _read_matching_entries(path, user, read_file):
            if matched not in seen:
                seen.add(matched)
                ranges.append(matched)
    for matched in _getent_range("subgid", user, run):
        if matched not in seen:
            seen.add(matched)
            ranges.append(matched)
    return ranges


def find_storage_conf(
    *,
    paths: Sequence[str] = DEFAULT_STORAGE_CONF_PATHS,
    read_file: Callable[[str], Optional[str]],
) -> list[str]:
    """Return the existing storage.conf locations (empty == none found).

    ``read_file`` is the single source of truth: it returns the file's text when
    the path exists and is readable, or ``None`` otherwise (so a directory or
    unreadable file — both of which the default reader reports as ``None`` —
    count as "not found"). This keeps the check injectable in tests.
    """

    found: list[str] = []
    for path in paths:
        if read_file(path) is not None:
            found.append(_expand(path))
    return found


def _subuid_finding(
    user: str,
    ranges: list[str],
    paths: Sequence[str],
) -> Finding:
    if ranges:
        return Finding(
            name=PREREQ_SUBUID,
            status=STATUS_OK,
            message="subordinate UID range configured",
            detail=", ".join(f"{user}:{r}" for r in ranges),
        )
    checked = ", ".join(_expand(p) for p in paths)
    return Finding(
        name=PREREQ_SUBUID,
        status=STATUS_MISSING,
        message="no subordinate UID range for this user",
        detail=f"checked: {checked}",
    )


def _subgid_finding(
    user: str,
    ranges: list[str],
    paths: Sequence[str],
) -> Finding:
    if ranges:
        return Finding(
            name=PREREQ_SUBGID,
            status=STATUS_OK,
            message="subordinate GID range configured",
            detail=", ".join(f"{user}:{r}" for r in ranges),
        )
    checked = ", ".join(_expand(p) for p in paths)
    return Finding(
        name=PREREQ_SUBGID,
        status=STATUS_MISSING,
        message="no subordinate GID range for this user",
        detail=f"checked: {checked}",
    )


def _storage_conf_finding(
    found: list[str],
) -> Finding:
    if found:
        return Finding(
            name=PREREQ_STORAGE_CONF,
            status=STATUS_OK,
            message="storage configuration found",
            detail=", ".join(found),
        )
    checked = ", ".join(_expand(p) for p in DEFAULT_STORAGE_CONF_PATHS)
    return Finding(
        name=PREREQ_STORAGE_CONF,
        status=STATUS_MISSING,
        message="no container storage configuration found",
        detail=(
            f"checked: {checked}\n"
            "create one, e.g. ~/.config/containers/storage.conf "
            "(see podman-storage.conf(5))"
        ),
    )


def check_host(
    user: Optional[str] = None,
    *,
    subuid_paths: Sequence[str] = DEFAULT_SUBUID_PATHS,
    subgid_paths: Sequence[str] = DEFAULT_SUBGID_PATHS,
    storage_conf_paths: Sequence[str] = DEFAULT_STORAGE_CONF_PATHS,
    read_file: Callable[[str], Optional[str]] = _default_read_file,
    run: Runner = _default_run,
) -> list[Finding]:
    """Probe the host for the rootless-podman prerequisites.

    Returns the three findings in a stable order (subuid, subgid, storage.conf).
    Report-only: this never modifies the host and never runs podman/systemd.
    """

    who = user or _current_user()
    subuid = get_subuid(who, paths=subuid_paths, read_file=read_file, run=run)
    subgid = get_subgid(who, paths=subgid_paths, read_file=read_file, run=run)
    storage = find_storage_conf(paths=storage_conf_paths, read_file=read_file)
    return [
        _subuid_finding(who, subuid, subuid_paths),
        _subgid_finding(who, subgid, subgid_paths),
        _storage_conf_finding(storage),
    ]


def render_report(findings: Sequence[Finding]) -> str:
    """Render findings as an aligned, human-readable report (trailing newline)."""

    width = max((len(f.name) for f in findings), default=0)
    lines = [
        "Rootless podman host prerequisites (report only — nothing modified)",
        "==================================================================",
    ]
    for finding in findings:
        lines.append(f"{finding.name.ljust(width)}: {finding.status}")
        lines.append(f"    {finding.message}")
        if finding.detail:
            for detail_line in finding.detail.splitlines():
                lines.append(f"    {detail_line}")
    return "\n".join(lines) + "\n"


# Re-exported for callers that want to inject a fake reader/runner in tests.
__all__ = [
    "DEFAULT_STORAGE_CONF_PATHS",
    "DEFAULT_SUBGID_PATHS",
    "DEFAULT_SUBUID_PATHS",
    "Finding",
    "PREREQ_STORAGE_CONF",
    "PREREQ_SUBGID",
    "PREREQ_SUBUID",
    "STATUS_MISSING",
    "STATUS_OK",
    "STATUS_UNKNOWN",
    "check_host",
    "find_storage_conf",
    "get_subgid",
    "get_subuid",
    "render_report",
]
