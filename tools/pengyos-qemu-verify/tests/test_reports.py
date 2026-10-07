"""Tests for the JUnit XML + JSON emitters.

The emitters (``pengyos_qemu_verify/reports.py``) are pure string builders, so
every unit test runs against in-memory :class:`BootResult` /
:class:`CheckResult` objects and committed log fixtures — no QEMU, network,
root, display, or hardware. Coverage:

* the JSON shape — the spec's ``{image, accel, duration,
  checks:[{name,status,evidence}], result}`` keys (as a superset), round-trip
  via ``json.loads``, checks serialised in input order;
* the overall verdict (``pass`` iff boot ready AND all checks passed; timeout /
  failed check / launch error are ``fail``; empty checks are vacuously ok);
* the JUnit XML — parse it back (spec: "JUnit XML is well-formed (parse it
  back)"), the testsuite attributes, the synthetic ``boot-reached-readiness``
  testcase, one testcase per check, ``<failure>`` elements carrying the
  evidence text, XML escaping of hostile check names/evidence, and the
  pass/timeout/empty-checks shapes;
* the file writers (parent-dir creation, content matches the builders);
* ``make_error_result`` (the pre-flight failure path);
* the CLI wiring: ``boot ... --json OUT --junit OUT`` end-to-end against a
  fake ``qemu-system-x86_64`` on ``PATH`` — a successful boot writes a
  parseable pass report, a hanging guest yields a timeout report (the spec's
  "fake qemu that sleeps forever" case), and a missing image (a pre-flight
  error) still writes both reports.
"""

import json
import os
import stat
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pengyos_qemu_verify import cli
from pengyos_qemu_verify import reports
from pengyos_qemu_verify.assertions import (
    FAIL_STATUS,
    PASS_STATUS,
    CheckResult,
    default_assertions,
    run_assertions,
)
from pengyos_qemu_verify.boot import BootResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _boot(**overrides) -> BootResult:
    """A ready BootResult with sensible defaults; tests override what they need."""
    base = dict(
        image="pengyos-core-x86_64.img",
        argv=["qemu-system-x86_64", "-M", "q35"],
        kvm=True,
        ready=True,
        timed_out=False,
        returncode=0,
        duration=12.345,
        serial_log="/tmp/pengyos-verify-x/pengyos-serial.log",
        error=None,
    )
    base.update(overrides)
    return BootResult(**base)


def _checks() -> "list":
    """A mixed pass/fail check list shaped like real runner output."""
    return [
        CheckResult("reached-login", PASS_STATUS, "pengyos login: "),
        CheckResult("no-kernel-panic", FAIL_STATUS, "Kernel panic - not syncing: VFS"),
    ]


# ==========================================================================
# JSON report
# ==========================================================================


def test_json_has_spec_keys():
    d = reports.result_to_dict(_boot(), _checks())
    for key in ("image", "accel", "duration", "checks", "result"):
        assert key in d
    assert d["image"] == "pengyos-core-x86_64.img"
    assert d["accel"] == "kvm"
    assert d["duration"] == 12.345
    assert d["result"] == reports.FAIL_RESULT  # a check failed


def test_json_checks_are_spec_shape_in_order():
    d = reports.result_to_dict(_boot(), _checks())
    assert d["checks"] == [
        {"name": "reached-login", "status": PASS_STATUS, "evidence": "pengyos login: "},
        {"name": "no-kernel-panic", "status": FAIL_STATUS,
         "evidence": "Kernel panic - not syncing: VFS"},
    ]


def test_json_is_superset_of_spec_shape():
    d = reports.result_to_dict(_boot(), _checks())
    # The item-5 BootResult.to_dict() keys are carried along (superset).
    for key in ("returncode", "ready", "timed_out", "markers", "error", "serial_log"):
        assert key in d
    assert d["ready"] is True
    assert d["error"] is None


def test_json_round_trips():
    text = reports.to_json(_boot(), _checks())
    d = json.loads(text)
    assert d == reports.result_to_dict(_boot(), _checks())


def test_json_accepts_check_dicts():
    d = reports.result_to_dict(
        _boot(),
        [{"name": "a", "status": PASS_STATUS, "evidence": None}],
    )
    assert d["checks"] == [{"name": "a", "status": PASS_STATUS, "evidence": None}]
    assert d["result"] == reports.PASS_RESULT


def test_json_rejects_bad_check_items():
    with pytest.raises(TypeError, match="CheckResult"):
        reports.result_to_dict(_boot(), ["not-a-check"])


def test_json_empty_checks():
    d = reports.result_to_dict(_boot(), ())
    assert d["checks"] == []
    assert d["result"] == reports.PASS_RESULT


def test_json_checks_none_uses_checks_attached_to_boot():
    attached = [CheckResult("reached-login", PASS_STATUS, "pengyos login: ")]
    boot = _boot()
    boot.checks = attached
    d = reports.result_to_dict(boot)  # checks omitted entirely
    assert d["checks"] == [c.to_dict() for c in attached]
    assert d["result"] == reports.PASS_RESULT
    # Same fallback through the string builder and the other entry points.
    assert json.loads(reports.to_json(boot))["checks"] == [c.to_dict() for c in attached]
    suite = ET.fromstring(reports.junit_xml(boot)).find("testsuite")
    assert [c.get("name") for c in suite.findall("testcase")] == [
        reports.BOOT_TESTCASE, "reached-login",
    ]
    assert reports.overall_result(boot) == reports.PASS_RESULT


def test_json_explicit_checks_override_attached():
    attached = [CheckResult("attached", FAIL_STATUS, "nope")]
    override = [CheckResult("override", PASS_STATUS, "ok")]
    boot = _boot()
    boot.checks = attached
    d = reports.result_to_dict(boot, override)
    assert d["checks"] == [override[0].to_dict()]
    assert d["result"] == reports.PASS_RESULT
    # An explicit empty list still means "no checks", not "the attached ones".
    d2 = reports.result_to_dict(boot, ())
    assert d2["checks"] == []


def test_overall_result_none_uses_attached_failing_check():
    boot = _boot()
    boot.checks = [CheckResult("no-kernel-panic", FAIL_STATUS, "Kernel panic ...")]
    assert reports.overall_result(boot) == reports.FAIL_RESULT
    # Boot-level ok stays True (checks are the emitters' concern for the
    # combined verdict) while the roll-up property disagrees.
    assert boot.ok is True
    assert boot.checks_passed is False
    assert reports.overall_result(boot, ()) == reports.PASS_RESULT


# ==========================================================================
# overall verdict
# ==========================================================================


def test_overall_pass_when_ready_and_all_checks_pass():
    assert reports.overall_result(_boot(), [CheckResult("a", PASS_STATUS)]) == reports.PASS_RESULT


def test_overall_fail_when_a_check_fails():
    assert reports.overall_result(_boot(), _checks()) == reports.FAIL_RESULT


def test_overall_fail_on_timeout_even_with_no_checks():
    assert reports.overall_result(_boot(ready=False, timed_out=True), ()) == reports.FAIL_RESULT


def test_overall_fail_on_launch_error():
    assert reports.overall_result(
        _boot(ready=False, error="QEMU binary not found: 'qemu-system-x86_64'"), ()
    ) == reports.FAIL_RESULT


def test_overall_vacuous_pass_with_no_checks_and_ready_boot():
    assert reports.overall_result(_boot(), []) == reports.PASS_RESULT


# ==========================================================================
# JUnit XML
# ==========================================================================


def _parse(xml_text: str):
    """The spec's "parse it back" requirement — must always succeed."""
    return ET.fromstring(xml_text)


def test_junit_is_well_formed_and_has_testsuites_tree():
    root = _parse(reports.junit_xml(_boot(), _checks()))
    assert root.tag == "testsuites"
    assert root.get("name") == reports.SUITE_NAME
    suites = root.findall("testsuite")
    assert len(suites) == 1
    suite = suites[0]
    assert suite.get("image") == "pengyos-core-x86_64.img"
    assert suite.get("accel") == "kvm"
    assert suite.get("result") == reports.FAIL_RESULT


def test_junit_testcase_counts_and_attributes():
    xml = reports.junit_xml(_boot(), _checks())
    root = _parse(xml)
    suite = root.find("testsuite")
    # 1 synthetic boot testcase + 2 checks.
    cases = suite.findall("testcase")
    assert len(cases) == 3
    assert suite.get("tests") == "3"
    assert suite.get("failures") == "1"  # the one failed check; the boot is ok
    assert suite.get("errors") == "0"
    assert suite.get("skipped") == "0"
    assert float(suite.get("time")) == pytest.approx(12.345, abs=0.001)


def test_junit_synthetic_boot_testcase_first_and_passing():
    suite = _parse(reports.junit_xml(_boot(), _checks())).find("testsuite")
    cases = suite.findall("testcase")
    assert cases[0].get("name") == reports.BOOT_TESTCASE
    # A passing testcase has no <failure> child.
    assert cases[0].find("failure") is None


def test_junit_failed_check_carries_failure_with_evidence():
    suite = _parse(reports.junit_xml(_boot(), _checks())).find("testsuite")
    cases = {c.get("name"): c for c in suite.findall("testcase")}
    good = cases["reached-login"].find("failure")
    assert good is None
    bad = cases["no-kernel-panic"].find("failure")
    assert bad is not None
    assert "no-kernel-panic" in bad.get("message")
    # The failure text is the evidence line from the log.
    assert "Kernel panic - not syncing: VFS" in (bad.text or "")


def test_junit_failing_present_check_without_evidence_still_has_text():
    boot = _boot()
    checks = [CheckResult("nope", FAIL_STATUS, None)]
    case = (
        _parse(reports.junit_xml(boot, checks))
        .find("testsuite")
        .findall("testcase")[1]
    )
    failure = case.find("failure")
    assert failure is not None
    # No match line to point at -> an explanatory placeholder, not empty.
    assert failure.text and "appear" in failure.text


def test_junit_timeout_boot_gets_testsuite_and_testcase_failures():
    boot = _boot(ready=False, timed_out=True, returncode=143, duration=1.0)
    xml = reports.junit_xml(boot, ())
    root = _parse(xml)
    suite = root.find("testsuite")
    assert suite.get("result") == reports.FAIL_RESULT
    assert suite.get("tests") == "1"
    assert suite.get("failures") == "1"
    case = suite.find("testcase")
    assert case.get("name") == reports.BOOT_TESTCASE
    failure = case.find("failure")
    assert failure is not None
    assert "timeout" in failure.get("message")


def test_junit_launch_error_boot():
    boot = _boot(
        ready=False,
        returncode=None,
        error="QEMU binary not found: 'qemu-system-x86_64'",
    )
    case = (
        _parse(reports.junit_xml(boot, ())).find("testsuite").find("testcase")
    )
    failure = case.find("failure")
    assert failure is not None
    assert "QEMU binary not found" in failure.text


def test_junit_passing_boot_all_checks():
    boot = _boot()
    checks = [CheckResult("a", PASS_STATUS, "ok"), CheckResult("b", PASS_STATUS, None)]
    suite = _parse(reports.junit_xml(boot, checks)).find("testsuite")
    assert suite.get("failures") == "0"
    assert suite.get("result") == reports.PASS_RESULT
    for case in suite.findall("testcase"):
        assert case.find("failure") is None


def test_junit_escaping_of_hostile_names_and_evidence():
    hostile_name = "check <with & 'quotes'>"
    hostile_evidence = "line with <tags> & more 'stuff' \"here\"\n  "
    checks = [CheckResult(hostile_name, FAIL_STATUS, hostile_evidence)]
    xml = reports.junit_xml(_boot(), checks)
    # The raw text contains the escaped forms, and parses back to originals.
    assert "&lt;with &amp;" in xml
    suite = _parse(xml).find("testsuite")
    case = [c for c in suite.findall("testcase") if c.get("name") == hostile_name][0]
    assert case.find("failure").text == hostile_evidence


def test_junit_accepts_check_dicts_and_rejects_bad_items():
    suite = _parse(
        reports.junit_xml(_boot(), [{"name": "d", "status": PASS_STATUS, "evidence": None}])
    ).find("testsuite")
    names = [c.get("name") for c in suite.findall("testcase")]
    assert names == [reports.BOOT_TESTCASE, "d"]
    with pytest.raises(TypeError, match="CheckResult"):
        reports.junit_xml(_boot(), [42])


def test_junit_xml_has_a_trailing_newline_and_no_declaration_noise():
    text = reports.junit_xml(_boot(), ())
    assert text.endswith("\n")
    assert "<testsuites" in text


# ==========================================================================
# file writers + error result
# ==========================================================================


def test_write_json_creates_parents_and_matches_builder(tmp_path):
    out = tmp_path / "deep" / "nested" / "result.json"
    path = reports.write_json(str(out), _boot(), _checks())
    assert path == str(out)
    assert json.loads(out.read_text(encoding="utf-8")) == reports.result_to_dict(
        _boot(), _checks()
    )


def test_write_junit_creates_parents_and_is_parseable(tmp_path):
    out = tmp_path / "deep" / "nested" / "result.xml"
    path = reports.write_junit(str(out), _boot(), _checks())
    assert path == str(out)
    root = ET.fromstring(out.read_text(encoding="utf-8"))
    assert root.tag == "testsuites"


def test_make_error_result_is_a_failed_result():
    r = reports.make_error_result("missing.img", "disk image not found")
    assert r.status == "failed"
    assert r.ok is False
    assert r.error == "disk image not found"
    assert r.image == "missing.img"
    # It flows through both emitters without issue.
    assert reports.overall_result(r, ()) == reports.FAIL_RESULT
    d = reports.result_to_dict(r, ())
    assert d["result"] == reports.FAIL_RESULT
    ET.fromstring(reports.junit_xml(r, ()))


# ==========================================================================
# CLI wiring (boot --json/--junit against a fake qemu on PATH)
# ==========================================================================

FAKE_QEMU_LOGIN = r'''#!/usr/bin/env python3
import signal, sys, time
path = None
args = sys.argv[1:]
for i, a in enumerate(args):
    if a == "-serial" and i + 1 < len(args) and args[i + 1].startswith("file:"):
        path = args[i + 1][len("file:"):]
if path:
    with open(path, "a") as fh:
        fh.write("Linux boot started\nPengyOS Core ready\npengyos login: \n")
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    time.sleep(0.2)
'''

FAKE_QEMU_HANG = r'''#!/usr/bin/env python3
import signal, sys, time
# Writes a banner but never the readiness marker: the --timeout case.
with open([a[5:] for a in sys.argv[1:] if a.startswith("file:")][0], "a") as fh:
    fh.write("banner only\n")
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    time.sleep(0.2)
'''

FAKE_QEMU_LOGIN_PANIC = r'''#!/usr/bin/env python3
import signal, sys, time
# Reaches the login prompt (readiness) but also emits a kernel panic line:
# the "boot is ready but a default check fails" case for the wired-in
# assertion runner.
path = None
args = sys.argv[1:]
for i, a in enumerate(args):
    if a == "-serial" and i + 1 < len(args) and args[i + 1].startswith("file:"):
        path = args[i + 1][len("file:"):]
if path:
    with open(path, "a") as fh:
        fh.write("[  42.000000] Kernel panic - not syncing: boom\n")
        fh.write("pengyos login: \n")
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    time.sleep(0.2)
'''


def _fake_qemu(tmp_path, name, script) -> str:
    fake_dir = tmp_path / name
    fake_dir.mkdir(exist_ok=True)
    exe = fake_dir / "qemu-system-x86_64"
    exe.write_text(script, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe.parent)


def _install_qemu(tmp_path, monkeypatch, script, name="fakeqemu") -> None:
    fake_dir = _fake_qemu(tmp_path, name, script)
    monkeypatch.setenv("PATH", fake_dir + os.pathsep + os.environ.get("PATH", ""))


def test_cli_boot_writes_json_and_junit_on_success(tmp_path, monkeypatch, capsys):
    _install_qemu(tmp_path, monkeypatch, FAKE_QEMU_LOGIN)
    image = tmp_path / "img.img"
    image.write_bytes(b"\0" * 1024)
    json_out = tmp_path / "out" / "result.json"
    junit_out = tmp_path / "out" / "result.xml"
    rc = cli.main([
        "boot", str(image),
        "--timeout", "10",
        "--json", str(json_out),
        "--junit", str(junit_out),
    ])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert str(json_out) in out and str(junit_out) in out

    d = json.loads(json_out.read_text(encoding="utf-8"))
    for key in ("image", "accel", "duration", "checks", "result"):
        assert key in d
    assert d["image"] == str(image)
    assert d["result"] == reports.PASS_RESULT
    assert d["ready"] is True
    # The wired-in item-6 runner: the default set ran over the captured log
    # and its results are attached to the result the emitters report.
    assert d["checks"] == [
        {"name": "reached-login", "status": PASS_STATUS,
         "evidence": "pengyos login:"},
        {"name": "no-kernel-panic", "status": PASS_STATUS, "evidence": None},
    ]

    out_lines = out.splitlines()
    assert "check:    reached-login PASS — pengyos login:" in out_lines
    assert "check:    no-kernel-panic PASS" in out_lines

    root = ET.fromstring(junit_out.read_text(encoding="utf-8"))
    suite = root.find("testsuite")
    assert suite.get("result") == reports.PASS_RESULT
    assert suite.get("failures") == "0"
    cases = suite.findall("testcase")
    # 1 synthetic boot testcase + the 2 attached default checks, in order.
    assert [c.get("name") for c in cases] == [
        reports.BOOT_TESTCASE, "reached-login", "no-kernel-panic",
    ]
    assert suite.get("tests") == "3"


def test_cli_boot_timeout_writes_reports_and_fails(tmp_path, monkeypatch):
    _install_qemu(tmp_path, monkeypatch, FAKE_QEMU_HANG, name="fakeqemu-hang")
    image = tmp_path / "img.img"
    image.write_bytes(b"\0" * 1024)
    json_out = tmp_path / "result.json"
    junit_out = tmp_path / "result.xml"
    rc = cli.main([
        "boot", str(image),
        "--timeout", "1",
        "--json", str(json_out),
        "--junit", str(junit_out),
    ])
    assert rc == cli.EXIT_FAIL
    d = json.loads(json_out.read_text(encoding="utf-8"))
    assert d["result"] == reports.FAIL_RESULT
    assert d["ready"] is False
    assert d["timed_out"] is True
    # The checks still run over the partial (captured) log even on timeout:
    # the banner-only log has no ``login:`` (reached-login FAIL, no
    # evidence) and no panic (no-kernel-panic PASS).
    assert d["checks"] == [
        {"name": "reached-login", "status": FAIL_STATUS, "evidence": None},
        {"name": "no-kernel-panic", "status": PASS_STATUS, "evidence": None},
    ]
    suite = ET.fromstring(junit_out.read_text(encoding="utf-8")).find("testsuite")
    # boot-timeout failure + the failed reached-login check.
    assert suite.get("failures") == "2"
    cases = {c.get("name"): c for c in suite.findall("testcase")}
    failure = cases[reports.BOOT_TESTCASE].find("failure")
    assert failure is not None and "timeout" in failure.get("message")
    # The failing present-check with no match line gets the placeholder.
    check_failure = cases["reached-login"].find("failure")
    assert check_failure is not None
    assert "appear" in (check_failure.text or "")


def test_cli_boot_ready_but_check_fails_exit_one(tmp_path, monkeypatch, capsys):
    """Wiring contract: ready boot + a failed default check => exit 1."""
    _install_qemu(
        tmp_path, monkeypatch, FAKE_QEMU_LOGIN_PANIC, name="fakeqemu-panic-login"
    )
    image = tmp_path / "img.img"
    image.write_bytes(b"\0" * 1024)
    json_out = tmp_path / "result.json"
    junit_out = tmp_path / "result.xml"
    rc = cli.main([
        "boot", str(image),
        "--timeout", "10",
        "--json", str(json_out),
        "--junit", str(junit_out),
    ])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_FAIL
    assert "result:   ready" in out  # the boot itself reached the marker
    assert "check:    no-kernel-panic FAIL" in out

    d = json.loads(json_out.read_text(encoding="utf-8"))
    assert d["ready"] is True and d["result"] == reports.FAIL_RESULT
    by_name = {c["name"]: c for c in d["checks"]}
    assert by_name["reached-login"]["status"] == PASS_STATUS
    assert by_name["no-kernel-panic"]["status"] == FAIL_STATUS
    assert "Kernel panic" in by_name["no-kernel-panic"]["evidence"]

    suite = ET.fromstring(junit_out.read_text(encoding="utf-8")).find("testsuite")
    assert suite.get("result") == reports.FAIL_RESULT
    # The boot testcase passes; only the absent-check fails, with evidence.
    assert suite.get("failures") == "1"
    cases = {c.get("name"): c for c in suite.findall("testcase")}
    assert cases[reports.BOOT_TESTCASE].find("failure") is None
    failure = cases["no-kernel-panic"].find("failure")
    assert failure is not None and "Kernel panic" in failure.text


def test_cli_boot_preflight_failure_still_writes_reports(tmp_path, capsys):
    """Spec: 'boot … always writes JSON' — even when the image is missing."""
    json_out = tmp_path / "result.json"
    junit_out = tmp_path / "result.xml"
    rc = cli.main([
        "boot", str(tmp_path / "missing.img"),
        "--json", str(json_out),
        "--junit", str(junit_out),
    ])
    assert rc == cli.EXIT_FAIL
    d = json.loads(json_out.read_text(encoding="utf-8"))
    assert d["result"] == reports.FAIL_RESULT
    assert d["image"] == str(tmp_path / "missing.img")
    assert d["error"] and "not found" in d["error"]
    root = ET.fromstring(junit_out.read_text(encoding="utf-8"))
    suite = root.find("testsuite")
    assert suite.get("result") == reports.FAIL_RESULT
    assert suite.find("testcase").find("failure") is not None


def test_cli_boot_without_report_flags_writes_nothing(tmp_path, monkeypatch, capsys):
    _install_qemu(tmp_path, monkeypatch, FAKE_QEMU_LOGIN)
    image = tmp_path / "img.img"
    image.write_bytes(b"\0" * 1024)
    rc = cli.main(["boot", str(image), "--timeout", "10"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert "report:" not in out


def test_cli_boot_unwritable_report_path_is_not_fatal(tmp_path, monkeypatch, capsys):
    # A report that cannot be written is reported on stderr, but the boot's
    # own exit code is preserved.
    _install_qemu(tmp_path, monkeypatch, FAKE_QEMU_LOGIN)
    image = tmp_path / "img.img"
    image.write_bytes(b"\0" * 1024)
    blocked = tmp_path / "img.img"  # a file: writing a path *under* it fails
    rc = cli.main([
        "boot", str(image), "--timeout", "10", "--json", str(blocked / "x.json"),
    ])
    err = capsys.readouterr().err
    assert rc == cli.EXIT_OK
    assert "could not write" in err


def test_emitters_consume_real_fixture_checks():
    """End-to-end shape: real runner output (item 6) feeding item 7's emitters."""
    login_log = _read_fixture("serial_login_sample.log")
    checks = run_assertions(login_log, default_assertions())
    d = reports.result_to_dict(_boot(), checks)
    assert d["result"] == reports.PASS_RESULT
    assert d["checks"] == [c.to_dict() for c in checks]
    suite = ET.fromstring(reports.junit_xml(_boot(), checks)).find("testsuite")
    assert suite.get("failures") == "0"

    panic_log = _read_fixture("serial_kernel_panic_sample.log")
    checks = run_assertions(panic_log, default_assertions())
    d = reports.result_to_dict(_boot(ready=False, timed_out=True), checks)
    assert d["result"] == reports.FAIL_RESULT
    failed = [c for c in d["checks"] if c["status"] == FAIL_STATUS]
    assert len(failed) >= 1
    suite = ET.fromstring(reports.junit_xml(_boot(ready=False, timed_out=True), checks)).find("testsuite")
    # boot failure + failed checks, each with a <failure> element.
    n_failures = int(suite.get("failures"))
    assert n_failures == len(failed) + 1
    for case in suite.findall("testcase"):
        if case.get("name") != reports.BOOT_TESTCASE and case.find("failure") is not None:
            assert case.find("failure").text  # non-empty failure text
