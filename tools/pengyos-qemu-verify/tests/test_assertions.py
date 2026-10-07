"""Tests for the assertion runner.

The runner is pure (log string in, results out), so every case runs against
plain strings and committed serial-log fixtures — no QEMU, network, root,
display, or hardware. Covers:

* present checks (pass when found, fail when absent) and absent checks (pass
  when not found, fail when found);
* evidence capture (the containing line, truncated to the 512-char cap) and
  the ``None``-evidence cases (passing present with no match / passing absent);
* ``Assertion`` validation (kind, name, pattern) and the ``re.error`` on a bad
  pattern;
* empty assertion list, order preservation, ``all_passed``;
* the built-in ``default_assertions()`` against the real login and
  kernel-panic fixtures;
* the ``to_dict()`` shape that the item-7 JSON/JUnit emitters consume.
"""

import re
from pathlib import Path

import pytest

from pengyos_qemu_verify import assertions as A
from pengyos_qemu_verify.assertions import (
    FAIL_STATUS,
    KIND_ABSENT,
    KIND_PRESENT,
    PASS_STATUS,
    Assertion,
    CheckResult,
    all_passed,
    default_assertions,
    run_assertions,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ==========================================================================
# Assertion value object
# ==========================================================================


def test_assertion_defaults_to_present():
    a = Assertion("n", "pat")
    assert a.kind == KIND_PRESENT


def test_assertion_factories_set_kind():
    assert Assertion.present("n", "p").kind == KIND_PRESENT
    assert Assertion.absent("n", "p").kind == KIND_ABSENT


def test_assertion_rejects_bad_kind():
    with pytest.raises(ValueError, match="kind"):
        Assertion("n", "p", kind="maybe")


def test_assertion_rejects_empty_name_and_pattern():
    with pytest.raises(ValueError):
        Assertion("", "p")
    with pytest.raises(ValueError):
        Assertion("   ", "p")
    with pytest.raises(ValueError):
        Assertion("n", "")


# ==========================================================================
# present checks
# ==========================================================================


def test_present_passes_when_found_with_evidence():
    log = "step one\ninit started ok\nlast line\n"
    results = run_assertions(log, (Assertion.present("found", "init started"),))
    assert len(results) == 1
    r = results[0]
    assert r.name == "found"
    assert r.status == PASS_STATUS
    assert r.passed is True
    # Evidence is the *line* containing the match, not just the substring.
    assert r.evidence == "init started ok"


def test_present_fails_when_absent_no_evidence():
    results = run_assertions("just a line\n", (Assertion.present("nope", "login:"),))
    r = results[0]
    assert r.status == FAIL_STATUS
    assert r.passed is False
    assert r.evidence is None


def test_present_is_real_regex():
    log = "PengyOS Core ready\n"
    results = run_assertions(log, (Assertion.present("m", "[Pp]engyOS Core ready"),))
    assert results[0].passed is True
    assert "PengyOS Core ready" in results[0].evidence


def test_present_evidence_truncated_to_cap():
    log = ("x" * 900 + " login: " + "y" * 900) + "\n"
    results = run_assertions(log, (Assertion.present("m", "login:"),))
    r = results[0]
    assert r.passed is True
    assert r.evidence is not None
    assert len(r.evidence) <= A.MAX_EVIDENCE_CHARS
    # It is the (truncated) whole line, anchored at the line start.
    assert r.evidence.startswith("x" * 100)


def test_present_evidence_on_first_match_line():
    # Two lines match; evidence must come from the FIRST one.
    log = "login: first\nother line\nlogin: second\n"
    results = run_assertions(log, (Assertion.present("m", "login:"),))
    assert results[0].evidence == "login: first"


# ==========================================================================
# absent checks
# ==========================================================================


def test_absent_passes_when_not_found():
    results = run_assertions("all good\n", (Assertion.absent("clean", "Kernel panic"),))
    r = results[0]
    assert r.status == PASS_STATUS
    assert r.passed is True
    # No match to point at -> no evidence.
    assert r.evidence is None


def test_absent_fails_when_found_with_evidence():
    log = "booting\nKernel panic - not syncing: VFS\naftermath\n"
    results = run_assertions(log, (Assertion.absent("no-panic", "Kernel panic"),))
    r = results[0]
    assert r.status == FAIL_STATUS
    assert r.passed is False
    assert r.evidence == "Kernel panic - not syncing: VFS"


def test_absent_uses_real_regex():
    log = "oops: something bad\n"
    results = run_assertions(log, (Assertion.absent("no-oops", "[Oo]ops"),))
    assert results[0].passed is False
    assert "oops: something bad" in results[0].evidence


# ==========================================================================
# runner mechanics
# ==========================================================================


def test_run_preserves_order_and_mixed_kinds():
    log = "init started\nKernel panic - boom\n"
    checks = (
        Assertion.present("a-present", "init started"),
        Assertion.absent("b-absent-ok", "never here"),
        Assertion.present("c-present-miss", "nope"),
        Assertion.absent("d-absent-fail", "Kernel panic"),
    )
    results = run_assertions(log, checks)
    assert [r.name for r in results] == ["a-present", "b-absent-ok", "c-present-miss", "d-absent-fail"]
    assert [r.status for r in results] == [
        PASS_STATUS, PASS_STATUS, FAIL_STATUS, FAIL_STATUS,
    ]


def test_run_empty_list_yields_empty():
    assert run_assertions("anything\n", ()) == []


def test_run_accepts_a_list():
    results = run_assertions("x\n", [Assertion.present("m", "x")])
    assert results[0].passed is True


def test_run_bad_pattern_raises_re_error():
    with pytest.raises(re.error):
        run_assertions("x\n", (Assertion.present("bad", "(unclosed"),))
    # A bad pattern in an absent check raises too.
    with pytest.raises(re.error):
        run_assertions("x\n", (Assertion.absent("bad", "[oops"),))


def test_run_empty_log():
    # No present check can match; every absent check passes.
    results = run_assertions(
        "",
        (
            Assertion.present("p", "login:"),
            Assertion.absent("a", "Kernel panic"),
        ),
    )
    assert [r.status for r in results] == [FAIL_STATUS, PASS_STATUS]


def test_all_passed():
    passed = [CheckResult("a", PASS_STATUS), CheckResult("b", PASS_STATUS)]
    assert all_passed(passed) is True
    assert all_passed((CheckResult("a", PASS_STATUS), CheckResult("b", FAIL_STATUS))) is False
    # Vacuous truth: an empty result set passes.
    assert all_passed([]) is True
    assert all_passed(()) is True


def test_check_result_to_dict_shape():
    r = CheckResult("reached-login", PASS_STATUS, "pengyos login: ")
    d = r.to_dict()
    assert d == {"name": "reached-login", "status": PASS_STATUS,
                 "evidence": "pengyos login: "}
    assert CheckResult("x", FAIL_STATUS).to_dict()["evidence"] is None


# ==========================================================================
# default_assertions against the real fixtures
# ==========================================================================


def test_default_assertions_shape():
    checks = default_assertions()
    assert isinstance(checks, tuple)
    assert len(checks) >= 1
    for c in checks:
        assert isinstance(c, Assertion)
    kinds = {c.kind for c in checks}
    # The set must include at least one present and one absent check.
    assert KIND_PRESENT in kinds and KIND_ABSENT in kinds


def test_default_assertions_pass_on_login_fixture():
    log = _read_fixture("serial_login_sample.log")
    results = run_assertions(log, default_assertions())
    assert all_passed(results) is True
    assert all(r.status == PASS_STATUS for r in results)
    by_name = {r.name: r for r in results}
    # The present login check carries the "pengyos login:" line as evidence.
    assert by_name["reached-login"].evidence is not None
    assert "login:" in by_name["reached-login"].evidence


def test_default_assertions_fail_on_panic_fixture():
    log = _read_fixture("serial_kernel_panic_sample.log")
    results = run_assertions(log, default_assertions())
    assert all_passed(results) is False
    by_name = {r.name: r for r in results}
    # No login prompt was reached.
    assert by_name["reached-login"].status == FAIL_STATUS
    assert by_name["reached-login"].evidence is None
    # The panic line was detected.
    assert by_name["no-kernel-panic"].status == FAIL_STATUS
    assert by_name["no-kernel-panic"].evidence is not None
    assert "Kernel panic" in by_name["no-kernel-panic"].evidence

# --- a console line that a kernel message split -----------------------------
# systemd writes a status line in pieces (prefix, colour sequence, unit name,
# terminator), so a kernel printk can land between them. This is the shape that
# made the Core matrix's network-target check flake once on 26.10.3.

SPLIT_LINE_LOG = (
    "[    1.821811] systemd[1]: Startup finished.\n"
    "Reached target \x1b[0;1;39m\n"
    "[    3.104412] e1000e 0000:00:02.0 enp0s2: NIC Link is Up 1000 Mbps Full\n"
    " Duplex, Flow Control: Rx/Tx\n"
    "Network\x1b[0m.\n"
)
SPLIT_PATTERN = (
    r"Reached target (?:[\s\S]{0,120}?\n)?(?:\x1b\[[0-9;]*m)?"
    r"(?:[\s\S]{0,120}?\n)?Network"
)


# The same shape written with a bare dot. '.' cannot cross a newline, so the gap
# can only ever cover one console line: a kernel message that reaches over more
# than one (a wrapped or multi-line printk) breaks the match. That is why the
# shipped matrix pattern spells the gap out as [\s\S].
SINGLE_LINE_PATTERN = (
    r"Reached target (?:.{0,120}?\n)?(?:\x1b\[[0-9;]*m)?(?:.{0,120}?\n)?Network"
)


def test_a_bounded_character_class_gap_matches_a_split_console_line():
    check = Assertion.present("network-target", SPLIT_PATTERN)
    result = run_assertions(SPLIT_LINE_LOG, (check,))[0]
    assert result.status == PASS_STATUS


def test_a_bare_dot_gap_misses_the_same_split_line():
    """Why the shipped pattern spells the gap out as [\\s\\S]: '.' stops at the
    newline, so the injected kernel line breaks the match."""
    check = Assertion.present("network-target", SINGLE_LINE_PATTERN)
    result = run_assertions(SPLIT_LINE_LOG, (check,))[0]
    assert result.status == FAIL_STATUS
    assert result.evidence is None


def test_evidence_of_a_multi_line_match_spans_the_split():
    """A reader has to be able to see what got joined."""
    check = Assertion.present("network-target", SPLIT_PATTERN)
    result = run_assertions(SPLIT_LINE_LOG, (check,))[0]
    assert result.evidence.startswith("Reached target")
    assert "NIC Link is Up" in result.evidence
    assert result.evidence.rstrip().endswith("Network\x1b[0m.")
