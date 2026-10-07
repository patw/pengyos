"""Result emitters for the PengyOS headless boot-test harness.

Turning a
:class:`.boot.BootResult` (plus the optional list of
:class:`.assertions.CheckResult` from the item-6 assertion runner) into the
two machine-readable report formats the spec's Deliverable promises:

* **JSON** — the spec's shape ``{image, accel, duration,
  checks:[{name, status, evidence}], result}``, kept as a *superset* of that
  (extra keys: ``returncode``, ``ready``, ``timed_out``, ``markers``,
  ``error``, ``serial_log`` — the same convention ``BootResult.to_dict()``
  already uses) so CI can read the verdict and a human can read what
  happened.
* **JUnit XML** — a ``<testsuites>/<testsuite>`` tree with one
  ``<testcase>`` for the boot outcome itself (synthetic check
  ``boot-reached-readiness``) and one ``<testcase>`` per assertion check;
  failures carry a ``<failure>`` element whose message is a short reason and
  whose text is the evidence line. Built with :mod:`xml.etree.ElementTree`,
  so the output is always well-formed and parseable back.

The emitters are *pure* string builders (``result_to_dict`` / ``to_json`` /
``junit_xml``) plus thin file writers (``write_json`` / ``write_junit``); no
QEMU, no network, no display. The overall ``result`` is ``"pass"`` only when
the boot reached readiness **and** every check passed; anything else
(timeout, QEMU failure, a failed check) is ``"fail"``.
"""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from typing import Iterable, Mapping, Union

from .assertions import CheckResult
from .boot import BootResult

__all__ = [
    "PASS_RESULT",
    "FAIL_RESULT",
    "SUITE_NAME",
    "BOOT_TESTCASE",
    "result_to_dict",
    "overall_result",
    "to_json",
    "junit_xml",
    "write_json",
    "write_junit",
    "make_error_result",
]

# Overall verdict values (spec JSON "result").
PASS_RESULT: str = "pass"
FAIL_RESULT: str = "fail"

# JUnit naming.
SUITE_NAME: str = "pengyos-qemu-verify"
BOOT_TESTCASE: str = "boot-reached-readiness"


def _resolve_checks(
    boot: "BootResult",
    checks: "Union[Iterable[Union[CheckResult, Mapping]], None]",
) -> "list[CheckResult]":
    """Resolve the effective check list for a given ``checks`` argument.

    ``None`` (the default in every public emitter) means *the checks attached
    to the boot itself* (``boot.checks``) — the item-6 runner output the boot
    flow attaches after it finishes; an explicit iterable (even an empty one)
    is used verbatim, which is how callers override or test with their own
    checks.
    """
    if checks is None:
        return list(boot.checks)
    return [_as_check_result(c) for c in checks]


def _as_check_result(item: Union["CheckResult", Mapping]) -> CheckResult:
    """Coerce one check to a :class:`CheckResult` (accepts its dict shape)."""
    if isinstance(item, CheckResult):
        return item
    if isinstance(item, Mapping):
        return CheckResult(
            name=str(item["name"]),
            status=str(item["status"]),
            evidence=item.get("evidence"),
        )
    raise TypeError(
        "checks must be CheckResult instances or {name, status, evidence} "
        f"mappings (got {type(item).__name__})"
    )


def overall_result(
    boot: BootResult,
    checks: "Union[Iterable[Union[CheckResult, Mapping]], None]" = None,
) -> str:
    """The overall verdict: ``"pass"`` iff the boot reached readiness and
    every check passed.

    ``checks=None`` (default) uses the checks attached to the boot
    (``boot.checks``); an explicit iterable overrides. A timeout, a QEMU
    launch failure, or a single failed check is a ``fail`` even if everything
    else looks fine. An empty check list is vacuously satisfied (the verdict
    then rests on the boot outcome alone).
    """
    results = _resolve_checks(boot, checks)
    return PASS_RESULT if boot.ok and all(r.passed for r in results) else FAIL_RESULT


def result_to_dict(
    boot: BootResult,
    checks: "Union[Iterable[Union[CheckResult, Mapping]], None]" = None,
) -> "dict":
    """The JSON report: the spec's ``{image, accel, duration, checks,
    result}`` shape as a superset (see the module docstring for the extra
    keys). ``checks=None`` reports the checks attached to the boot
    (``boot.checks``); an explicit iterable overrides. Each check is
    serialised via ``to_dict()`` so the list is exactly the spec's
    ``{name, status, evidence}`` shape, in input order.
    """
    results = _resolve_checks(boot, checks)
    d = boot.to_dict()
    d["checks"] = [c.to_dict() for c in results]
    d["result"] = overall_result(boot, results)
    # Present the spec's keys first (image, accel, duration, checks, result);
    # ordering is cosmetic but makes the emitted JSON easier to eyeball.
    ordered: "dict" = {}
    for key in ("image", "accel", "duration", "checks", "result"):
        ordered[key] = d[key]
    for key, value in d.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def to_json(
    boot: BootResult,
    checks: "Union[Iterable[Union[CheckResult, Mapping]], None]" = None,
) -> str:
    """Serialise :func:`result_to_dict` to a JSON string (2-space indent,
    trailing newline)."""
    return json.dumps(result_to_dict(boot, checks), indent=2) + "\n"


def _boot_failure(boot: BootResult) -> "tuple[str, str]":
    """The ``(message, text)`` failure for the synthetic boot testcase."""
    if boot.ready and boot.error is None:
        message, text = "", ""
    elif boot.error is not None:
        message = f"boot failed: {boot.error}"
        text = boot.error
    elif boot.timed_out:
        message = "timeout: readiness markers not seen before the deadline"
        text = "QEMU was terminated at the --timeout deadline without all " \
               "readiness markers appearing in the serial log."
    else:
        message = "boot did not reach readiness"
        text = "QEMU exited (or no readiness markers appeared) without all " \
               "readiness markers appearing in the serial log."
    return message, text


def _check_failure(reason: str, evidence: "str | None") -> str:
    """Failure text for a check: the evidence line, or an explanatory
    placeholder when there is no match line to point at."""
    if evidence:
        return evidence
    return reason


def junit_xml(
    boot: BootResult,
    checks: "Union[Iterable[Union[CheckResult, Mapping]], None]" = None,
) -> str:
    """Render the JUnit XML report.

    Layout (always well-formed — built via :mod:`xml.etree.ElementTree`):

    * ``<testsuites name="pengyos-qemu-verify" tests=N failures=F time=…>``
      wrapping a single ``<testsuite>`` carrying the boot context as
      attributes (``image``, ``accel``, ``result``, ``tests``, ``failures``,
      ``errors``, ``skipped``, ``time``);
    * the first ``<testcase>`` is the synthetic
      :data:`BOOT_TESTCASE` (``boot-reached-readiness``): no child when the
      boot is ok, otherwise a ``<failure>`` with a short ``message`` and the
      error/timeout text;
    * one ``<testcase>`` per check (``<testcase name=<check name>>``); a
      failing check carries a ``<failure>`` whose text is the evidence line
      (or an explanatory placeholder when the check had no match line).
    """
    results = _resolve_checks(boot, checks)
    verdict = overall_result(boot, results)
    failed_checks = [r for r in results if not r.passed]
    boot_failed = not (boot.ok and not failed_checks)
    boot_ok = boot.ok

    tests = 1 + len(results)
    failures = len(failed_checks) + (0 if boot_ok else 1)

    suites = ET.Element("testsuites", {
        "name": SUITE_NAME,
        "tests": str(tests),
        "failures": str(failures),
        "errors": "0",
        "skipped": "0",
        "time": f"{boot.duration:.3f}",
    })
    suite = ET.SubElement(suites, "testsuite", {
        "name": SUITE_NAME,
        "image": boot.image,
        "accel": boot.accel,
        "result": verdict,
        "tests": str(tests),
        "failures": str(failures),
        "errors": "0",
        "skipped": "0",
        "time": f"{boot.duration:.3f}",
    })

    # Synthetic boot-outcome testcase (first child).
    boot_case = ET.SubElement(suite, "testcase", {
        "name": BOOT_TESTCASE,
        "classname": "pengyos-qemu-verify.boot",
    })
    if not boot_ok:
        message, text = _boot_failure(boot)
        ET.SubElement(boot_case, "failure", {"message": message, "type": "BootFailure"}).text = text

    for r in results:
        case = ET.SubElement(suite, "testcase", {
            "name": r.name,
            "classname": "pengyos-qemu-verify.checks",
        })
        if not r.passed:
            reason = (
                "expected pattern to be absent from the serial log"
                if r.evidence is not None
                else "expected pattern to appear in the serial log"
            )
            failure = ET.SubElement(case, "failure", {"message": f"check failed: {r.name}", "type": "CheckFailed"})
            failure.text = _check_failure(reason, r.evidence)

    return ET.tostring(suites, encoding="unicode") + "\n"


def _write(path: str, text: str) -> str:
    """Write ``text`` to ``path``, creating parent directories as needed.

    Returns the path written. A non-writable location raises ``OSError`` —
    the CLI surfaces that as an error rather than pretending the report was
    emitted.
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def write_json(
    path: str,
    boot: BootResult,
    checks: "Union[Iterable[Union[CheckResult, Mapping]], None]" = None,
) -> str:
    """Write the JSON report to ``path`` (parents created) and return it."""
    return _write(path, to_json(boot, checks))


def write_junit(
    path: str,
    boot: BootResult,
    checks: "Union[Iterable[Union[CheckResult, Mapping]], None]" = None,
) -> str:
    """Write the JUnit XML report to ``path`` (parents created) and return it."""
    return _write(path, junit_xml(boot, checks))


def make_error_result(image: str, error: str) -> BootResult:
    """A minimal :class:`BootResult` for a *preflight* failure (one that
    raised before a boot attempt: missing image, missing firmware, …).

    The emitters then always have a result to report, honouring the spec's
    "always writes JSON" acceptance criterion even when the harness could not
    get far enough to build a full one.
    """
    return BootResult(image=image, error=error)
