"""Tests for the profile validator (``pengyos_apparmor.validator``).

Covers the grammar basics the deliverable promises — header, balanced
braces, valid rule prefixes, well-formed capability/path rules — and the
over-broad-grant lint, the profile-local ``# sanctioned:`` network
sanction directives, plus the CLI ``validate`` subcommand. All checks
run on in-memory fixtures and ``tmp_path`` files: no network, no root,
no AppArmor tooling.
"""

from __future__ import annotations

from pathlib import Path

from typing import Optional

import pytest

from pengyos_apparmor import validator
from pengyos_apparmor.cli import build_parser, main

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "profiles"

# Explicit write prefixes so fixtures do not depend on the registry.
PREFIXES = ("/srv/demo",)


def make_profile(name: str = "demo", rules: str = "", aliases: str = "demo.bin",
                 header: Optional[str] = None):
    """Build profile source text: header, body rules, closing brace.

    ``header=None`` builds the conventional header; pass an explicit string
    (including ``""``) to exercise malformed headers.
    """
    if header is None:
        header = f"{name} ({aliases}) {{"
    return f"{header}\n{rules}\n}}\n"


def run(rules: str, name: str = "demo", header: Optional[str] = None):
    """Validate a fixture profile built around ``rules``."""
    return validator.validate_text(
        make_profile(name=name, rules=rules, header=header),
        name,
        write_prefixes=PREFIXES,
    )


def error_messages(report) -> list[str]:
    return [i.message for i in report.errors]


def warning_messages(report) -> list[str]:
    return [i.message for i in report.warnings]


# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------


def test_module_public_api():
    for attr in (
        "validate_text",
        "validate_profile",
        "discover_profiles",
        "write_allowed",
        "OVER_BROAD_TREE_PERMS",
        "Issue",
        "ProfileReport",
        "VALID_PERM_FLAGS",
        "WRITE_PREFIXES",
        "WRITE_EXEMPT_PATHS",
        "NON_PROFILE_FILES",
        "LOOPBACK_IPS",
        "SANCTIONED_NETWORK_RULES",
        "SANCTION_DIRECTIVE",
    ):
        assert hasattr(validator, attr), f"validator.{attr} missing"


def test_registry_matches_committed_profiles():
    # The registry covers exactly the shipped profiles; a new profile
    # without an entry must fail validation rather than pass silently.
    assert set(validator.WRITE_PREFIXES) == {"pengy-agent", "sshd"}
    assert validator.WRITE_EXEMPT_PATHS == frozenset(
        ("/dev/null", "/dev/zero", "/dev/full", "/dev/tty")
    )
    assert validator.NON_PROFILE_FILES == frozenset({"README.md"})


def test_valid_perm_flags_set():
    assert validator.VALID_PERM_FLAGS == frozenset("rwxcplkmia")


def test_over_broad_tree_perms_constant():
    # The single source of truth for the over-broad grant lint: write,
    # keepcaps, link — the flags that make a /** tree grant wide.
    assert validator.OVER_BROAD_TREE_PERMS == frozenset("wkl")


def test_sanctioned_network_rules_match_committed_grants():
    # Every network grant in the committed profiles is sanctioned (which
    # is what lets `validate --strict profiles/` pass), and the sanction
    # set holds no rule that no committed profile uses.
    granted = set()
    for path in validator.discover_profiles(PROFILES):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("network"):
                granted.add(line.rstrip(","))
    assert granted == set(validator.SANCTIONED_NETWORK_RULES), (
        f"committed network grants {sorted(granted)!r} do not match the "
        f"sanctioned set {sorted(validator.SANCTIONED_NETWORK_RULES)!r}"
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_minimal_valid_profile_has_no_issues():
    report = run("/etc/hostname r,\n/dev/null rw,")
    assert report.ok
    assert report.issues == []


def test_valid_rules_with_deny_capability_and_flags():
    report = run(
        "\n".join(
            [
                "deny /etc/shadow r,",
                "/usr/bin/demo rix,",
                "capability net_bind_service,",
                "/srv/demo rw,",
                "# a comment, not a rule",
                "",
                "/dev/tty rw,",
            ]
        )
    )
    assert report.ok
    assert report.issues == []


def test_comment_prose_does_not_trigger_lints():
    # Comments may document the invariants without tripping the checks.
    text = (
        "# strict: no complain, no unconfined, no network rules today\n"
        "demo (demo.bin) {\n"
        "# deny /etc/shadow r, would be the guardrail\n"
        "/etc/hostname r,\n"
        "}\n"
    )
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert report.ok
    assert report.issues == []


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "demo {",  # missing (alias)
        "demo (alias",  # unbalanced parens
        "demo (demo.bin)",  # missing {
        "demo\t(demo.bin) {",  # tab instead of space
        "demo (demo.bin)  {",  # double space
        "",  # empty profile
    ],
)
def test_bad_header_flagged(header):
    report = run("/etc/hostname r,", header=header)
    assert not report.ok
    assert any("header" in m or "open brace" in m for m in error_messages(report))


def test_empty_profile_reports_no_header():
    report = validator.validate_text("", "demo", write_prefixes=PREFIXES)
    assert not report.ok
    assert any("no profile header" in m for m in error_messages(report))


def test_header_name_mismatch_flagged():
    report = run("/etc/hostname r,", header="other (demo.bin) {")
    assert not report.ok
    assert any("does not match 'demo'" in m for m in error_messages(report))


def test_header_ok_but_alias_anything_named():
    # The alias (on-disk executable) is free-form within the name charset.
    report = run("/etc/hostname r,", header="demo (demo-v2.1.x86_64) {")
    assert report.ok


# ---------------------------------------------------------------------------
# Braces
# ---------------------------------------------------------------------------


def test_missing_closing_brace_flagged():
    text = "demo (demo.bin) {\n/etc/hostname r,\n"
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert not report.ok
    assert any("missing closing brace" in m for m in error_messages(report))


def test_close_before_open_flagged():
    text = "}\ndemo (demo.bin) {\n/etc/hostname r,\n}\n"
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert not report.ok
    assert any("closing brace precedes" in m for m in error_messages(report))


def test_content_after_closing_brace_flagged():
    text = "demo (demo.bin) {\n/etc/hostname r,\n}\ntrailing garbage,\n"
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert not report.ok
    assert any("must end with the closing brace" in m for m in error_messages(report))


def test_no_braces_at_all_flagged():
    report = run("", header="demo (demo.bin)")
    assert not report.ok
    assert any("no open brace" in m for m in error_messages(report))


def _count_errors(report) -> list:
    return [i for i in report.errors if "exactly one" in i.message]


@pytest.mark.parametrize(
    "extra_rule, stray_lineno",
    [
        # header '{' + one stray '{' line in the body: two opens, one close.
        ("/etc/hostname r,\n{", 3),
        # header '{' + two stray '{' lines: three opens, one close.
        ("/etc/hostname r,\n{\n{", 4),
    ],
)
def test_extra_open_brace_flagged(extra_rule, stray_lineno):
    # The positional checks (first-open / first-close / ends-with-close)
    # all pass here — only the brace *count* reveals the imbalance, and a
    # stray extra '{' is a hard parse error (real AppArmor rejects it).
    text = f"demo (demo.bin) {{\n{extra_rule}\n}}\n"
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert not report.ok
    stray = _count_errors(report)
    assert len(stray) == 1
    assert stray[0].lineno == stray_lineno


def test_extra_close_brace_flagged():
    text = "demo (demo.bin) {\n/etc/hostname r,\n}\n}\n"
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert not report.ok
    # The stray second '}' both trips the ends-with check and the count.
    assert any("must end with the closing brace" in m for m in error_messages(report))
    stray = _count_errors(report)
    assert len(stray) == 1
    assert stray[0].lineno == 4


def test_balanced_single_brace_pair_has_no_brace_error():
    # A well-formed profile has exactly one '{' (header) and one '}' (last
    # line): the count check must not false-positive on a clean profile.
    report = run("/etc/hostname r,")
    assert report.ok
    assert not _count_errors(report)


def test_braces_in_comments_are_not_counted():
    # Structural braces are counted on code lines only; a comment that
    # mentions '{' or '}' is prose and must not trip the balance check.
    text = (
        "demo (demo.bin) {\n"
        "  # opens with { and closes with } -- just prose\n"
        "  /etc/hostname r,\n"
        "}\n"
    )
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert report.ok
    assert not _count_errors(report)


# ---------------------------------------------------------------------------
# Rule prefixes and well-formedness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule",
    [
        "include /etc/apparmor.d/local",  # unknown prefix
        "allow /etc/hostname",  # 'allow' is not a prefix
        "ptrace peer",  # unknown prefix
        "r",  # bare flag
        "deny",  # bare deny
    ],
)
def test_invalid_rule_prefix_flagged(rule):
    # Every fixture is exactly two tokens, so the token-multiplicity check
    # (see test_path_rule_extra_tokens_flagged) does not preempt the
    # prefix check here: these exercise the unknown-prefix error on its
    # own.
    report = run(rule + ",")
    assert not report.ok
    assert any("invalid rule prefix" in m for m in error_messages(report))


def test_relative_path_flagged():
    report = run("etc/hostname r,")
    assert not report.ok
    assert any("absolute" in m for m in error_messages(report))


def test_missing_permission_flags_flagged():
    report = run("/etc/hostname,")
    assert not report.ok
    assert any("no permission flags" in m for m in error_messages(report))


@pytest.mark.parametrize("perms", ["rwz", "wqx", "rj"])
def test_invalid_permission_flags_flagged(perms):
    report = run(f"/etc/hostname {perms},")
    assert not report.ok
    assert any("invalid permission flags" in m for m in error_messages(report))


def test_all_valid_flag_letters_accepted():
    # /srv/demo is inside the fixture write prefixes so 'w' does not also
    # trip the write-confinement check.
    report = run(f"/srv/demo {''.join(sorted(validator.VALID_PERM_FLAGS))},")
    assert report.ok


def test_rule_without_trailing_comma_flagged():
    text = "demo (demo.bin) {\n/etc/hostname r\n}\n"
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert not report.ok
    assert any("rule must end with ','" in m for m in error_messages(report))


# ---------------------------------------------------------------------------
# Capability rules
# ---------------------------------------------------------------------------


def test_known_capability_rule_ok():
    report = run("capability net_bind_service,")
    assert report.ok
    assert report.issues == []


def test_bare_capability_grant_warned():
    report = run("capability,")
    assert report.ok  # well-formed, but the lint fires
    assert any("over-broad" in m for m in warning_messages(report))


def test_unknown_capability_warned():
    report = run("capability totally_new_cap,")
    assert report.ok
    assert any("unknown capability" in m for m in warning_messages(report))


def test_capability_with_bad_name_warned():
    report = run("capability sys-admin,")
    assert report.ok
    assert any("suspicious name" in m for m in warning_messages(report))


def test_deny_capability_warned_as_noop():
    report = run("deny capability sys_admin,")
    assert report.ok
    assert any("no-op" in m for m in warning_messages(report))


def test_all_known_capabilities_accepted():
    for cap in validator.KNOWN_CAPABILITIES:
        report = run(f"capability {cap},")
        assert report.ok, f"{cap} should be a known capability"

# ---------------------------------------------------------------------------
# Rule token multiplicity: a path rule is '<path> <flags>', a capability
# rule names exactly one capability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule",
    [
        "/etc/passwd /etc/shadow r,",          # two paths, one flag set
        "/etc/passwd r /etc/shadow r,",        # a path and two flag sets
        "/etc/passwd r r,",                    # the same flag set twice
        "/etc/passwd r x,",                    # two separate flag tokens
        "/tmp/x w /tmp/y w,",                  # two write paths
        "/etc/passwd /etc/shadow /etc/group r,",  # three paths
    ],
)
def test_path_rule_extra_tokens_flagged(rule):
    # A path rule is exactly '<path> <flags>'. Extra tokens are malformed:
    # the old parse took the FIRST path and the LAST flags and dropped the
    # middle, so `/etc/passwd /etc/shadow r,` silently validated as
    # `/etc/passwd r,` while real AppArmor rejects the line.
    report = run(rule)
    assert not report.ok, f"{rule!r} must be flagged: {report.issues}"
    assert any(
        "path rule must be exactly '<path> <flags>'" in m
        for m in error_messages(report)
    ), f"{rule!r}: {error_messages(report)}"


@pytest.mark.parametrize(
    "rule",
    [
        "deny /etc/shadow r r,",
        "deny /etc/shadow /etc/gshadow r,",
    ],
)
def test_deny_path_rule_extra_tokens_flagged(rule):
    # The 'deny' prefix does not open an extra slot: after it the rule is
    # still exactly one path plus one flag set.
    report = run(rule)
    assert not report.ok, f"{rule!r} must be flagged: {report.issues}"
    assert any(
        "path rule must be exactly '<path> <flags>'" in m
        for m in error_messages(report)
    ), f"{rule!r}: {error_messages(report)}"


@pytest.mark.parametrize(
    "rule",
    [
        "capability setuid chown,",
        "capability net_admin net_raw,",
        "capability setuid chown fowner,",
        "deny capability setuid chown,",
    ],
)
def test_capability_rule_multiple_names_flagged(rule):
    # A capability rule names exactly ONE capability: two (or more) names
    # is a parse error, not a grant of several. The earlier behaviour
    # joined the extras into one bogus name and only warned 'suspicious
    # name' — that must be a grammar error now, and the misleading
    # suspicious-name warning must not fire for the joined name.
    report = run(rule)
    assert not report.ok, f"{rule!r} must be flagged: {report.issues}"
    assert any(
        "capability rule must name exactly one capability" in m
        for m in error_messages(report)
    ), f"{rule!r}: {error_messages(report)}"
    assert not any(
        "suspicious name" in m for m in warning_messages(report)
    ), f"{rule!r}: {warning_messages(report)}"


def test_malformed_path_rule_reports_only_its_own_error():
    # The rule is malformed as a whole: the per-field checks (absolute
    # path, flag letters, write confinement) must not additionally fire
    # for the silently-taken first/last tokens — exactly one error.
    report = run("/etc/passwd w /etc/passwd w,")
    assert error_messages(report) == [
        m for m in error_messages(report)
        if "path rule must be exactly '<path> <flags>'" in m
    ]
    assert len(error_messages(report)) == 1, error_messages(report)
    report = run("/etc/passwd q /etc/passwd q,")
    assert len(error_messages(report)) == 1, error_messages(report)


def test_field_malformed_path_rule_reports_only_its_own_errors():
    # A path rule whose per-field grammar check fails (non-absolute path,
    # missing flags, or an invalid flag token) is malformed as a whole: the
    # SEMANTIC checks (whole-filesystem grant, over-broad lint, write
    # confinement) must not additionally fire. This is the same "a
    # malformed rule reports its own error only" discipline the
    # token-shape and comma-terminator checks use — before the fix, a
    # relative-path or invalid-flag write rule double-reported (its own
    # grammar error PLUS a spurious write-confinement error, reasoning about
    # the write surface of a rule AppArmor would reject outright).
    # relative path carrying a write flag (no leading '/'): only the
    # 'absolute' error, no write-confinement error
    report = run("var/lib/other w,")
    assert len(error_messages(report)) == 1, error_messages(report)
    assert any(
        "path must be absolute" in m for m in error_messages(report)
    ), error_messages(report)
    assert not any(
        "outside the declared write prefixes" in m for m in error_messages(report)
    ), error_messages(report)
    # a relative path with an invalid flag token: BOTH grammar errors fire
    # (they are independent facts about the rule), but NO write-confinement
    # error.
    report = run("var/lib/other wzz,")
    assert len(error_messages(report)) == 2, error_messages(report)
    assert any(
        "path must be absolute" in m for m in error_messages(report)
    ), error_messages(report)
    assert any(
        "invalid permission flags" in m for m in error_messages(report)
    ), error_messages(report)
    assert not any(
        "outside the declared write prefixes" in m for m in error_messages(report)
    ), error_messages(report)
    # absolute path but an invalid flag token (the token contains 'w'): only
    # the invalid-flags error, no write-confinement error for the misread
    # flag set.
    report = run("/var/lib/other rwz,")
    assert len(error_messages(report)) == 1, error_messages(report)
    assert any(
        "invalid permission flags" in m for m in error_messages(report)
    ), error_messages(report)
    assert not any(
        "outside the declared write prefixes" in m for m in error_messages(report)
    ), error_messages(report)
    # a path with no '/' at all (not even relative): the invalid-prefix
    # error only, no write-confinement error.
    report = run("relative w,")
    assert len(error_messages(report)) == 1, error_messages(report)
    assert any(
        "invalid rule prefix" in m for m in error_messages(report)
    ), error_messages(report)
    assert not any(
        "outside the declared write prefixes" in m for m in error_messages(report)
    ), error_messages(report)
    # a missing flag token (a single-token path rule): the 'no permission
    # flags' error only.
    report = run("/var/lib/other,")
    assert len(error_messages(report)) == 1, error_messages(report)
    assert any(
        "no permission flags" in m for m in error_messages(report)
    ), error_messages(report)


def test_well_formed_rule_keeps_its_semantic_checks():
    # The fix must NOT weaken the semantic checks for a WELL-FORMED rule
    # (absolute path, valid flag token): those are the documented, tested
    # behaviours. A whole-filesystem grant with a write flag still fires
    # BOTH the whole-filesystem error and the write-confinement error,
    # and a tree write outside the declared trees still fires the
    # write-confinement error alongside the over-broad warning.
    report = run("/** rw,")
    assert len(error_messages(report)) == 2, error_messages(report)
    assert any(
        "whole-filesystem" in m for m in error_messages(report)
    ), error_messages(report)
    assert any(
        "outside the declared write prefixes" in m for m in error_messages(report)
    ), error_messages(report)
    report = run("/var/lib/other/** rwkl,")
    assert any(
        "outside the declared write prefixes" in m for m in error_messages(report)
    ), error_messages(report)
    assert any(
        "over-broad" in m for m in warning_messages(report)
    ), error_messages(report)


def test_cli_validate_field_malformed_rule_reports_only_its_own_error(tmp_path, capsys):
    # End to end: a profile file whose write rule is relative (not
    # absolute) fails `validate` (exit 1) with the path error only — the
    # spurious write-confinement error must not appear in the output.
    p = tmp_path / "badpath"
    p.write_text("badpath (badpath.bin) {\nvar/lib/other w,\n}\n", encoding="utf-8")
    code, out = cli(["validate", str(p)], capsys)
    assert code == 1, out.out
    assert "path must be absolute" in out.out
    assert "outside the declared write prefixes" not in out.out
    assert "1 error(s)" in out.out


@pytest.mark.parametrize(
    "rule",
    [
        "/etc/passwd r,",
        "/etc/passwd rix,",
        "/srv/demo rwxcplkmia,",                # all valid flags, one token
        "deny /etc/shadow r,",
        "capability setuid,",
        "deny capability sys_admin,",
        "/dev/null rw,",
    ],
)
def test_well_formed_rules_do_not_trip_token_multiplicity(rule):
    # Exactly one path + one flag set (or exactly one capability name) is
    # the normal shape: the token-multiplicity check must not fire.
    report = run(rule)
    assert report.ok, f"{rule!r} must be well-formed: {report.issues}"
    assert not any(
        "must be exactly" in m or "exactly one capability" in m
        for m in error_messages(report)
    ), f"{rule!r}: {error_messages(report)}"


def test_cli_validate_flags_extra_token_rule(tmp_path, capsys):
    # End to end: a profile file whose rule carries extra tokens fails
    # `validate` (exit 1) and `--strict` alike.
    p = tmp_path / "extra"
    p.write_text("extra (extra.bin) {\n/etc/passwd /etc/shadow r,\n}\n")
    code, out = cli(["validate", str(p)], capsys)
    assert code == 1, out.out
    assert "'<path> <flags>'" in out.out
    code2, out2 = cli(["validate", "--strict", str(p)], capsys)
    assert code2 == 1, out2.out

# ---------------------------------------------------------------------------
# Comma terminator: a rule ends with exactly one ',', never more, and a
# comma may not appear inside a rule token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule",
    [
        "/etc/passwd, r,",                      # comma inside the path token
        "/tmp/x r,  ,",                         # comma left dangling in a token
        "capability, setuid,",                   # comma after the keyword
        "deny /etc/shadow, r,",                  # comma inside a deny rule
        "network inet tcp, rw,",                 # comma inside a network rule
        "network peer=(ip=127.0.0.1), rw,",      # comma after the peer blob
        # (address sets such as ip={a,b} are out of this validator's model
        # and are documented as unsupported in the error message; see the
        # brace-bearing rule lines section for the dedicated set tests)
    ],
)
def test_comma_inside_rule_flagged(rule):
    # A comma terminates a rule: one inside a rule token makes the line
    # malformed. The old check only required the LAST character to be a
    # comma, so `/etc/passwd, r,` validated clean as the path
    # `/etc/passwd,` with flag `r` — a comma-laden path no profile could
    # have intended (real AppArmor reads the comma as a terminator too).
    report = run(rule)
    assert not report.ok, f"{rule!r} must be flagged: {report.issues}"
    assert any(
        "commas terminate rules" in m for m in error_messages(report)
    ), f"{rule!r}: {error_messages(report)}"


@pytest.mark.parametrize(
    "rule",
    [
        "/etc/passwd r,,",
        "capability setuid,,",
        "deny /etc/shadow r,,",
        "network inet tcp rw,,",
    ],
)
def test_double_trailing_comma_flagged(rule):
    # A rule is terminated by ONE comma. A second trailing comma is a stray
    # terminator (it opens an empty rule in real AppArmor), not an
    # invisible character: `network inet tcp rw,,` used to validate clean.
    report = run(rule)
    assert not report.ok, f"{rule!r} must be flagged: {report.issues}"
    assert any(
        "exactly one ','" in m for m in error_messages(report)
    ), f"{rule!r}: {error_messages(report)}"


def test_comma_malformed_rule_reports_only_its_own_error():
    # A comma-laden rule is malformed as a whole: the per-field checks
    # (write confinement, socket-grant lint) must not additionally fire for
    # the misread tokens — exactly one error, no socket-grant warning.
    report = run("/etc/passwd, w,")  # would also trip write confinement
    assert len(error_messages(report)) == 1, error_messages(report)
    assert any(
        "commas terminate rules" in m for m in error_messages(report)
    ), error_messages(report)
    report = run("network inet tcp rw,,")
    assert len(error_messages(report)) == 1, error_messages(report)
    assert not any(
        "socket grant" in m for m in warning_messages(report)
    ), warning_messages(report)


@pytest.mark.parametrize(
    "rule",
    [
        "/etc/passwd r,",
        "deny /etc/shadow r,",
        "capability setuid,",
        "network inet tcp peer=(ip=127.0.0.1),",
        "network inet tcp rw,",
        "/srv/demo rw,",
    ],
)
def test_well_formed_rules_do_not_trip_comma_check(rule):
    # Exactly one terminating comma is the normal shape: the terminator
    # checks must not fire on any well-formed rule (the sanctioned
    # unbound loopback grant in particular — it feeds the port-pin
    # transition's anchors).
    report = run(rule)
    assert report.ok, f"{rule!r} must be well-formed: {report.issues}"
    assert not any(
        "commas terminate rules" in m or "exactly one ','" in m
        for m in error_messages(report)
    ), f"{rule!r}: {error_messages(report)}"


def test_cli_validate_flags_comma_inside_rule(tmp_path, capsys):
    # End to end: a profile file with a comma inside a rule token fails
    # `validate` (exit 1) and `--strict` alike.
    p = tmp_path / "comma"
    p.write_text("comma (comma.bin) {\n/etc/passwd, r,\n}\n")
    code, out = cli(["validate", str(p)], capsys)
    assert code == 1, out.out
    assert "commas terminate rules" in out.out
    code2, out2 = cli(["validate", "--strict", str(p)], capsys)
    assert code2 == 1, out2.out


# ---------------------------------------------------------------------------
# Strict posture
# ---------------------------------------------------------------------------


def test_complain_posture_flagged():
    text = "demo (demo.bin) complain {\n/etc/hostname r,\n}\n"
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert not report.ok
    assert any("'complain'" in m for m in error_messages(report))


def test_unconfined_posture_flagged():
    text = "demo (demo.bin) {\n/etc/hostname r unconfined,\n}\n"
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert not report.ok
    assert any("'unconfined'" in m for m in error_messages(report))


# ---------------------------------------------------------------------------
# Over-broad wildcard-tree grant lint
# ---------------------------------------------------------------------------


def test_over_broad_tree_write_grant_warned():
    # Writing inside a declared tree is legal (no error), but a single
    # /** rule that can write any file under the tree is warned about.
    report = run("/srv/demo/** rw,")
    assert report.ok
    assert any(
        "over-broad wildcard-tree grant" in m for m in warning_messages(report)
    )


@pytest.mark.parametrize("perms", ["rwkl", "rwk", "rk", "rl", "w", "wka"])
def test_over_broad_tree_flags_warned(perms):
    # The checklist item's literal example is `/**  rwkl,` style.
    report = run(f"/srv/demo/** {perms},")
    assert report.ok  # inside the declared tree: warning, not error
    assert any(
        "over-broad wildcard-tree grant" in m and perms in m
        for m in warning_messages(report)
    )


def test_over_broad_grant_warns_even_with_extra_spacing():
    report = run("/srv/demo/**  rwkl,")
    assert report.ok
    assert any("over-broad" in m for m in warning_messages(report))


def test_scoped_tree_read_exec_map_grants_stay_clean():
    # r/x/m on a /** tree is the intended minimal style — no lint.
    report = run(
        "/usr/lib/openssh/** r,\n"
        "/opt/agent/bin/** rx,\n"
        "/usr/lib/python3/**/*.so.* mr,\n"
    )
    assert report.ok
    assert report.issues == []


def test_non_tree_glob_write_not_warned():
    # A single-level glob without /** is not a tree grant.
    report = run("/srv/demo/sub/*.log rw,")
    assert report.ok
    assert report.issues == []


@pytest.mark.parametrize("rule", ["deny /etc/sudoers.d/** r,", "deny /root/** rw,"])
def test_deny_tree_grant_not_warned(rule):
    # Denies narrow rather than grant; the lint is for allow rules only.
    report = run(rule)
    assert report.ok
    assert report.issues == []


def test_over_broad_grant_still_fails_write_confinement():
    # The lint is a warning; a tree write outside the declared trees is
    # still a hard error (both fire together).
    report = run("/var/lib/other/** rwkl,")
    assert not report.ok
    assert any(
        "outside the declared write prefixes" in m for m in error_messages(report)
    )
    assert any("over-broad" in m for m in warning_messages(report))


def test_committed_profiles_pass_strict_validation():
    # The tightening item: every committed profile passes --strict —
    # zero errors AND zero warnings. The over-broad wildcard-tree write
    # grants are gone (narrowed to explicit paths), and the single
    # loopback network grant is sanctioned (SANCTIONED_NETWORK_RULES).
    for path in validator.discover_profiles(PROFILES):
        report = validator.validate_profile(path)
        assert report.ok
        assert report.warnings == [], (
            f"{path.name} must have no warnings for --strict:\n"
            + "\n".join(i.format(path.name) for i in report.warnings)
        )


# ---------------------------------------------------------------------------
# Write confinement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/**", "/*", "/"])
def test_whole_filesystem_allow_grant_flagged(path):
    report = run(f"{path} r,")
    assert not report.ok
    assert any("whole-filesystem" in m for m in error_messages(report))


def test_whole_filesystem_deny_allowed():
    # Deny guardrails may (and should) use /** to narrow.
    report = run("deny /root/** r,")
    assert report.ok
    assert report.issues == []


def test_write_outside_allowlist_flagged():
    report = run("/var/lib/other/db rw,")
    assert not report.ok
    assert any("outside the declared write prefixes" in m for m in error_messages(report))


def test_write_inside_allowlist_ok():
    report = run("/srv/demo rw,\n/srv/demo/** rw,")
    assert report.ok


def test_write_exempt_paths_ok():
    report = run("/dev/null rw,\n/dev/tty rw,")
    assert report.ok


def test_deny_with_write_not_confined():
    # Denies never grant anything; confinement applies to allow rules only.
    report = run("deny /var/lib/other/db rw,")
    assert report.ok


def test_write_requires_declared_prefixes(tmp_path):
    # A profile with no entry in WRITE_PREFIXES and no explicit prefixes:
    # any write grant fails, and the missing entry itself is warned.
    path = tmp_path / "brand-new"
    path.write_text("brand-new (brand-new.bin) {\n/tmp/scratch rw,\n}\n", encoding="utf-8")
    report = validator.validate_profile(path)
    assert not report.ok
    assert any("outside the declared write prefixes" in m for m in error_messages(report))
    assert any("no declared write prefixes" in m for m in warning_messages(report))
    # ...and it passes once the caller declares the write tree explicitly.
    ok = validator.validate_profile(path, write_prefixes=("/tmp/scratch",))
    assert ok.ok and ok.issues == []


# ---------------------------------------------------------------------------
# Network rules: warning for base profiles, well-formedness still checked
# ---------------------------------------------------------------------------


def test_network_rule_warned_but_ok():
    # Well-formed AppArmor network syntax: network [type [protocol]] <flags>.
    report = run("network inet stream rw,\n")
    assert report.ok
    assert any("network rule: intentional socket grant" in m for m in warning_messages(report))


def test_network_rule_without_flags_flagged():
    report = run("network,")
    assert not report.ok
    assert any("network rule has no permission flags" in m for m in error_messages(report))


def test_network_rule_with_bad_flags_flagged():
    report = run("network inet stream rq,")
    assert not report.ok
    assert any("invalid token 'rq'" in m for m in error_messages(report))


def test_network_loopback_grant_sanctioned_clean():
    # The pengy-agent's committed grant: loopback-scoped, pure client,
    # and sanctioned — so it carries NO warnings at all (which is what
    # lets `validate --strict profiles/` pass).
    rule = "network inet tcp peer=(ip=127.0.0.1)"
    assert rule in validator.SANCTIONED_NETWORK_RULES
    report = run(rule + ",")
    assert report.ok
    assert report.warnings == []


@pytest.mark.parametrize(
    "rule",
    [
        "network inet tcp peer=(ip=127.0.0.1 port=8080),",  # port added
        "network inet tcp peer=(ip=::1),",  # different loopback IP
        "network inet udp peer=(ip=127.0.0.1),",  # different type
        "network inet6 tcp peer=(ip=127.0.0.1),",  # different family
    ],
)
def test_network_sanction_requires_exact_rule(rule):
    # Any deviation from a sanctioned rule is NOT sanctioned: the
    # posture warnings come back (a sanction is per exact rule, like a
    # write prefix is per declared path).
    report = run(rule)
    if report.errors:
        return  # the deviant rule may be unparseable — still not clean
    assert report.ok
    warns = warning_messages(report)
    assert any("intentional socket grant" in m for m in warns), (
        f"{rule!r} must not be treated as sanctioned: {warns}"
    )


def test_deny_sanctioned_rule_still_warned_as_noop():
    # Sanction applies to allow rules only; a deny of a sanctioned rule
    # is still a no-op lint.
    report = run("deny network inet tcp peer=(ip=127.0.0.1),")
    assert report.ok
    warns = warning_messages(report)
    assert any("no-op" in m for m in warns)

# ---------------------------------------------------------------------------
# Port-pin transition guard
#
# The follow-up "pin the local model server's port" is
# blocked until PengyOS defines the model server's default port (no
# component defines one yet; the port must not be guessed). These tests
# pin the *shape* of that future change so it stays a mechanical, loud
# edit: the pinned port form must be grammar-valid, and it must NOT be
# treated as sanctioned (the posture warnings come back) until
# SANCTIONED_NETWORK_RULES is updated in the same change.
# ---------------------------------------------------------------------------


def _model_server_rule(port: str = "") -> str:
    """The pengy-agent's model-server grant, optionally port-pinned."""
    peer = "ip=127.0.0.1" + (f" port={port}" if port else "")
    return f"network inet tcp peer=({peer})"


def test_committed_grant_is_the_unbound_port_form():
    # Anchor for the pin: until the port is defined, the sanctioned
    # grant carries no port= conditional. When it is pinned, this
    # assertion must be updated deliberately — that edit is the visible,
    # reviewable part of the transition.
    rule = _model_server_rule()
    assert rule in validator.SANCTIONED_NETWORK_RULES
    assert "port=" not in rule


def test_port_pinned_grant_is_grammar_valid():
    # The future port-pinned form parses cleanly for any well-formed port
    # (and range) literal: zero errors, and the loopback scope is still
    # recognized (no scope warning).
    for port in ("11434", "4321-4330"):
        report = run(_model_server_rule(port) + ",")
        assert report.ok, f"port={port}: {report.errors}"
        warns = warning_messages(report)
        assert not any("malformed" in m for m in warns)
        assert not any("not loopback-scoped" in m for m in warns)


def test_port_pinned_grant_is_not_sanctioned_yet():
    # A sanction is per exact rule: adding port=N to the sanctioned grant
    # is a deviation, so the posture warnings come back until the
    # sanction set is updated alongside the profile. This is the
    # deliberate fail-loud behaviour of the port-pin transition — a
    # profile pinned without updating SANCTIONED_NETWORK_RULES cannot
    # pass `validate --strict`.
    for port in ("11434", "4321-4330"):
        rule = _model_server_rule(port)
        assert rule not in validator.SANCTIONED_NETWORK_RULES
        report = run(rule + ",")
        assert report.ok
        warns = warning_messages(report)
        assert any("intentional socket grant" in m for m in warns), (
            f"{rule!r} must not be sanctioned before the port is defined: "
            f"{warns}"
        )


def test_port_pin_transition_is_strict_failing_end_to_end(tmp_path, capsys):
    # End to end: a profile whose grant is port-pinned but whose sanction
    # set was not updated validates with zero errors yet fails
    # `--strict` — the exact state the transition must be reviewed in.
    p = tmp_path / "pengy-agent"
    p.write_text(
        "pengy-agent (pengy-agent.bin) {\n"
        + _model_server_rule("11434")
        + ",\n}\n",
        encoding="utf-8",
    )
    code, out = cli(["validate", str(p)], capsys)
    assert code == 0
    assert "0 error(s)" in out.out
    # Two posture warnings: implied permissions (no explicit access list)
    # + the intentional-socket-grant lint the unsanctioned deviation
    # re-trips.
    assert "2 warning(s)" in out.out
    code, _ = cli(["validate", "--strict", str(p)], capsys)
    assert code == 1


# ---------------------------------------------------------------------------
# Profile-local network sanctions (# sanctioned: <rule>)
#
# A profile can answer for a specific network grant in its own file with
# a ``# sanctioned: <rule>`` comment line. The rule must be an exact
# network rule the profile carries (typos are errors), it applies to
# allow rules only, and it silences the posture warnings for that one
# exact rule — in addition to the package-level SANCTIONED_NETWORK_RULES.
# This is the intended path for the pending model-server port pin: the
# pinned rule plus its local sanction makes the transition a
# single-file, strict-clean edit (see the end-to-end test below).
# ---------------------------------------------------------------------------


def test_sanction_directive_prefix_constant():
    assert validator.SANCTION_DIRECTIVE == "# sanctioned: "


def test_local_sanction_silences_posture_warnings():
    # The port-pinned grant, sanctioned in the profile itself: zero
    # issues at all (strict-clean).
    text = (
        "demo (demo.bin) {\n"
        "  # sanctioned: network inet tcp peer=(ip=127.0.0.1 port=11434)\n"
        "  network inet tcp peer=(ip=127.0.0.1 port=11434),\n"
        "}\n"
    )
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert report.ok
    assert report.issues == []


def test_local_sanction_requires_exact_match():
    # A sanction that deviates from the carried rule in any way (port,
    # IP, type, family) matches nothing: a hard error, and the carried
    # rule keeps its posture warnings (it is not sanctioned).
    rule = "network inet tcp peer=(ip=127.0.0.1 port=11434)"
    for variant in (
        "network inet tcp peer=(ip=127.0.0.1 port=9999)",
        "network inet tcp peer=(ip=::1 port=11434)",
        "network inet udp peer=(ip=127.0.0.1 port=11434)",
        "network inet6 tcp peer=(ip=127.0.0.1 port=11434)",
    ):
        text = (
            "demo (demo.bin) {\n"
            f"  # sanctioned: {variant}\n"
            f"  {rule},\n"
            "}\n"
        )
        report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
        assert not report.ok, f"{variant!r} must not be accepted as a sanction"
        assert any(
            "matches no network rule" in m for m in error_messages(report)
        ), f"{variant!r}: {report.issues}"
        # The carried, unsanctioned rule still gets its posture warnings.
        assert any("intentional socket grant" in m for m in warning_messages(report))


def test_local_sanction_of_absent_rule_flagged():
    # Sanctioning a rule the profile does not carry is a typo: error.
    text = (
        "demo (demo.bin) {\n"
        "  # sanctioned: network inet tcp peer=(ip=10.0.0.5)\n"
        "  network inet tcp peer=(ip=127.0.0.1 port=11434),\n"
        "}\n"
    )
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert not report.ok
    assert any("matches no network rule" in m for m in error_messages(report))


def test_local_sanction_empty_directive_flagged():
    text = (
        "demo (demo.bin) {\n"
        "  # sanctioned: \n"
        "  network inet tcp peer=(ip=127.0.0.1 port=11434),\n"
        "}\n"
    )
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert not report.ok
    assert any("empty local sanction" in m for m in error_messages(report))


@pytest.mark.parametrize(
    "directive",
    [
        "# sanctioned: /etc/passwd r",  # a path rule, not a network rule
        "# sanctioned: network",  # bare keyword, no rule
        "# sanctioned: capability sys_admin",  # a capability rule
    ],
)
def test_local_sanction_non_network_rule_flagged(directive):
    text = f"demo (demo.bin) {{\n  {directive}\n}}\n"
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert not report.ok
    assert any(
        "must name a network rule" in m for m in error_messages(report)
    ), f"{directive!r}: {report.issues}"


def test_local_sanction_with_trailing_comma_matches():
    # Body-consistent form: the directive may carry the rule's trailing
    # comma; it is stripped before the verbatim match.
    text = (
        "demo (demo.bin) {\n"
        "  # sanctioned: network inet tcp peer=(ip=127.0.0.1 port=11434),\n"
        "  network inet tcp peer=(ip=127.0.0.1 port=11434),\n"
        "}\n"
    )
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert report.ok
    assert report.issues == []


def test_local_sanction_is_inert_for_deny_rules():
    # A sanction applies to allow rules only. Sanctioning the string of
    # a deny network rule matches (the rule is carried) but the no-op
    # deny warning still fires.
    text = (
        "demo (demo.bin) {\n"
        "  # sanctioned: deny network inet tcp rw\n"
        "  deny network inet tcp rw,\n"
        "}\n"
    )
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert report.ok
    assert any("no-op" in m for m in warning_messages(report))


def test_local_sanction_only_silences_its_exact_rule():
    # Two network rules: the locally sanctioned one is clean, the other
    # keeps its posture warnings.
    text = (
        "demo (demo.bin) {\n"
        "  # sanctioned: network inet tcp peer=(ip=127.0.0.1 port=11434)\n"
        "  network inet tcp peer=(ip=127.0.0.1 port=11434),\n"
        "  network inet udp peer=(ip=127.0.0.1 port=5353),\n"
        "}\n"
    )
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert report.ok
    warns = warning_messages(report)
    assert sum("intentional socket grant" in m for m in warns) == 1


def test_local_sanction_ignores_plain_prose():
    # Prose that merely mentions the word 'sanctioned' is a comment, not
    # a directive: no directive error, and the carried rule keeps its
    # posture warnings (nothing was sanctioned).
    text = (
        "demo (demo.bin) {\n"
        "  # this grant is NOT sanctioned: see the spec\n"
        "  network inet tcp peer=(ip=127.0.0.1 port=11434),\n"
        "}\n"
    )
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert report.ok
    assert report.errors == []
    assert any("intentional socket grant" in m for m in warning_messages(report))


def test_local_sanction_of_globally_sanctioned_rule_is_harmless():
    # Duplicating a package-level sanction locally is a no-op set union.
    text = (
        "demo (demo.bin) {\n"
        "  # sanctioned: network inet tcp peer=(ip=127.0.0.1)\n"
        "  network inet tcp peer=(ip=127.0.0.1),\n"
        "}\n"
    )
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert report.ok
    assert report.issues == []


def test_port_pin_with_local_sanction_is_strict_clean_end_to_end(tmp_path, capsys):
    # The intended state of the port-pin transition, end to end: the
    # port-pinned grant plus its profile-local sanction is a
    # single-file edit and passes `validate --strict` (0 errors, 0
    # warnings). No SANCTIONED_NETWORK_RULES code edit is needed for the
    # profile to be strict-clean.
    p = tmp_path / "pengy-agent"
    p.write_text(
        "pengy-agent (pengy-agent.bin) {\n"
        "  # sanctioned: network inet tcp peer=(ip=127.0.0.1 port=11434)\n"
        "  network inet tcp peer=(ip=127.0.0.1 port=11434),\n"
        "}\n",
        encoding="utf-8",
    )
    code, out = cli(["validate", "--strict", str(p)], capsys)
    assert code == 0, out.out
    assert "0 error(s)" in out.out
    assert "0 warning(s)" in out.out
    assert "pengy-agent: OK" in out.out


def test_network_ipv6_and_none_loopback_scoped():
    for rule in ("network inet6 tcp peer=(ip=::1),", "network inet tcp peer=(ip=none),"):
        report = run(rule)
        assert report.ok
        assert not any("not loopback-scoped" in m for m in warning_messages(report))


def test_network_peer_blob_with_spaces():
    # apparmor.d(5) writes peer conditionals with spaces inside the
    # parentheses: peer=(ip=10.139.15.23 port=8081).
    report = run("network inet tcp peer=(ip=127.0.0.1 port=8080),")
    assert report.ok
    warns = warning_messages(report)
    assert not any("not loopback-scoped" in m for m in warns)
    assert any("intentional socket grant" in m for m in warns)


def test_network_non_loopback_ip_warned():
    report = run("network inet tcp peer=(ip=10.0.0.5 port=8080),")
    assert report.ok
    warns = warning_messages(report)
    assert any("not loopback-scoped" in m for m in warns)
    assert any("network rule: intentional socket grant" in m for m in warns)


def test_network_explicit_access_keywords_no_implied_warning():
    # Explicit socket operations: no implied-permissions lint.
    report = run("network connect send receive inet tcp peer=(ip=127.0.0.1),")
    assert report.ok
    warns = warning_messages(report)
    assert not any("implied permissions" in m for m in warns)
    assert any("intentional socket grant" in m for m in warns)


def test_network_local_ip_port_conditionals_ok():
    # Local (bind/listen) conditionals without a peer: well-formed.
    report = run("network bind listen inet tcp ip=127.0.0.1 port=8080-8084,")
    assert report.ok
    warns = warning_messages(report)
    assert not any("implied permissions" in m for m in warns)
    assert not any("not loopback-scoped" in m for m in warns)


@pytest.mark.parametrize(
    "rule",
    [
        "network inet tcp peer=(ip=999.1.1.1),",  # IPv4 octet > 255
        "network inet tcp peer=(ip=not-an-ip),",  # not an address at all
        "network ip=300.1.1.1,",  # local ip, octet > 255
        "network ip=1.2.3.4 port=9999999,",  # port out of range
        "network inet tcp peer=(port=abc),",  # malformed port
        "network inet tcp peer=(proto=tcp),",  # unknown peer conditional
        "network inet tcp peer=ip=127.0.0.1,",  # peer= without parentheses
        "network inet tcp peer=(),",  # empty peer conditional
        "network ib tcp rw,",  # unknown socket family
        "network inet sctp rw,",  # unknown type/protocol
    ],
)
def test_network_malformed_conditionals_flagged(rule):
    report = run(rule)
    assert not report.ok, f"{rule!r} must be flagged: {report.issues}"
    assert error_messages(report)


@pytest.mark.parametrize(
    "rule",
    [
        "network inet inet rw,",       # the same domain twice
        "network inet inet6 rw,",      # two different socket families
        "network inet unix rw,",       # v4 + unix — two domains
        "network tcp udp rw,",         # two protocols
        "network inet tcp udp rw,",    # a domain plus two types
        "network inet tcp tcp rw,",    # the same type twice
    ],
)
def test_network_rule_duplicate_slot_flagged(rule):
    # apparmor.d(5) gives a network rule at most ONE domain and at most ONE
    # type/protocol. A second of either is malformed even though every token
    # on its own is a legal one — the per-token shape check alone misses it.
    report = run(rule)
    assert not report.ok, f"{rule!r} must be flagged: {report.issues}"
    assert any(
        "more than one domain" in m or "more than one type/protocol" in m
        for m in error_messages(report)
    ), f"{rule!r}: {error_messages(report)}"


@pytest.mark.parametrize(
    "rule",
    [
        "network inet tcp peer=(ip=127.0.0.1) peer=(port=80) rw,",
        "network inet tcp peer=(ip=127.0.0.1) peer=(ip=127.0.0.1) rw,",
    ],
)
def test_network_rule_duplicate_peer_flagged(rule):
    # A rule carries at most one peer=(…) conditional: two peer blobs is
    # malformed even when each blob is individually well-formed.
    report = run(rule)
    assert not report.ok, f"{rule!r} must be flagged: {report.issues}"
    assert any(
        "more than one peer" in m for m in error_messages(report)
    ), f"{rule!r}: {error_messages(report)}"


@pytest.mark.parametrize(
    "rule",
    [
        "network inet tcp rw,",                                   # domain + type
        "network inet tcp peer=(ip=127.0.0.1) rw,",               # + one peer
        "network tcp rw,",                                        # type only
        "network rw,",                                            # coarse form
        "network bind listen inet tcp ip=127.0.0.1 port=8080,",   # local slots
    ],
)
def test_network_rule_single_slots_are_well_formed(rule):
    # Each grammar slot appearing exactly once is the normal, well-formed
    # shape: the multiplicity check must not fire (report is ok — posture
    # warnings are allowed, but there must be no grammar error).
    report = run(rule)
    assert report.ok, f"{rule!r} must be well-formed: {report.issues}"
    assert not any(
        "more than one" in m for m in error_messages(report)
    ), f"{rule!r}: {error_messages(report)}"

@pytest.mark.parametrize(
    "port",
    [
        "0",        # below the window
        "99999",    # five digits but > 65535 (the shape check alone misses it)
        "65536",    # one past the top
        "2000-1000",  # inverted range
        "0-5",      # range touching the bottom (endpoint < 1)
        "60000-70000",  # range whose top is out of window
    ],
)
@pytest.mark.parametrize(
    "placement",
    ["peer", "local"],
)
def test_network_out_of_range_port_flagged(port: str, placement: str):
    # A port that matches the shape (1-5 digits, optional dash) but is not a
    # real TCP port must be a grammar error — not just a shape failure.
    if placement == "peer":
        rule = f"network inet tcp peer=(ip=127.0.0.1 port={port}),"
    else:
        rule = f"network bind listen inet tcp ip=127.0.0.1 port={port},"
    report = run(rule)
    assert not report.ok, f"{rule!r} must be flagged: {report.issues}"
    assert any("malformed port" in m for m in error_messages(report)), (
        f"{rule!r}: expected a malformed-port error, got {error_messages(report)}"
    )


@pytest.mark.parametrize(
    "port",
    [
        "1",        # bottom of the window
        "65535",    # top of the window
        "8080",     # a normal port
        "8080-9000",  # well-ordered in-range range
        "8080-8080",  # degenerate single-port range
        "1-65535",  # the whole window
    ],
)
@pytest.mark.parametrize(
    "placement",
    ["peer", "local"],
)
def test_network_in_range_port_is_well_formed(port: str, placement: str):
    # The valid window (and well-ordered in-range ranges) must pass the
    # port grammar — flagged for posture only, never for a malformed port.
    if placement == "peer":
        rule = f"network inet tcp peer=(ip=127.0.0.1 port={port}),"
    else:
        rule = f"network bind listen inet tcp ip=127.0.0.1 port={port},"
    report = run(rule)
    assert report.ok, f"{rule!r} must be well-formed: {report.issues}"
    assert not any("malformed port" in m for m in error_messages(report))


def test_valid_port_window_matches_normalize_port():
    # The validator and the port-pin planner must agree on the port window:
    # every port normalize_port accepts, the validator must call well-formed,
    # and vice versa — otherwise a pin the planner emits could be rejected
    # by the profile it lands in (or a rejected pin could validate clean).
    from pengyos_apparmor import portpin

    accepted_by_validator = [p for p in (0, 1, 2, 8080, 65534, 65535, 65536) 
                             if validator._valid_port(str(p))]
    accepted_by_planner: list[int] = []
    for p in (0, 1, 2, 8080, 65534, 65535, 65536):
        try:
            portpin.normalize_port(p)
            accepted_by_planner.append(p)
        except Exception:
            pass
    assert accepted_by_validator == accepted_by_planner == [1, 2, 8080, 65534, 65535]


def test_deny_network_rule_warned_as_noop():
    # Denies grant nothing under default-deny: a no-op lint, not a grant.
    report = run("deny network inet tcp rw,")
    assert report.ok
    warns = warning_messages(report)
    assert any("no-op" in m for m in warns)
    assert not any("intentional socket grant" in m for m in warns)


# ---------------------------------------------------------------------------
# Brace-bearing rule lines: the body excludes the header and the counted
# brace lines by line IDENTITY, not by substring, so a rule line that
# itself carries literal '{' or '}' is still rule-validated. The old
# filter dropped ANY line containing a brace, so such a rule was silently
# never checked (a write grant on a brace path could sit outside the
# declared write trees and validate clean). ip=/port= address sets
# (ip={a,b}) are a documented grammar error: one rule per address/port.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule",
    [
        "network inet tcp peer=(ip={127.0.0.1,10.0.0.1}) rw,",  # peer set
        "network inet tcp peer=(ip={127.0.0.1, 10.0.0.1}) rw,",  # set w/ spaces
        "network ip={127.0.0.1, 10.0.0.1} rw,",  # local set (AGENTS example)
    ],
)
def test_ip_address_set_multi_member_flagged(rule):
    # A multi-member set necessarily carries a comma, and a comma is a
    # rule terminator: the comma error fires (its own error only), and the
    # message names address sets as unsupported. Before the identity-based
    # body filter, every one of these lines was silently skipped.
    report = run(rule)
    assert not report.ok, f"{rule!r} must be flagged: {report.issues}"
    errs = error_messages(report)
    assert len(errs) == 1, f"{rule!r}: {errs}"
    assert any(
        "commas terminate rules" in m and "ip={a,b}" in m for m in errs
    ), f"{rule!r}: {errs}"


@pytest.mark.parametrize(
    "rule",
    [
        "network inet tcp peer=(ip={127.0.0.1}),",  # single member, peer
        "network inet tcp ip={127.0.0.1} rw,",      # single member, local
        "network inet tcp peer=(ip={}),",           # empty set
    ],
)
def test_ip_address_set_without_comma_flagged(rule):
    # A set with no comma inside (single member or empty) gets the
    # dedicated address-set grammar error instead of a malformed-ip one —
    # and no posture warnings fire for a rule that already carries an
    # error (errors gate the lint block).
    report = run(rule)
    assert not report.ok, f"{rule!r} must be flagged: {report.issues}"
    errs = error_messages(report)
    assert any(
        "unsupported address set" in m for m in errs
    ), f"{rule!r}: {errs}"
    assert not any(
        "socket grant" in m or "implied permissions" in m
        for m in warning_messages(report)
    ), f"{rule!r}: {report.issues}"


@pytest.mark.parametrize(
    "rule",
    [
        "network inet tcp peer=(port={80}),",  # port set, peer
        "network bind listen inet tcp port={80},",  # port set, local
    ],
)
def test_port_set_flagged(rule):
    # The port= conditional takes port=N or port=N-M, never a set.
    report = run(rule)
    assert not report.ok, f"{rule!r} must be flagged: {report.issues}"
    assert any(
        "unsupported port set" in m for m in error_messages(report)
    ), f"{rule!r}: {error_messages(report)}"


def test_brace_bearing_path_write_is_now_confined():
    # The exact hole the substring filter left: a write grant on a
    # brace-bearing path OUTSIDE the declared write trees used to validate
    # clean (the line was silently skipped). It is rule-validated now.
    report = run("/var/lib/other/{x} w,")
    assert not report.ok, report.issues
    assert any(
        "outside the declared write prefixes" in m
        for m in error_messages(report)
    ), error_messages(report)


def test_brace_bearing_perm_token_is_now_checked():
    # A flag-set token carrying braces is not a valid flag set: the
    # per-field check fires for a brace line now too.
    report = run("/etc/hostname r{q},")
    assert not report.ok, report.issues
    assert any(
        "invalid permission flags" in m for m in error_messages(report)
    ), error_messages(report)


def test_brace_bearing_rule_line_does_not_break_brace_counts():
    # A rule line carrying '{' mid-line is not a structural brace (it does
    # not end in one): the one-open/one-close count check must not fire.
    report = run("network inet tcp peer=(ip={127.0.0.1}),")
    assert "exactly one" not in " ".join(error_messages(report))
    assert len(error_messages(report)) == 1, error_messages(report)


@pytest.mark.parametrize(
    "text, stray_lineno",
    [
        # header '{' + one stray '{' line: two opens, one close.
        ("demo (demo.bin) {\n/etc/hostname r,\n{\n}\n", 3),
        # the counted closing brace + a second '}' after it.
        ("demo (demo.bin) {\n/etc/hostname r,\n}\n}\n", 4),
    ],
)
def test_stray_brace_lines_report_their_own_error_only(text, stray_lineno):
    # Stray structural braces are garbage the count check (plus the
    # ends-with check) already flags; they must NOT be rule-validated a
    # second time (no 'rule must end with ','' noise on the same line).
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert not report.ok
    count = [i for i in report.errors if "exactly one" in i.message]
    assert len(count) == 1 and count[0].lineno == stray_lineno
    assert not any(
        "must end with ','" in i.message and i.lineno == stray_lineno
        for i in report.errors
    ), f"stray brace line must not be rule-validated: {report.issues}"


def test_malformed_header_line_excluded_by_identity_not_substring():
    # The header is excluded from the body by line identity (first
    # non-comment line), not by brace presence: a malformed header that
    # carries no '{' does not get rule-validated, while a brace-bearing
    # rule line below it does.
    text = (
        "demo (demo.bin)\n"
        "network inet tcp peer=(ip={127.0.0.1,10.0.0.1}) rw,\n"
        "}\n"
    )
    report = validator.validate_text(text, "demo", write_prefixes=PREFIXES)
    assert not report.ok
    msgs = [(i.lineno, i.message) for i in report.errors]
    assert any(
        ln == 1 and "bad profile header line" in m for ln, m in msgs
    ), msgs
    assert any(
        ln == 2 and "commas terminate rules" in m for ln, m in msgs
    ), msgs
    assert any("no open brace" in m for _ln, m in msgs), msgs


def test_brace_in_path_is_a_literal_glob_character():
    # AppArmor path globs use '*'/'?'/'[..]'; '{' is a literal character
    # in a path. A brace-bearing path rule that is otherwise well-formed
    # (absolute, inside a declared tree, valid flags) stays clean — the
    # identity-based exclusion must not over-fire on brace paths.
    report = run("/srv/demo/{a} r,")
    assert report.ok, f"brace path must be clean: {report.issues}"
    assert report.issues == []


def test_cli_validate_flags_address_set(tmp_path, capsys):
    # End to end: a profile file with an ip= address set fails `validate`
    # (exit 1) and `--strict` alike — multi-member via the comma rule, the
    # comma-free single-member form via the dedicated set error.
    p = tmp_path / "setprofile"
    p.write_text(
        "setprofile (setprofile.bin) {\n"
        "network inet tcp peer=(ip={127.0.0.1,10.0.0.1}) rw,\n"
        "}\n",
        encoding="utf-8",
    )
    code, out = cli(["validate", str(p)], capsys)
    assert code == 1, out.out
    assert "commas terminate rules" in out.out
    code2, out2 = cli(["validate", "--strict", str(p)], capsys)
    assert code2 == 1, out2.out
    p.write_text(
        "setprofile (setprofile.bin) {\n"
        "network inet tcp peer=(ip={127.0.0.1}),\n"
        "}\n",
        encoding="utf-8",
    )
    code3, out3 = cli(["validate", str(p)], capsys)
    assert code3 == 1, out3.out
    assert "unsupported address set" in out3.out


# ---------------------------------------------------------------------------
# File-level validation & discovery
# ---------------------------------------------------------------------------


def test_validate_profile_name_matches_filename(tmp_path):
    path = tmp_path / "demo"
    path.write_text("wrong (demo.bin) {\n/etc/hostname r,\n}\n", encoding="utf-8")
    report = validator.validate_profile(path, write_prefixes=PREFIXES)
    assert not report.ok
    assert any("does not match 'demo'" in m for m in error_messages(report))


def test_validate_profile_clean(tmp_path):
    path = tmp_path / "demo"
    path.write_text("demo (demo.bin) {\n/etc/hostname r,\n}\n", encoding="utf-8")
    report = validator.validate_profile(path, write_prefixes=PREFIXES)
    assert report.ok and report.issues == []


def test_discover_profiles_excludes_readme(tmp_path):
    (tmp_path / "README.md").write_text("# docs\n", encoding="utf-8")
    (tmp_path / "a-profile").write_text("a-profile (x) {\n/a r,\n}\n", encoding="utf-8")
    (tmp_path / "b-profile").write_text("b-profile (x) {\n/b r,\n}\n", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    names = [p.name for p in validator.discover_profiles(tmp_path)]
    assert names == ["a-profile", "b-profile"]


def test_discover_committed_profiles_directory():
    names = [p.name for p in validator.discover_profiles(PROFILES)]
    assert "pengy-agent" in names
    assert "sshd" in names
    assert "README.md" not in names


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cli(args, capsys):
    code = main(args)
    return code, capsys.readouterr()


def test_cli_validate_clean_profile(tmp_path, capsys):
    p = tmp_path / "demo"
    p.write_text("demo (demo.bin) {\n/etc/hostname r,\n}\n", encoding="utf-8")
    code, out = cli(["validate", str(p)], capsys)
    # No write grants, so zero errors; the unregistered name raises the
    # "declare your write tree" warning instead.
    assert code == 0
    assert "0 error(s)" in out.out
    assert "no declared write prefixes" in out.out
    assert "warning(s)" in out.out


def test_cli_validate_error_profile(tmp_path, capsys):
    p = tmp_path / "demo"
    p.write_text("demo (demo.bin) {\n/** rw,\n}\n", encoding="utf-8")
    code, out = cli(["validate", str(p)], capsys)
    assert code == 1
    assert "error: whole-filesystem allow grant" in out.out
    assert "error: write grant outside the declared write prefixes" in out.out
    assert "2 error(s)" in out.out


def test_cli_validate_directory_and_strict(tmp_path, capsys):
    (tmp_path / "clean").write_text("clean (x) {\n/a r,\n}\n", encoding="utf-8")
    (tmp_path / "noisy").write_text(
        "noisy (x) {\nnetwork inet stream rw,\n}\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# not a profile\n", encoding="utf-8")
    code, out = cli(["validate", str(tmp_path)], capsys)
    assert code == 0
    assert "2 profile(s)" in out.out
    # Warnings: two unregistered names + one network rule; zero errors.
    assert "0 error(s)" in out.out
    assert "3 warning(s)" in out.out
    # --strict fails on warnings.
    code, _ = cli(["validate", "--strict", str(tmp_path)], capsys)
    assert code == 1


def test_cli_strict_fails_on_over_broad_warning(tmp_path, capsys):
    p = tmp_path / "pengy-agent"
    p.write_text(
        "pengy-agent (pengy-agent.bin) {\n/tmp/pengy-agent/** rwkl,\n}\n",
        encoding="utf-8",
    )
    code, out = cli(["validate", str(p)], capsys)
    # The grant is inside a declared tree: one warning, zero errors.
    assert code == 0
    assert "0 error(s)" in out.out
    assert "1 warning(s)" in out.out
    code2, _ = cli(["validate", "--strict", str(p)], capsys)
    assert code2 == 1


def test_cli_validate_missing_target(capsys):
    code, err = cli(["validate", "/does/not/exist"], capsys)
    assert code == 2
    assert "no such file or directory" in err.err


def test_cli_validate_committed_profiles_zero_errors(capsys):
    code, out = cli(["validate", str(PROFILES)], capsys)
    assert code == 0
    assert "0 error(s)" in out.out


def test_cli_validate_committed_profiles_strict_passes(capsys):
    # The checklist item, end to end: the committed profiles pass
    # `validate --strict profiles/` (no warnings left to fail on).
    code, out = cli(["validate", "--strict", str(PROFILES)], capsys)
    assert code == 0, out.out
    assert "0 error(s)" in out.out
    assert "0 warning(s)" in out.out
    assert "pengy-agent: OK" in out.out
    assert "sshd: OK" in out.out


def test_cli_help_lists_validate(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "validate" in out


def test_cli_no_subcommand_still_returns_zero(capsys):
    # Bare `pengyos-apparmor` prints help (scaffold behaviour).
    assert main([]) == 0


def test_parser_has_no_stub_commands():
    import argparse

    parser = build_parser()
    # Only the implemented subcommands are advertised.
    sub = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    assert set(sub.choices) == {
        "validate",
        "plan-port-pin",
        "find-model-server-port",
        "find-agent-workspace-layout",
        "port-pin-status",
        "apply-port-pin",
        "sync-port-pin-docs",
        "apply-port-pin",
    }
