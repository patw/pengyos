"""Tests for the ``validate`` command: syntax, conflicts, required rules.

Covers spec.md "Verification":
- conflict detection (same symbol =y and =n) reported,
- syntax failures surfaced,
- the systemd required-pairs rule fires when a needed symbol is missing.
"""

from __future__ import annotations

import pytest

from pengyos_kernel_fragments.cli import main
from pengyos_kernel_fragments.fragments import parse_fragment_text
from pengyos_kernel_fragments.validate import (
    CODE_CONFLICT,
    CODE_MISSING_REQUIRED,
    CODE_SYNTAX,
    Conflict,
    RequiredRule,
    REQUIRED_RULES,
    Severity,
    format_report,
    select_rules,
    validate_directory,
    validate_fragments,
)


def _frag(*lines: str, name: str = "f.fragment"):
    return parse_fragment_text("\n".join(lines), name=name)


# --------------------------------------------------------------------------- #
# Syntax surfacing
# --------------------------------------------------------------------------- #


def test_validate_surfaces_syntax_errors():
    frag = _frag("CONFIG_FOO y", "CONFIG_BAR=99", name="bad.fragment")
    report = validate_fragments([frag])
    assert not report.ok
    syntax = [f for f in report.findings if f.code == CODE_SYNTAX]
    assert len(syntax) == 2
    assert syntax[0].source == "bad.fragment"
    assert syntax[0].line_no == 1
    assert syntax[1].line_no == 2
    assert all(f.severity is Severity.ERROR for f in syntax)


def test_clean_fragment_has_no_findings():
    # With no required rules, a syntactically clean fragment has no findings.
    frag = _frag("CONFIG_FOO=y", "CONFIG_BAR=m", name="clean.fragment")
    report = validate_fragments([frag], rules=())
    assert report.ok
    assert report.problems == 0
    assert report.exit_code == 0


def test_valid_assignment_is_not_a_syntax_error():
    frag = _frag('CONFIG_CMDLINE="root=/dev/sda1 # quiet"', name="f.fragment")
    report = validate_fragments([frag], rules=())
    assert report.ok


# --------------------------------------------------------------------------- #
# Conflict detection
# --------------------------------------------------------------------------- #


def test_conflict_detected_across_fragments():
    a = _frag("CONFIG_FOO=y", name="a.fragment")
    b = _frag("CONFIG_FOO=n", name="b.fragment")
    report = validate_fragments([a, b])
    conflicts = [c for c in report.conflicts if c.symbol == "CONFIG_FOO"]
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert isinstance(conflict, Conflict)
    assert conflict.values == (("a.fragment", "y", 1), ("b.fragment", "n", 1))
    conflict_findings = [
        f for f in report.findings if f.code == CODE_CONFLICT
    ]
    assert len(conflict_findings) == 1
    assert conflict_findings[0].symbol == "CONFIG_FOO"
    assert report.exit_code == 1


def test_conflict_detected_within_a_single_file():
    frag = _frag("CONFIG_DUP=y", "CONFIG_DUP=n", name="dup.fragment")
    report = validate_fragments([frag])
    assert any(c.symbol == "CONFIG_DUP" for c in report.conflicts)


def test_same_value_is_not_a_conflict():
    a = _frag("CONFIG_FOO=y", name="a.fragment")
    b = _frag("CONFIG_FOO=y", name="b.fragment")
    report = validate_fragments([a, b], rules=())
    assert not report.conflicts
    assert report.ok


def test_conflict_message_lists_every_assignment():
    a = _frag("CONFIG_FOO=y", name="a.fragment")
    b = _frag("CONFIG_FOO=m", name="b.fragment")
    report = validate_fragments([a, b])
    finding = next(f for f in report.findings if f.code == CODE_CONFLICT)
    assert "a.fragment=y@1" in finding.message
    assert "b.fragment=m@1" in finding.message


def test_no_conflict_for_distinct_symbols():
    a = _frag("CONFIG_A=y", name="a.fragment")
    b = _frag("CONFIG_B=n", name="b.fragment")
    report = validate_fragments([a, b])
    assert not report.conflicts


# --------------------------------------------------------------------------- #
# Required-symbol rules (declared as data)
# --------------------------------------------------------------------------- #


def test_required_rule_fires_when_symbol_missing():
    frag = _frag("CONFIG_CGROUPS=y", "CONFIG_EPOLL=y", name="f.fragment")
    report = validate_fragments([frag])
    missing = [f for f in report.findings if f.code == CODE_MISSING_REQUIRED]
    assert len(missing) == 1
    assert missing[0].symbol == "CONFIG_INOTIFY"
    assert missing[0].source == "systemd"
    assert "CONFIG_INOTIFY" in missing[0].message


def test_required_rule_satisfied_when_all_present():
    frag = _frag(
        "CONFIG_CGROUPS=y",
        "CONFIG_EPOLL=y",
        "CONFIG_INOTIFY=y",
        name="f.fragment",
    )
    report = validate_fragments([frag])
    assert not any(
        f.code == CODE_MISSING_REQUIRED for f in report.findings
    )


def test_required_rule_fires_when_symbol_disabled():
    frag = _frag(
        "CONFIG_CGROUPS=n",
        "CONFIG_EPOLL=y",
        "CONFIG_INOTIFY=y",
        name="f.fragment",
    )
    report = validate_fragments([frag])
    missing = [f for f in report.findings if f.code == CODE_MISSING_REQUIRED]
    assert len(missing) == 1
    assert missing[0].symbol == "CONFIG_CGROUPS"


def test_required_rule_enabled_value_other_than_n_counts():
    frag = _frag("CONFIG_CGROUPS=m", "CONFIG_EPOLL=y", "CONFIG_INOTIFY=y")
    report = validate_fragments([frag])
    assert not any(
        f.code == CODE_MISSING_REQUIRED for f in report.findings
    )


def test_required_rule_is_data():
    rule = REQUIRED_RULES[0]
    assert isinstance(rule, RequiredRule)
    assert rule.name == "systemd"
    assert ("CONFIG_CGROUPS", "CONFIG_EPOLL", "CONFIG_INOTIFY") == rule.symbols
    assert "systemd requires" in rule.message


def test_custom_rules_can_be_supplied():
    frag = _frag("CONFIG_FOO=y", name="f.fragment")
    rules = (RequiredRule(name="demo", symbols=("CONFIG_FOO", "CONFIG_BAR")),)
    report = validate_fragments([frag], rules=rules)
    missing = [f for f in report.findings if f.code == CODE_MISSING_REQUIRED]
    assert len(missing) == 1
    assert missing[0].symbol == "CONFIG_BAR"
    assert missing[0].source == "demo"


# --------------------------------------------------------------------------- #
# Rule selection
# --------------------------------------------------------------------------- #


def test_select_rules_all_when_no_profile():
    assert select_rules(REQUIRED_RULES, None) == REQUIRED_RULES


def test_select_rules_filters_by_name():
    selected = select_rules(REQUIRED_RULES, "systemd")
    assert selected == (REQUIRED_RULES[0],)


def test_select_rules_case_insensitive():
    assert select_rules(REQUIRED_RULES, "SYSTEMD") == (REQUIRED_RULES[0],)


def test_select_rules_unknown_profile_raises():
    with pytest.raises(ValueError):
        select_rules(REQUIRED_RULES, "does-not-exist")


# --------------------------------------------------------------------------- #
# Deterministic ordering
# --------------------------------------------------------------------------- #


def test_findings_are_sorted_deterministically():
    a = _frag("CONFIG_ZZZ=n", "CONFIG_AAA=n", name="a.fragment")
    b = _frag("CONFIG_ZZZ=y", "CONFIG_AAA=y", name="b.fragment")
    first = validate_fragments([a, b])
    second = validate_fragments([b, a])
    # Reversing the input order must not change the sorted output.
    assert [f.format() for f in first.findings] == [
        f.format() for f in second.findings
    ]
    # Errors sort before anything else, by source then symbol.
    sources = [(f.source, f.symbol) for f in first.findings]
    assert sources == sorted(sources)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def test_format_report_clean():
    frag = _frag("CONFIG_FOO=y", name="f.fragment")
    report = validate_fragments([frag], rules=())
    assert "OK:" in format_report(report)


def test_format_report_lists_problems():
    a = _frag("CONFIG_FOO=y", name="a.fragment")
    b = _frag("CONFIG_FOO=n", name="b.fragment")
    report = validate_fragments([a, b], rules=())
    text = format_report(report)
    assert "1 problem" in text
    assert "[CONFLICT]" in text


def test_report_counts_problems():
    a = _frag("CONFIG_FOO=y", name="a.fragment")
    b = _frag("CONFIG_FOO=n", name="b.fragment")
    report = validate_fragments([a, b], rules=())
    assert report.problems == 1
    assert len(report.fragment_names) == 2


# --------------------------------------------------------------------------- #
# End-to-end directory loading + validation
# --------------------------------------------------------------------------- #


def test_validate_directory_loads_and_reports(tmp_path):
    (tmp_path / "good.fragment").write_text("CONFIG_FOO=y\n", encoding="utf-8")
    (tmp_path / "bad.fragment").write_text("CONFIG_BAR=oops\n", encoding="utf-8")
    report = validate_directory(tmp_path)
    assert list(report.fragment_names) == [
        "bad.fragment",
        "good.fragment",
    ]
    assert not report.ok
    assert any(f.code == CODE_SYNTAX for f in report.findings)


def test_validate_directory_clean(tmp_path):
    # No required rules -> a syntactically clean pair of fragments is clean.
    (tmp_path / "a.fragment").write_text("CONFIG_A=y\n", encoding="utf-8")
    (tmp_path / "b.fragment").write_text("CONFIG_B=y\n", encoding="utf-8")
    report = validate_directory(tmp_path, rules=())
    assert report.ok


def test_validate_directory_ignores_non_fragment_files(tmp_path):
    (tmp_path / "a.fragment").write_text("CONFIG_A=y\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# not a fragment", encoding="utf-8")
    report = validate_directory(tmp_path, rules=())
    assert report.fragment_names == ("a.fragment",)
    assert report.ok


def test_validate_committed_library_is_clean():
    # The shipped fragment library must validate without conflicts or missing
    # required symbols (regression guard for the committed fixtures).
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[1]
    report = validate_directory(repo / "fragments")
    assert report.ok, format_report(report)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_validate_clean(capsys):
    # The shipped fragment library validates cleanly under all rules.
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[1]
    rc = main(["validate", str(repo / "fragments")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK:" in out


def test_cli_validate_reports_problems(tmp_path, capsys):
    (tmp_path / "a.fragment").write_text("CONFIG_FOO=y\n", encoding="utf-8")
    (tmp_path / "b.fragment").write_text("CONFIG_FOO=n\n", encoding="utf-8")
    rc = main(["validate", str(tmp_path)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "[CONFLICT]" in out
    assert "problem(s)" in out


def test_cli_validate_profile_filter(tmp_path, capsys):
    (tmp_path / "a.fragment").write_text("CONFIG_A=y\n", encoding="utf-8")
    rc = main(["validate", str(tmp_path), "--profile", "systemd"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "[MISSING_REQUIRED]" in out
    # Only the systemd rule ran: no cross-fragment conflict checks beyond it.
    assert "[CONFLICT]" not in out


def test_cli_validate_unknown_profile_errors(capsys):
    rc = main(["validate", "/nonexistent-xyz", "--profile", "ghost"])
    assert rc == 2
    assert "unknown profile" in capsys.readouterr().err


def test_cli_validate_not_a_directory(capsys):
    rc = main(["validate", "/nonexistent-xyz"])
    assert rc == 2
    assert "not a directory" in capsys.readouterr().err
