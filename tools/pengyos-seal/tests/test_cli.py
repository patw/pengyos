"""Tests for pengyos_seal.cli."""

from __future__ import annotations

import argparse

import pytest

from pengyos_seal import cli
from pengyos_seal.runner import MemoryRunner, RunResult


# --- parser -----------------------------------------------------------------


def test_build_parser_has_plan_and_run():
    parser = cli.build_parser()
    action = next(
        a for a in parser._actions if type(a).__name__ == "_SubParsersAction"
    )
    assert set(action.choices) == {"plan", "run"}


def test_main_requires_a_subcommand():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == cli.EXIT_USAGE


# --- plan -------------------------------------------------------------------


def test_plan_valid_build_dir(tmp_path, capsys):
    rc = cli.main(["plan", "--build", str(tmp_path)])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    # The plan enumerates the ordered seal steps (sign -> name -> repro ->
    # witness -> verify) with no side effects.
    assert "seal plan for" in out
    assert "sign:" in out and "witness:" in out and "verify:" in out


def test_plan_non_build_dir_clean_error(tmp_path, capsys):
    missing = tmp_path / "does-not-exist"
    rc = cli.main(["plan", "--build", str(missing)])
    assert rc == cli.EXIT_FAIL
    err = capsys.readouterr().err
    assert "error:" in err
    assert "not a directory" in err
    assert "Traceback" not in err


def test_plan_missing_build_arg_raises_usage():
    with pytest.raises(SystemExit) as exc:
        cli.main(["plan"])
    assert exc.value.code == cli.EXIT_USAGE


def test_plan_threads_runner_and_reads_virtual_dir(capsys):
    """``cmd_plan`` honours an injected runner: a seeded virtual dir is discovered.

    Discovery walks the build tree through the runner, so a MemoryRunner seeded
    with the build's real SBOM yields a plan that names the discovered target.
    """
    r = MemoryRunner()
    r.seed_dir("/virtual/build")
    r.seed_file("/virtual/build/images/m2.spdx.json", b"{\"spdx\"}")
    args = argparse.Namespace(build="/virtual/build", json=False)
    rc = cli.cmd_plan(args, r=r)
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "seal plan for /virtual/build" in out
    assert "images/m2.spdx.json" in out
    assert "target: m2" in out


def test_plan_virtual_missing_dir_raises():
    r = MemoryRunner()  # build dir not seeded -> not a dir
    args = argparse.Namespace(build="/virtual/build", json=False)
    with pytest.raises(cli.SealError):
        cli.cmd_plan(args, r=r)


# --- run --------------------------------------------------------------------


def test_run_valid_build_dir(capsys):
    """``run`` now signs the expected artifacts through the injected runner."""
    r = MemoryRunner()
    r.seed_dir("/virtual/build")
    r.seed_file("/virtual/build/images/rootfs.ext4", b"rootfs")
    r.seed_file("/virtual/build/images/bzImage", b"bz")
    r.results["pengyos-sign sign"] = RunResult(args=(), rc=0, stdout="signed")
    # The witness + verifier results are seeded to a clean pass (rc=0) so the
    # full seal runs without a missing tool.
    r.results["pengyos-witness-bundle run"] = RunResult(args=(), rc=0)
    r.results["pengyos-verifier check"] = RunResult(args=(), rc=0)
    args = argparse.Namespace(
        build="/virtual/build",
        key="seed",
        pub=None,
        attest=None,
        prev=None,
        json=None,
        sign_bin=None,
        verify_bin=None,
        witness_bin=None,
        repro_bin=None,
    )
    rc = cli.cmd_run(args, r=r)
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "signed images/rootfs.ext4" in out
    assert "signed images/bzImage" in out
    assert "verifier PASS" in out


def _bin_args(**overrides) -> argparse.Namespace:
    base = dict(
        build="/virtual/build",
        sign_bin=None, verify_bin=None, witness_bin=None, repro_bin=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_require_runner_omits_absent_bin_overrides():
    """No ``--*-bin`` given -> NO overrides (run the tools by name from PATH).

    Regression for the shipped ``pengyos-seal run`` crashing on stage 1: the
    production runner used to register a ``None`` override for every absent
    flag, which blanked ``argv[0]``.
    """
    r = cli._require_runner(_bin_args())
    assert r.overrides == {}


def test_require_runner_keeps_supplied_overrides():
    r = cli._require_runner(_bin_args(sign_bin="/opt/pengyos-sign"))
    assert r.overrides == {"pengyos-sign": "/opt/pengyos-sign"}


def test_require_runner_uses_build_dir_as_runner_root(tmp_path):
    """The runner's cwd must be the build dir: the sign argv uses build-relative
    artifact paths (``images/rootfs.ext4``), so a runner rooted anywhere else
    makes pengyos-sign fail with "No such file or directory"."""
    args = _bin_args()
    args.build = str(tmp_path)
    r = cli._require_runner(args)
    assert r.build_dir == str(tmp_path)


def test_production_runner_runs_tool_by_name(tmp_path):
    """The production runner (no overrides) execs the tool by name and fails
    closed on a missing binary (rc=127) instead of crashing."""
    r = cli._require_runner(_bin_args())
    result = r.run_command(["pengyos-seal-no-such-tool-xyz"])
    assert result.rc == 127


def test_run_missing_key_raises_usage():
    with pytest.raises(SystemExit) as exc:
        cli.main(["run", "--build", "/tmp/whatever"])
    assert exc.value.code == cli.EXIT_USAGE


def test_run_non_build_dir_clean_error(tmp_path, capsys):
    missing = tmp_path / "does-not-exist"
    rc = cli.main(["run", "--build", str(missing), "--key", "seed"])
    assert rc == cli.EXIT_FAIL
    err = capsys.readouterr().err
    assert "error:" in err
    assert "not a directory" in err
    assert "Traceback" not in err


def test_run_threads_runner_and_signs_virtual_dir(capsys):
    """``cmd_run`` honours an injected runner and signs the seeded artifacts."""
    r = MemoryRunner()
    r.seed_dir("/virtual/build")
    r.seed_file("/virtual/build/images/rootfs.ext4", b"rootfs")
    r.seed_file("/virtual/build/images/bzImage", b"bz")
    r.results["pengyos-sign sign"] = RunResult(args=(), rc=0, stdout="signed")
    # The witness + verifier results are seeded to a clean pass (rc=0) so the
    # full seal runs without a missing tool.
    r.results["pengyos-witness-bundle run"] = RunResult(args=(), rc=0)
    r.results["pengyos-verifier check"] = RunResult(args=(), rc=0)
    args = argparse.Namespace(
        build="/virtual/build",
        key="seed",
        pub=None,
        attest=None,
        prev=None,
        json=None,
        sign_bin=None,
        verify_bin=None,
        witness_bin=None,
        repro_bin=None,
    )
    rc = cli.cmd_run(args, r=r)
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "signed images/rootfs.ext4" in out
    assert "signed images/bzImage" in out
    assert "verifier PASS" in out


# --- shared flags / errors --------------------------------------------------


def test_version_flag_exits_zero():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == cli.EXIT_OK


def test_seal_error_maps_to_one_line_no_traceback(tmp_path, capsys):
    with pytest.raises(cli.SealError) as exc:
        raise cli.SealError("boom")
    assert str(exc.value) == "boom"

    rc = cli.main(["plan", "--build", str(tmp_path / "missing")])
    assert rc == cli.EXIT_FAIL
    err = capsys.readouterr().err
    assert err.count("\n") == 1  # exactly one line, no traceback body


def test_expected_artifacts_pinned():
    # Pinned to the pengyos-verifier EXPECTED_ARTIFACTS (M1 board layout).
    assert cli.EXPECTED_ARTIFACTS == ("images/rootfs.ext4", "images/bzImage")
    assert cli.SBOM_ROOT_FILENAME == "sbom.spdx.json"
    assert cli.SIGNATURE_SUFFIX == ".sig"
