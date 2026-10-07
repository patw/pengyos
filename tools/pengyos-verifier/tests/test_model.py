"""Tests for the shared report vocabulary (model.py)."""

from __future__ import annotations

import json

import pytest

from pengyos_verifier.model import (
    Finding,
    Report,
    Status,
    findings_from_mapping,
)


# --- Status ---------------------------------------------------------------

def test_status_values():
    assert Status.PASS.value == "pass"
    assert Status.FAIL.value == "fail"
    assert str(Status.FAIL) == "fail"


# --- Finding ---------------------------------------------------------------

def test_pass_finding_needs_no_evidence():
    f = Finding(check="sbom", status=Status.PASS, message="all good")
    assert f.ok is True
    assert f.evidence == ()


def test_fail_finding_requires_evidence():
    with pytest.raises(ValueError, match="must carry evidence"):
        Finding(check="sbom", status=Status.FAIL, message="no sbom")


def test_status_coerced_from_string():
    f = Finding(check="sbom", status="fail", message="no sbom",
                evidence=("sbom.spdx.json",))
    assert f.status is Status.FAIL
    assert f.ok is False


def test_bad_status_rejected():
    with pytest.raises(ValueError, match="status must be a Status"):
        Finding(check="sbom", status="maybe", message="m",
                evidence=("x",))


def test_empty_check_rejected():
    with pytest.raises(ValueError, match="check id"):
        Finding(check="", status=Status.PASS, message="m")


def test_empty_message_rejected():
    with pytest.raises(ValueError, match="message"):
        Finding(check="sbom", status=Status.PASS, message="")


def test_bare_string_evidence_rejected():
    # A bare str would otherwise be iterated character-by-character into
    # nonsense per-char evidence (caught in iteration 3).
    with pytest.raises(ValueError, match="not a bare string"):
        Finding(check="sbom", status=Status.FAIL, message="m",
                evidence="sbom.spdx.json: missing")


def test_evidence_normalized_to_string_tuple():
    f = Finding(check="repro-score", status=Status.PASS, message="m",
                evidence=["a", 1, None])
    assert f.evidence == ("a", "1", "None")
    with pytest.raises(AttributeError):  # dataclasses.FrozenInstanceError
        f.evidence = ("b",)


def test_finding_to_dict_roundtrip():
    f = Finding(check="attest-chain", status=Status.FAIL,
                message="self-hash mismatch",
                evidence=("line 3", "seq 2"))
    d = f.to_dict()
    assert d == {
        "check": "attest-chain",
        "status": "fail",
        "message": "self-hash mismatch",
        "evidence": ["line 3", "seq 2"],
    }
    assert findings_from_mapping(d) == f


# --- Report ----------------------------------------------------------------

def make_report(*statuses):
    findings = tuple(
        Finding(check=f"check-{i}", status=s,
                message="m",
                evidence=() if s is Status.PASS else ("e",))
        for i, s in enumerate(statuses)
    )
    return Report(build="output/core", attest="ATTEST.log",
                  findings=findings)


def test_report_all_pass():
    r = make_report(Status.PASS, Status.PASS)
    assert r.ok is True
    assert r.failed == ()
    assert r.exit_code == 0


def test_report_any_fail():
    r = make_report(Status.PASS, Status.FAIL, Status.PASS)
    assert r.ok is False
    assert len(r.failed) == 1
    assert r.failed[0].check == "check-1"
    assert r.exit_code == 1


def test_report_preserves_finding_order():
    r = make_report(Status.FAIL, Status.FAIL)
    assert [f.check for f in r.findings] == ["check-0", "check-1"]


def test_report_to_json_is_deterministic_and_parseable():
    r = make_report(Status.PASS, Status.FAIL)
    a, b = r.to_json(), r.to_json()
    assert a == b
    doc = json.loads(a)
    assert doc["verifier"] == "pengyos-verifier"
    assert doc["build"] == "output/core"
    assert doc["attest"] == "ATTEST.log"
    assert doc["ok"] is False
    assert len(doc["findings"]) == 2
    assert doc["findings"][1]["status"] == "fail"
    assert a.endswith("\n")


def test_report_accepts_plain_findings_list():
    r = Report(build="b", attest="a",
               findings=[Finding(check="sbom", status="pass",
                                 message="ok")])
    assert r.findings[0].status is Status.PASS
    assert r.ok is True
