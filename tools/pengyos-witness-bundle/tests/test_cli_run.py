"""End-to-end tests for the ``run`` subcommand (implementation task 5).

Covers the full ``run`` pipeline through :func:`pengyos_witness_bundle.cli.main`:

* a good build -> exit 0, ``witness_ready`` and a ``.witness/`` view produced;
* a tampered SBOM -> exit non-zero, signature identified as FAILED (the whole
  point of the "witness can't find the artifact" gap: the signature must verify);
* an empty / missing build -> clean failure (exit 1), never a traceback;
* a partial build -> exit 1 (NOT witness-ready) but the present artifacts are
  still bundled into the view;
* copy (default) vs symlink (``--link``) materialisation;
* the machine-readable ``--json`` surface (stdout and file).

Every case is offline: the SBOM is signed in-tree with the bundled Ed25519, so
no network, root, or third-party package is required.
"""

import contextlib
import io
import json
import os

from pengyos_witness_bundle import cli


# --- build-dir helpers ------------------------------------------------------
def _run_json(argv):
    """Run ``run`` with ``--json`` capturing stdout, and return the payload."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(["run", *argv, "--json"])
    return json.loads(buf.getvalue())


# --- good build -> witness-ready -------------------------------------------
#
# The build-dir fixtures (good_build / signed_build / tampered_build / pub) are
# declared in conftest.py and shared across the suite; they are used below
# instead of the old inline helpers.


def test_run_good_build_exits_zero(good_build):
    assert cli.main(["run", "--build", str(good_build)]) == 0


def test_run_good_build_produces_witness_view(good_build):
    assert cli.main(["run", "--build", str(good_build)]) == 0
    witness = good_build / ".witness"
    for name in (
        "sbom.spdx.json",
        "sbom.cyclonedx.json",
        "sbom.spdx.json.sig",
        "repro-report.json",
    ):
        assert (witness / name).exists(), f"missing {name}"


def test_run_good_build_json_witness_ready(good_build):
    payload = _run_json(["--build", str(good_build)])
    assert payload["witness_ready"] is True
    assert payload["complete"] is True
    assert payload["witness_view"]["mode"] == "copy"
    assert payload["witness_view"]["bundled"]  # non-empty


def test_run_symlink_mode(good_build):
    assert cli.main(["run", "--build", str(good_build), "--link"]) == 0
    witness = good_build / ".witness"
    sbom_view = witness / "sbom.spdx.json"
    assert os.path.islink(sbom_view)
    # The symlink reads back the source content.
    assert sbom_view.read_text() == (
        good_build / "images" / "pengyos-x86_64.spdx.json"
    ).read_text()


# --- tampered SBOM -> non-zero, tamper identified --------------------------


def test_run_tampered_sbom_exits_nonzero(tampered_build, pub):
    rc = cli.main(["run", "--build", str(tampered_build), "--pub", str(pub)])
    assert rc != 0


def test_run_tampered_sbom_identifies_failure(tampered_build, pub):
    # A tampered SBOM is a hard fail: the signature result is null and the exit
    # code is non-zero (the tamper is identified on stderr, not masked).
    payload = _run_json(["--build", str(tampered_build), "--pub", str(pub)])
    assert payload["signature"] is None
    assert payload["witness_ready"] is False


def test_run_verified_sig_exits_zero(signed_build, pub):
    payload = _run_json(["--build", str(signed_build), "--pub", str(pub)])
    assert payload["witness_ready"] is True
    assert payload["signature"]["verified"] is True
    assert payload["witness_ready"] is True


# --- empty / missing build -> clean failure --------------------------------


def test_run_empty_build_exits_nonzero(tmp_path):
    rc = cli.main(["run", "--build", str(tmp_path)])
    assert rc == 1


def test_run_empty_build_reports_missing(empty_build):
    payload = _run_json(["--build", str(empty_build)])
    assert payload["complete"] is False
    assert payload["witness_ready"] is False
    assert payload["missing"] == [
        "sbom",
        "cyclonedx",
        "sbom_signature",
        "repro_report",
    ]


def test_run_missing_dir_exits_one_no_traceback(tmp_path):
    # A build dir that does not exist is a clean runtime error (exit 1), not a
    # traceback.
    assert cli.main(["run", "--build", str(tmp_path / "nope")]) == 1


def test_run_pub_given_but_failed_shows_hard_fail_not_absent(tampered_build, pub, capsys):
    # When --pub is given but verification hard-fails (tamper), the display must
    # say the signature FAILED, never "(no --pub given)".
    cli.main(["run", "--build", str(tampered_build), "--pub", str(pub)])
    out = capsys.readouterr().out
    assert "FAILED (hard fail" in out
    assert "no --pub given" not in out


def test_run_no_pub_shows_no_pub_given(good_build, capsys):
    cli.main(["run", "--build", str(good_build)])
    out = capsys.readouterr().out
    assert "no --pub given" in out


def test_run_partial_build_exits_nonzero_but_bundles_present(tmp_path):
    # Only sbom + signature present: not witness-ready (exit 1), but the present
    # artifacts are still materialised into the view.
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pengyos-x86_64.spdx.json").write_text("SBOM")
    (tmp_path / "images" / "pengyos-x86_64.spdx.json.sig").write_text("SIG")

    payload = _run_json(["--build", str(tmp_path)])
    assert payload["complete"] is False
    assert payload["witness_ready"] is False
    kinds = {b["kind"] for b in payload["witness_view"]["bundled"]}
    assert kinds == {"sbom", "sbom_signature"}
    assert payload["witness_view"]["skipped"] == ["cyclonedx", "repro_report"]


# --- json: stdout vs file --------------------------------------------------


def test_run_json_to_file(good_build):
    out = good_build / "result.json"
    rc = cli.main(["run", "--build", str(good_build), "--json", str(out)])
    assert rc == 0
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["witness_ready"] is True


def test_run_without_json_is_human_output(good_build, capsys):
    cli.main(["run", "--build", str(good_build)])
    out = capsys.readouterr().out
    assert "# pengyos-witness-bundle run" in out
    assert "witness-ready" in out


def test_re_discover_after_bundle_still_finds_real_sbom(good_build):
    # Bundling materialises <build>/.witness/sbom.spdx.json, which matches the
    # broad "*.spdx.json" alias. Discovery must still resolve the *real*
    # images/<target>.spdx.json (most-specific alias wins), never the .witness
    # copy. Regression guard for the "witness can't find the artifact" gap.
    from pengyos_witness_bundle.discovery import discover

    cli.main(["run", "--build", str(good_build)])
    assert (good_build / ".witness" / "sbom.spdx.json").exists()
    result = discover(good_build)
    sbom = next(a for a in result.artifacts if a.kind == "sbom")
    assert sbom.path == "images/pengyos-x86_64.spdx.json"
    assert ".witness" not in (sbom.path or "")
