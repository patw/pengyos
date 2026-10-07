"""Tests for the minimal, strict-by-default AppArmor profiles in ``profiles/``.

These pin the *authoring* contract of the committed profiles (the deeper
grammar validator is a separate subtask). For every profile file in
``profiles/`` (README.md excluded) they assert:

* the header line is ``<name> (alias) {`` and the profile name equals the
  file name,
* exactly one balanced brace pair (single profile per file),
* every body line is a comment, blank, or a rule ending in a comma with a
  well-formed permission-flag token,
* strict posture: no ``complain`` and no ``unconfined``,
* no ``/**`` (wildcard-everything) *allow* grants — denies may use
  them — and no wildcard-tree (``/**``) write grants at all (the
  profiles pass ``validate --strict``; writes are pinned to explicit
  paths),
* network grants are pinned exactly: ``pengy-agent`` carries exactly one
  rule — the loopback-scoped grant to the local model server — and every
  other profile carries no ``network`` rules at all,
* every writable rule stays inside a per-profile write allowlist
  (network rules are socket grants, not file writes — exempt),
* sshd never *allows* reading ``/etc/shadow`` / ``/etc/gshadow``
  (PengyOS ships key-only auth; shadow reads are a config-drift signal).

A real parse by ``aa-parser`` is opt-in behind ``PENGYOS_IT=1`` and skipped
whenever the binary is unavailable (it needs AppArmor tooling).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pengyos_apparmor import validator

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "profiles"

# Profiles the task requires (spec.md: files: profiles/pengy-agent, profiles/sshd, …).
REQUIRED_PROFILES = ("pengy-agent", "sshd")

# The authoring contract constants live in the validator package (single
# source of truth — the validator item reuses this table); re-export them
# here so this file keeps reading as the contract spec.
NON_PROFILE_FILES = set(validator.NON_PROFILE_FILES)
VALID_PERM_FLAGS = set(validator.VALID_PERM_FLAGS)
WRITE_EXEMPT_PATHS = tuple(validator.WRITE_EXEMPT_PATHS)
WRITE_PREFIXES = {k: tuple(v) for k, v in validator.WRITE_PREFIXES.items()}

NAME_RE = r"[A-Za-z0-9._-]+"
HEADER_RE = re.compile(rf"^({NAME_RE}) \(({NAME_RE})\) \{{$")


def profile_files() -> list[Path]:
    files = [
        p
        for p in sorted(PROFILES.iterdir())
        if p.is_file() and p.name not in NON_PROFILE_FILES
    ]
    return files


def rule_lines(path: Path) -> list[tuple[int, str, str]]:
    """Return (line_number, is_allow, rule_text) for every rule in the body.

    Rule text is the stripped line without its trailing comma.
    """
    out: list[tuple[int, str, str]] = []
    in_body = False
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not in_body:
            if line.endswith("{"):
                in_body = True
            continue
        if line == "}":
            break
        if not line or line.startswith("#"):
            continue
        assert line.endswith(","), f"{path.name}:{lineno}: rule must end with ','"
        out.append((lineno, not line.startswith("deny "), line[:-1]))
    return out


def rule_parts(rule: str) -> tuple[str, str]:
    if rule.startswith("deny "):
        rule = rule[len("deny "):]
    path, _, perms = rule.rpartition(" ")
    assert path, f"rule has no path: {rule!r}"
    assert perms and all(c in VALID_PERM_FLAGS for c in perms), (
        f"rule has invalid permission flags: {rule!r}"
    )
    return path, perms


def _profile_names() -> list[str]:
    return [p.name for p in profile_files()]


def test_profiles_directory_has_readme():
    assert (PROFILES / "README.md").is_file()


def test_required_profiles_exist():
    names = _profile_names()
    for required in REQUIRED_PROFILES:
        assert required in names, f"missing required profile: {required}"


def test_profile_header_name_matches_filename():
    for path in profile_files():
        first = next(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        m = HEADER_RE.match(first)
        assert m, f"{path.name}: bad profile header line: {first!r}"
        assert m.group(1) == path.name, (
            f"{path.name}: profile name {m.group(1)!r} does not match the file name"
        )


def test_single_balanced_brace_pair():
    for path in profile_files():
        text = path.read_text(encoding="utf-8")
        # Strip comments so braces inside comment prose can't fool the count.
        body = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("#")
        )
        assert body.count("{") == 1, f"{path.name}: expected exactly one open brace outside comments"
        assert body.count("}") == 1, f"{path.name}: expected exactly one close brace outside comments"
        lines = [l for l in body.splitlines() if l.strip()]
        assert lines[-1].strip() == "}", f"{path.name}: profile must end with a closing brace"


def test_body_lines_well_formed():
    for path in profile_files():
        for lineno, _is_deny, rule in rule_lines(path):
            assert "  " not in rule, f"{path.name}:{lineno}: double space in rule: {rule!r}"
            if rule.startswith("network"):
                continue  # socket grants are pinned by test_network_grants
            path_part, perms = rule_parts(rule)
            assert path_part.startswith("/"), (
                f"{path.name}:{lineno}: path must be absolute: {rule!r}"
            )
            # permissions must not be the only token
            assert len(path_part) > 1, f"{path.name}:{lineno}: path looks empty: {rule!r}"


def test_strict_posture_no_complain_or_unconfined():
    for path in profile_files():
        # Comment prose may mention the invariants it documents; only the
        # actual rules matter.
        code = " ".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ).lower()
        assert "complain" not in code, f"{path.name}: strict profiles must not use 'complain'"
        assert "unconfined" not in code, f"{path.name}: no 'unconfined' posture allowed"


def test_no_wildcard_everything_allow_grants():
    for path in profile_files():
        prefixes = WRITE_PREFIXES[path.name]
        for lineno, is_allow, rule in rule_lines(path):
            if not is_allow:
                continue  # denies narrow rather than grant
            if rule.startswith("network"):
                continue  # socket grants are pinned by test_network_grants
            path_part, perms = rule_parts(rule)
            if path_part in ("/**", "/*", "/"):
                pytest.fail(
                    f"{path.name}:{lineno}: whole-filesystem allow grant: {rule!r}"
                )
            if "/**" in path_part and "w" in perms:
                assert any(path_part.startswith(prefix) for prefix in prefixes), (
                    f"{path.name}:{lineno}: wildcard-tree write grant outside the "
                    f"declared write trees: {rule!r}"
                )


def test_network_grants():
    # The only socket access in the base profiles: pengy-agent's single
    # loopback-scoped grant to the local model server (loopback TCP, pure
    # client — a peer= conditional cannot carry bind/listen, and no other
    # IP, family, or protocol is granted). Everything else stays
    # socket-free.
    expected = {"pengy-agent": ["network inet tcp peer=(ip=127.0.0.1)"]}
    for path in profile_files():
        grants = [
            rule
            for _lineno, _is_allow, rule in rule_lines(path)
            if rule.startswith("network")
        ]
        allowed = expected.get(path.name, [])
        assert grants == allowed, (
            f"{path.name}: network grants {grants!r} do not match the pinned "
            f"set {allowed!r}"
        )
        for rule in grants:
            assert "deny" not in rule, (
                f"{path.name}: deny network rules are a no-op: {rule!r}"
            )


def test_writes_confined_to_allowlist():
    for path in profile_files():
        prefixes = WRITE_PREFIXES[path.name]
        for lineno, is_allow, rule in rule_lines(path):
            if not is_allow:
                continue  # denies never grant anything
            if rule.startswith("network"):
                continue  # socket grant, not a file write
            path_part, perms = rule_parts(rule)
            if "w" in perms:
                if path_part in WRITE_EXEMPT_PATHS:
                    continue
                assert any(path_part.startswith(prefix) for prefix in prefixes), (
                    f"{path.name}:{lineno}: write grant outside the allowlist: {rule!r}"
                )


def test_no_wildcard_tree_write_grants():
    # The tightened contract: an allow rule may never carry a write
    # (w), keepcaps (k), or link (l) flag on a wildcard-tree (/**) path.
    # This is what lets `validate --strict profiles/` pass.
    for path in profile_files():
        for lineno, is_allow, rule in rule_lines(path):
            if not is_allow:
                continue  # denies narrow rather than grant
            if rule.startswith("network"):
                continue  # socket grants are pinned by test_network_grants
            path_part, perms = rule_parts(rule)
            assert "/**" not in path_part or not set(perms) & set("wkl"), (
                f"{path.name}:{lineno}: wildcard-tree grant with write/keepcaps/"
                f"link flags: {rule!r} — narrow the path or the flags"
            )


def test_pengy_agent_writable_paths_are_explicit():
    # pengy-agent's write surface is pinned exactly: the workspace root
    # + its flat .git metadata, the flat /tmp scratch area, and its log
    # (no wildcard-tree write grant replaces these).
    expected_writes = {
        "/opt/pengy/workspaces/pengy-agent",
        "/opt/pengy/workspaces/pengy-agent/.git",
        "/opt/pengy/workspaces/pengy-agent/.git/*",
        "/tmp/pengy-agent/*",
        "/var/log/pengy/agent.log",
    }
    actual = set()
    for _lineno, is_allow, rule in rule_lines(PROFILES / "pengy-agent"):
        if not is_allow or rule.startswith("network"):
            continue
        path_part, perms = rule_parts(rule)
        if "w" in perms and path_part not in WRITE_EXEMPT_PATHS:
            actual.add(path_part)
    assert actual == expected_writes, (
        f"pengy-agent write grants {sorted(actual)!r} drifted from the "
        f"pinned set {sorted(expected_writes)!r}"
    )


def test_sshd_run_writes_are_the_pidfile():
    # sshd's only write under /run is the PidFile — no tree grant.
    for _lineno, is_allow, rule in rule_lines(PROFILES / "sshd"):
        if not is_allow or rule.startswith("network"):
            continue
        path_part, perms = rule_parts(rule)
        if path_part.startswith("/run") and "w" in perms:
            assert path_part == "/run/sshd.pid", (
                f"sshd: unexpected /run write grant: {rule!r}"
            )


def test_sshd_never_allows_shadow_reads():
    path = PROFILES / "sshd"
    for lineno, is_allow, rule in rule_lines(path):
        if not is_allow:
            continue
        assert "/etc/shadow" not in rule, f"sshd:{lineno}: allows {rule!r}"
        assert "/etc/gshadow" not in rule, f"sshd:{lineno}: allows {rule!r}"


def test_pengy_agent_denies_sensitive_sources():
    path = PROFILES / "pengy-agent"
    denies = [rule for _, is_allow, rule in rule_lines(path) if not is_allow]
    joined = " ".join(denies)
    for sensitive in ("/etc/shadow", "/etc/gshadow", "/etc/sudoers"):
        assert any(sensitive in d for d in denies), (
            f"pengy-agent: missing explicit deny guardrail for {sensitive}"
        )
    assert "/root/**" in joined, "pengy-agent: /root/** should be explicitly denied"


def test_every_profile_in_directory_is_covered_by_write_allowlist():
    # Any profile added later must declare its write prefixes here — the
    # suite fails rather than silently passing a new wide-open profile.
    for path in profile_files():
        assert path.name in WRITE_PREFIXES, (
            f"{path.name}: declare its write prefixes in WRITE_PREFIXES "
            "(and a required entry, if it is a PengyOS core component)"
        )


def test_validator_passes_committed_profiles():
    # The validator (pengyos_apparmor.validator) must agree with the
    # invariants pinned in this file: every committed profile validates
    # with zero errors.
    for path in profile_files():
        report = validator.validate_profile(path)
        assert report.ok, (
            f"{path.name} must validate cleanly:\n"
            + "\n".join(i.format(path.name) for i in report.errors)
        )


def test_integration_parse_with_aa_parser():
    """Opt-in: parse every profile with the real AppArmor parser."""
    if os.environ.get("PENGYOS_IT") != "1":
        pytest.skip("opt-in integration test (set PENGYOS_IT=1)")
    parser = shutil.which("aa-parser") or shutil.which("aa-parse")
    if parser is None:
        pytest.skip("aa-parser not installed")
    for path in profile_files():
        proc = subprocess.run(
            [parser, "-k", "--base", str(PROFILES), path.name],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, (
            f"{path.name}: aa-parser failed:\n{proc.stdout}\n{proc.stderr}"
        )
