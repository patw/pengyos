"""Tests for the shared build-dir fixtures (implementation task 6).

This is the dedicated group that exercises the reusable fixtures declared in
``conftest.py`` and pins their intended pass / fail behaviour through the ``run``
CLI:

* :func:`good_build`      — a minimal, complete build (all four canonical
  artifacts present, dummy signature) -> witness-ready with no ``--pub`` ->
  **exit 0 / PASS**;
* :func:`signed_build`    — a good build whose SBOM is signed with a real ed25519
  key -> signature verifies with ``--pub`` -> **exit 0 / PASS**;
* :func:`tampered_build`  — a good build whose SBOM is corrupted *after* signing ->
  the signature no longer matches -> **non-zero / FAIL**, tamper identified;
* :func:`empty_build`     — an empty dir -> every artifact missing -> **non-zero /
  FAIL**;
* :func:`pub`             — the ``signer``'s public key written to a file.

Every case is offline and deterministic: the SBOM is signed in-tree with the
bundled Ed25519, so no network, root, or third-party package is required.
"""

import contextlib
import io
import json

from pengyos_witness_bundle import cli
from pengyos_witness_bundle.discovery import discover


# --- good_build -> PASS -----------------------------------------------------


def test_good_build_is_witness_ready_without_pub(good_build):
    # A complete build with a dummy signature is witness-ready with no key.
    assert discover(good_build).complete is True
    assert cli.main(["run", "--build", str(good_build)]) == 0


def test_good_build_json_reports_pass(good_build):
    payload = _run_json(["--build", str(good_build)])
    assert payload["witness_ready"] is True
    assert payload["complete"] is True


# --- signed_build -> PASS (with --pub) --------------------------------------


def test_signed_build_verifies_and_passes(signed_build, pub):
    assert cli.main(["run", "--build", str(signed_build), "--pub", str(pub)]) == 0


def test_signed_build_json_reports_verified(signed_build, pub):
    payload = _run_json(["--build", str(signed_build), "--pub", str(pub)])
    assert payload["witness_ready"] is True
    assert payload["signature"]["verified"] is True
    assert payload["signature"]["method"] in ("stdlib-ed25519", "pengyos-sbom")


# --- tampered_build -> FAIL (with --pub) ------------------------------------


def test_tampered_build_fails(tampered_build, pub):
    # A tampered SBOM is a hard fail: non-zero exit, tamper identified.
    rc = cli.main(["run", "--build", str(tampered_build), "--pub", str(pub)])
    assert rc != 0


def test_tampered_build_json_identifies_failure(tampered_build, pub):
    payload = _run_json(["--build", str(tampered_build), "--pub", str(pub)])
    # The signature result is null (hard fail on stderr), never a bogus OK.
    assert payload["signature"] is None
    assert payload["witness_ready"] is False


# --- empty_build -> FAIL (missing) ------------------------------------------


def test_empty_build_fails_on_missing(empty_build):
    assert cli.main(["run", "--build", str(empty_build)]) == 1


def test_empty_build_json_reports_all_missing(empty_build):
    payload = _run_json(["--build", str(empty_build)])
    assert payload["complete"] is False
    assert payload["witness_ready"] is False
    assert payload["missing"] == [
        "sbom",
        "cyclonedx",
        "sbom_signature",
        "repro_report",
    ]


# --- pub fixture ------------------------------------------------------------


def test_pub_file_is_32_bytes(pub, signer):
    assert pub.exists()
    assert pub.read_bytes() == signer.pub


# --- cross-fixture invariants -----------------------------------------------


def test_good_and_tampered_builds_share_layout(good_build, tampered_build):
    # The tampered build is the good build with only the SBOM body corrupted, so
    # discovery still finds all four kinds in both (the tamper is at the
    # signature layer, not the presence layer).
    assert discover(good_build).complete is True
    assert discover(tampered_build).complete is True


def _run_json(argv):
    """Run ``run`` with ``--json`` to stdout and return the decoded payload."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(["run", *argv, "--json"])
    return json.loads(buf.getvalue())
