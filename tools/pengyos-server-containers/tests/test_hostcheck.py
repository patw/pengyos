"""Tests for the report-only rootless-podman host prerequisite check.

The check probes the host (reads subuid/subgid files, may run ``getent``) but
never runs podman/systemd and never modifies the host. Every probe is
injectable, so these tests need no root, network, or real podman.
"""

from __future__ import annotations

import os

from pengyos_podman import hostcheck as hc
from pengyos_podman.hostcheck import (
    Finding,
    PREREQ_STORAGE_CONF,
    PREREQ_SUBGID,
    PREREQ_SUBUID,
    STATUS_MISSING,
    STATUS_OK,
    STATUS_UNKNOWN,
    check_host,
    find_storage_conf,
    get_subgid,
    get_subuid,
    render_report,
)


def _reader(mapping: dict[str, str | None]) -> callable:
    """Build a ``read_file`` injection: logical path -> contents (or None)."""

    def _read(path: str) -> str | None:
        return mapping.get(path)

    return _read


# --- _parse_range_line --------------------------------------------------------


def test_parse_range_line_matches_user() -> None:
    assert hc._parse_range_line("alex:100000:65536", "alex") == "100000:65536"


def test_parse_range_line_rejects_other_user() -> None:
    assert hc._parse_range_line("someone_else:100000:65536", "alex") is None


def test_parse_range_line_rejects_comments_and_blanks() -> None:
    assert hc._parse_range_line("# a comment", "alex") is None
    assert hc._parse_range_line("", "alex") is None
    assert hc._parse_range_line("   ", "alex") is None


def test_parse_range_line_rejects_malformed() -> None:
    # Non-numeric range.
    assert hc._parse_range_line("alex:abc:65536", "alex") is None
    # Too few fields.
    assert hc._parse_range_line("alex:100000", "alex") is None
    # Empty range fields.
    assert hc._parse_range_line("alex::", "alex") is None


# --- get_subuid / get_subgid --------------------------------------------------


def test_get_subuid_reads_system_file() -> None:
    read = _reader({"/etc/subuid": "alex:100000:65536\n"})
    assert get_subuid("alex", read_file=read, run=lambda a: None) == ["100000:65536"]


def test_get_subgid_reads_system_file() -> None:
    read = _reader({"/etc/subgid": "alex:100000:65536\n"})
    assert get_subgid("alex", read_file=read, run=lambda a: None) == ["100000:65536"]


def test_get_subuid_reads_per_user_file() -> None:
    # A per-user entry is what a rootless setup typically adds.
    read = _reader({"~/.config/subuid": "alex:200000:100000\n"})
    assert get_subuid("alex", read_file=read, run=lambda a: None) == ["200000:100000"]


def test_get_subuid_dedupes_across_sources() -> None:
    # Present in both the system and per-user file -> reported once.
    read = _reader(
        {"/etc/subuid": "alex:100000:65536\n", "~/.config/subuid": "alex:100000:65536\n"}
    )
    assert get_subuid("alex", read_file=read, run=lambda a: None) == ["100000:65536"]


def test_get_subuid_collects_multiple_ranges() -> None:
    read = _reader(
        {"/etc/subuid": "alex:100000:65536\nalex:200000:100000\n"}
    )
    assert get_subuid("alex", read_file=read, run=lambda a: None) == [
        "100000:65536",
        "200000:100000",
    ]


def test_get_subuid_ignores_comments_and_other_users() -> None:
    read = _reader(
        {
            "/etc/subuid": (
                "# comment\n"
                "alex:100000:65536\n"
                "root:1000:65536\n"
            )
        }
    )
    assert get_subuid("alex", read_file=read, run=lambda a: None) == ["100000:65536"]


def test_get_subuid_uses_getent_fallback() -> None:
    # No files present; getent supplies the range.
    read = _reader({})
    def run(argv):
        if list(argv) == ["getent", "subuid", "alex"]:
            return "alex:300000:65536\n"
        return None

    assert get_subuid("alex", read_file=read, run=run) == ["300000:65536"]


def test_get_subuid_getent_unsupported_yields_empty() -> None:
    # getent unsupported (unknown database) -> treated as "no entry".
    read = _reader({})
    assert get_subuid("alex", read_file=read, run=lambda a: None) == []


def test_get_subuid_empty_when_nothing_configured() -> None:
    assert get_subuid("nobody", read_file=_reader({}), run=lambda a: None) == []


# --- find_storage_conf --------------------------------------------------------


def test_find_storage_conf_found_at_first_path() -> None:
    read = _reader({"~/.config/containers/storage.conf": "[storage]\n"})
    found = find_storage_conf(read_file=read)
    assert len(found) == 1
    assert found[0].endswith("/.config/containers/storage.conf")


def test_find_storage_conf_found_at_later_path() -> None:
    read = _reader({"/etc/containers/storage.conf": "[storage]\n"})
    found = find_storage_conf(read_file=read)
    assert len(found) == 1
    assert found[0] == "/etc/containers/storage.conf"


def test_find_storage_conf_none_found() -> None:
    assert find_storage_conf(read_file=_reader({})) == []


def test_default_read_file_ignores_directory() -> None:
    # The default reader reports a directory as "not found" (IsADirectoryError),
    # so a directory never counts as a storage.conf.
    assert hc._default_read_file(os.getcwd()) is None


# --- check_host ---------------------------------------------------------------


def test_check_host_returns_three_findings_in_order() -> None:
    findings = check_host(
        "alex",
        read_file=_reader(
            {
                "/etc/subuid": "alex:100000:65536\n",
                "/etc/subgid": "alex:100000:65536\n",
            }
        ),
        run=lambda a: None,
    )
    assert [f.name for f in findings] == [PREREQ_SUBUID, PREREQ_SUBGID, PREREQ_STORAGE_CONF]


def test_check_host_all_ok() -> None:
    findings = check_host(
        "alex",
        read_file=_reader(
            {
                "/etc/subuid": "alex:100000:65536\n",
                "/etc/subgid": "alex:100000:65536\n",
                "~/.config/containers/storage.conf": "[storage]\n",
            }
        ),
        run=lambda a: None,
    )
    assert [f.status for f in findings] == [STATUS_OK, STATUS_OK, STATUS_OK]


def test_check_host_all_missing() -> None:
    findings = check_host(
        "ghost",
        read_file=_reader({}),
        run=lambda a: None,
    )
    assert [f.status for f in findings] == [
        STATUS_MISSING,
        STATUS_MISSING,
        STATUS_MISSING,
    ]


def test_check_host_ok_finding_has_range_detail() -> None:
    findings = check_host(
        "alex",
        read_file=_reader({"/etc/subuid": "alex:100000:65536\n"}),
        run=lambda a: None,
    )
    subuid = findings[0]
    assert isinstance(subuid, Finding)
    assert subuid.name == PREREQ_SUBUID
    assert "100000:65536" in subuid.detail


def test_check_host_missing_finding_lists_checked_paths() -> None:
    findings = check_host(
        "ghost",
        read_file=_reader({}),
        run=lambda a: None,
    )
    storage = findings[2]
    assert storage.name == PREREQ_STORAGE_CONF
    assert "/etc/containers/storage.conf" in storage.detail


# --- render_report ------------------------------------------------------------


def test_render_report_contains_statuses() -> None:
    report = render_report(
        [
            Finding(PREREQ_SUBUID, STATUS_OK, "subordinate UID range configured"),
            Finding(PREREQ_SUBGID, STATUS_MISSING, "no subordinate GID range"),
            Finding(PREREQ_STORAGE_CONF, STATUS_OK, "storage configuration found"),
        ]
    )
    assert "subuid" in report
    assert "subgid" in report
    assert "storage.conf" in report
    assert STATUS_OK in report
    assert STATUS_MISSING in report


def test_render_report_contains_detail() -> None:
    report = render_report(
        [
            Finding(
                PREREQ_SUBUID,
                STATUS_OK,
                "subordinate UID range configured",
                "alex:100000:65536",
            )
        ]
    )
    assert "alex:100000:65536" in report


def test_render_report_ends_with_newline() -> None:
    report = render_report([Finding(PREREQ_SUBUID, STATUS_OK, "ok")])
    assert report.endswith("\n")


def test_render_report_is_deterministic() -> None:
    findings = [
        Finding(PREREQ_SUBUID, STATUS_OK, "ok"),
        Finding(PREREQ_SUBGID, STATUS_OK, "ok"),
        Finding(PREREQ_STORAGE_CONF, STATUS_MISSING, "missing"),
    ]
    assert render_report(findings) == render_report(findings)


# --- real host (defaults) -----------------------------------------------------


def test_check_host_defaults_returns_three_findings() -> None:
    # Environment-independent: whatever the host has, there are exactly three
    # findings in a stable order with valid statuses.
    findings = check_host()
    assert [f.name for f in findings] == [PREREQ_SUBUID, PREREQ_SUBGID, PREREQ_STORAGE_CONF]
    for finding in findings:
        assert finding.status in (STATUS_OK, STATUS_MISSING, STATUS_UNKNOWN)


def test_default_read_file_reads_real_subuid() -> None:
    # /etc/subuid is world-readable on a normal host; the default reader reads it.
    text = hc._default_read_file("/etc/subuid")
    if os.path.isfile("/etc/subuid"):
        assert isinstance(text, str)


def test_default_run_returns_stdout_and_none_on_failure() -> None:
    assert hc._default_run(["true"]) == ""
    assert hc._default_run(["false"]) is None
