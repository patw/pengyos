"""Tests for pengyos_seal.seal.

Covers the sign stage: invoking ``pengyos-sign`` once per expected artifact with
the exact argv, the clean missing-artifact error, the clean signing-failure
error, idempotent re-runs, and the ``run_seal`` convenience wrapper. Every test
injects a :class:`MemoryRunner` so signing runs against a virtual build tree —
no real files, network, or root.
"""

from __future__ import annotations

import argparse

import pytest

from pengyos_seal import cli, plan, seal
from pengyos_seal.runner import MemoryRunner, RunResult

BUILD = "/virtual/build"


def _plan(
    r: MemoryRunner, *, pub: Optional[str] = None, attest: Optional[str] = None,
) -> "plan.SealPlan":
    """The deterministic seal plan for a seeded runner (builds the plan the run would).

    ``pub`` / ``attest`` are threaded into ``build_plan`` so the witness/verify
    argv carry them — matching how ``cmd_run`` builds the plan.
    """
    return plan.build_plan(BUILD, runner=r, pub=pub, attest=attest)


def _seeded_runner(
    *,
    artifacts: tuple[str, ...] = ("images/rootfs.ext4", "images/bzImage"),
    sign_rc: int = 0,
    sign_stdout: str = "signed",
    sign_stderr: str = "",
    witness_rc: int = 0,
    witness_stderr: str = "",
    verify_rc: int = 0,
    verify_stdout: str = "",
    verify_stderr: str = "",
) -> MemoryRunner:
    """A runner with the given artifacts seeded and a seeded pengyos-sign result.

    The witness + verifier results are seeded to a clean pass (rc=0) so the full
    seal (sign -> name -> repro -> witness -> verify) runs without a missing
    tool — override them to model a failing witness/verifier.
    """
    r = MemoryRunner()
    r.seed_dir(BUILD)
    for artifact in artifacts:
        r.seed_file(f"{BUILD}/{artifact}", b"payload")
    r.results["pengyos-sign sign"] = RunResult(
        args=(), rc=sign_rc, stdout=sign_stdout, stderr=sign_stderr,
    )
    r.results["pengyos-witness-bundle run"] = RunResult(
        args=(), rc=witness_rc, stdout="", stderr=witness_stderr,
    )
    r.results["pengyos-verifier check"] = RunResult(
        args=(), rc=verify_rc, stdout=verify_stdout, stderr=verify_stderr,
    )
    return r


# --- sign: golden argv ------------------------------------------------------


def test_sign_invokes_penyos_sign_once_per_artifact_in_order():
    r = _seeded_runner()
    result = seal.Seal(BUILD, "seed", r).sign()
    assert result.signed == list(cli.EXPECTED_ARTIFACTS)
    # One command per expected artifact, in pinned order.
    assert r.commands == [
        (
            "pengyos-sign", "sign", "images/rootfs.ext4",
            "--key", "seed", "--sig", "images/rootfs.ext4.sig",
        ),
        (
            "pengyos-sign", "sign", "images/bzImage",
            "--key", "seed", "--sig", "images/bzImage.sig",
        ),
    ]


def test_sign_passes_the_real_key_into_argv():
    r = _seeded_runner()
    seal.Seal(BUILD, "s3cr3t", r).sign()
    assert r.commands[0][4] == "s3cr3t"  # the value after "--key"


def test_sign_checks_existence_before_signing():
    """The seal probes the build tree (through the runner) before signing."""
    r = _seeded_runner()
    seal.Seal(BUILD, "seed", r).sign()
    # The existence probe is a build-dir-qualified path handed to the runner.
    assert r.exists(f"{BUILD}/images/rootfs.ext4")
    assert r.exists(f"{BUILD}/images/bzImage")


# --- sign: missing artifact -------------------------------------------------


def test_sign_missing_artifact_fails_cleanly_naming_it():
    # images/bzImage is absent.
    r = _seeded_runner(artifacts=("images/rootfs.ext4",))
    with pytest.raises(cli.SealError) as exc:
        seal.Seal(BUILD, "seed", r).sign()
    msg = str(exc.value)
    assert "images/bzImage" in msg
    assert "not found" in msg
    # The missing one is last in pinned order, so the first is signed first...
    assert r.commands == [
        ("pengyos-sign", "sign", "images/rootfs.ext4", "--key", "seed",
         "--sig", "images/rootfs.ext4.sig"),
    ]


def test_sign_first_missing_artifact_stops_before_signing_second():
    # images/rootfs.ext4 (the first in pinned order) is absent -> never sign it,
    # and never reach images/bzImage.
    r = _seeded_runner(artifacts=("images/bzImage",))
    with pytest.raises(cli.SealError):
        seal.Seal(BUILD, "seed", r).sign()
    assert r.commands == []


def test_sign_missing_artifact_is_a_seal_error_not_a_traceback():
    r = _seeded_runner(artifacts=())  # nothing seeded
    with pytest.raises(cli.SealError):
        seal.Seal(BUILD, "seed", r).sign()


# --- sign: signing failure --------------------------------------------------


def test_sign_nonzero_exit_fails_cleanly_naming_it():
    r = _seeded_runner(sign_rc=1, sign_stderr="ed25519: bad key")
    with pytest.raises(cli.SealError) as exc:
        seal.Seal(BUILD, "seed", r).sign()
    msg = str(exc.value)
    assert "images/rootfs.ext4" in msg
    assert "ed25519: bad key" in msg


def test_sign_zero_exit_with_empty_stderr_reports_exit_code():
    r = _seeded_runner(sign_rc=3, sign_stderr="")
    with pytest.raises(cli.SealError) as exc:
        seal.Seal(BUILD, "seed", r).sign()
    assert "exit code 3" in str(exc.value)


# --- sign: idempotency ------------------------------------------------------


def test_sign_is_idempotent_reinvokes_penyos_sign():
    """Re-running the seal re-invokes pengyos-sign, overwriting the .sig."""
    r = _seeded_runner()
    seal.Seal(BUILD, "seed", r).sign()
    seal.Seal(BUILD, "seed", r).sign()
    # Two rounds of signing — the second overwrites the first's signatures.
    assert len(r.commands) == 4
    assert r.commands[0] == r.commands[2]
    assert r.commands[1] == r.commands[3]


# --- run_seal ---------------------------------------------------------------


def test_run_seal_runs_sign_and_returns_result():
    r = _seeded_runner()
    result = seal.run_seal(BUILD, "seed", r)
    assert result.build_dir == BUILD
    assert result.signed == list(cli.EXPECTED_ARTIFACTS)


def test_run_seal_propagates_missing_artifact():
    r = _seeded_runner(artifacts=("images/rootfs.ext4",))
    with pytest.raises(cli.SealError):
        seal.run_seal(BUILD, "seed", r)


# --- name (normalise_sbom): golden argv ------------------------------------


def _seeded_sbom_runner(
    *,
    sbom: str = "images/m2.spdx.json",
    sbom_data: bytes = b"{\"spdxVersion\": \"2.3\"}",
) -> MemoryRunner:
    """A runner with artifacts + an SBOM seeded, and a seeded pengyos-sign.

    The witness + verifier results are seeded to a clean pass (rc=0) so the full
    seal runs without a missing tool.
    """
    r = MemoryRunner()
    r.seed_dir(BUILD)
    for artifact in ("images/rootfs.ext4", "images/bzImage"):
        r.seed_file(f"{BUILD}/{artifact}", b"payload")
    r.seed_file(f"{BUILD}/{sbom}", sbom_data)
    r.results["pengyos-sign sign"] = RunResult(args=(), rc=0, stdout="signed")
    r.results["pengyos-witness-bundle run"] = RunResult(args=(), rc=0)
    r.results["pengyos-verifier check"] = RunResult(args=(), rc=0)
    return r


def test_normalise_sbom_symlinks_discovered_sbom_to_root_name():
    r = _seeded_sbom_runner()
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).normalise_sbom()
    # (source, root) are the build-relative paths the seal symlinks.
    assert result.sbom_symlinked == ("images/m2.spdx.json", "sbom.spdx.json")
    # The name stage symlinks — it does not invoke an external tool.
    assert r.commands == []


def test_normalise_sbom_symlink_points_discovered_to_conventional_root():
    r = _seeded_sbom_runner()
    seal.Seal(BUILD, "seed", r, plan=_plan(r)).normalise_sbom()
    # The runner records exactly one symlink: root name -> discovered SBOM,
    # both build-dir-qualified.
    assert r.links_created == [
        (f"{BUILD}/sbom.spdx.json", f"{BUILD}/images/m2.spdx.json"),
    ]


def test_normalise_sbom_symlink_resolves_to_real_doc():
    """The verifier reads sbom.spdx.json through the symlink — it must resolve."""
    r = _seeded_sbom_runner()
    seal.Seal(BUILD, "seed", r, plan=_plan(r)).normalise_sbom()
    # Reading the conventional root name through the runner follows the symlink
    # to the real SPDX doc.
    assert r.read_bytes(f"{BUILD}/sbom.spdx.json") == b"{\"spdxVersion\": \"2.3\"}"
    assert r.readlink(f"{BUILD}/sbom.spdx.json") == (
        f"{BUILD}/images/m2.spdx.json"
    )


def test_name_stage_prefers_symlink_over_copy():
    """The seal symlinks (records one link, writes nothing) rather than copying."""
    r = _seeded_sbom_runner()
    seal.Seal(BUILD, "seed", r, plan=_plan(r)).normalise_sbom()
    # No copy: the root name was never written as a real file — only linked.
    assert f"{BUILD}/sbom.spdx.json" not in r.files
    assert r.written_paths == []
    assert len(r.links_created) == 1


def test_normalise_sbom_idempotent_overwrites_existing_link():
    """Re-running the name stage replaces the symlink cleanly."""
    r = _seeded_sbom_runner()
    seal.Seal(BUILD, "seed", r, plan=_plan(r)).normalise_sbom()
    seal.Seal(BUILD, "seed", r, plan=_plan(r)).normalise_sbom()
    # Two runs -> two recorded links (the second replaces the first).
    assert len(r.links_created) == 2
    assert r.links_created[0] == r.links_created[1]


def test_normalise_sbom_handles_a_longer_target_name():
    """The build may emit images/pengyos-x86_64.spdx.json — still normalised."""
    r = _seeded_sbom_runner(sbom="images/pengyos-x86_64.spdx.json")
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).normalise_sbom()
    assert result.sbom_symlinked == (
        "images/pengyos-x86_64.spdx.json",
        "sbom.spdx.json",
    )
    assert r.links_created == [
        (f"{BUILD}/sbom.spdx.json", f"{BUILD}/images/pengyos-x86_64.spdx.json"),
    ]


# --- name (normalise_sbom): absent SBOM ------------------------------------


def test_normalise_sbom_absent_is_a_note_not_an_error():
    """No discovered SBOM -> a recorded note, no symlink, no raise."""
    r = MemoryRunner()
    r.seed_dir(BUILD)
    for artifact in ("images/rootfs.ext4", "images/bzImage"):
        r.seed_file(f"{BUILD}/{artifact}", b"payload")
    r.results["pengyos-sign sign"] = RunResult(args=(), rc=0, stdout="signed")
    result = seal.Seal(BUILD, "seed", r).normalise_sbom()
    assert result.sbom_symlinked is None
    assert result.errors  # a non-fatal note was recorded
    assert r.links_created == []
    assert r.commands == []


def test_run_signs_then_normalises_sbom():
    """Seal.run runs sign then the name stage in provenance order."""
    r = _seeded_sbom_runner()
    result = seal.run_seal(BUILD, "seed", r, plan=_plan(r))
    # Signed both artifacts...
    assert result.signed == ["images/rootfs.ext4", "images/bzImage"]
    # ...then normalised the SBOM.
    assert result.sbom_symlinked == ("images/m2.spdx.json", "sbom.spdx.json")
    # Two signing commands (one per artifact), then the witness and verifier.
    assert [c[0] for c in r.commands] == [
        "pengyos-sign", "pengyos-sign",
        "pengyos-witness-bundle", "pengyos-verifier",
    ]
    assert r.links_created  # a symlink was created


# --- cmd_run: name wiring ---------------------------------------------------


def test_cmd_run_signs_and_normalises_sbom(capsys):
    r = _seeded_sbom_runner()
    rc = cli.cmd_run(_run_args(), r=r)
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "signed images/rootfs.ext4" in out
    assert "signed images/bzImage" in out
    assert "normalised SBOM images/m2.spdx.json -> sbom.spdx.json" in out


def test_cmd_run_no_sbom_surfaces_a_note(capsys):
    r = MemoryRunner()
    r.seed_dir(BUILD)
    for artifact in ("images/rootfs.ext4", "images/bzImage"):
        r.seed_file(f"{BUILD}/{artifact}", b"payload")
    r.results["pengyos-sign sign"] = RunResult(args=(), rc=0, stdout="signed")
    # The witness + verifier results are seeded to a clean pass (rc=0) so the
    # full seal runs without a missing tool.
    r.results["pengyos-witness-bundle run"] = RunResult(args=(), rc=0)
    r.results["pengyos-verifier check"] = RunResult(args=(), rc=0)
    rc = cli.cmd_run(_run_args(), r=r)
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "normalised SBOM" not in out
    assert "nothing to" in out  # the non-fatal note was surfaced


# --- cmd_run: sign wiring ---------------------------------------------------


def _run_args(**overrides) -> argparse.Namespace:
    base = dict(
        build=BUILD, key="seed", pub=None, attest=None, prev=None, json=None,
        sign_bin=None, verify_bin=None, witness_bin=None, repro_bin=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cmd_run_signs_and_prints_each_artifact(capsys):
    r = _seeded_runner()
    rc = cli.cmd_run(_run_args(), r=r)
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "signed images/rootfs.ext4" in out
    assert "signed images/bzImage" in out


def test_cmd_run_missing_artifact_maps_to_clean_error(tmp_path, capsys):
    # An empty build dir has no artifacts -> the seal fails clean.
    rc = cli.main(["run", "--build", str(tmp_path), "--key", "seed"])
    assert rc == cli.EXIT_FAIL
    err = capsys.readouterr().err
    assert "error:" in err
    assert "not found" in err
    assert "Traceback" not in err


def test_cmd_run_sign_failure_raises_seal_error():
    """``cmd_run`` propagates the SealError; ``main`` maps it to a clean error."""
    r = _seeded_runner(sign_rc=1, sign_stderr="boom")
    with pytest.raises(cli.SealError) as exc:
        cli.cmd_run(_run_args(), r=r)
    assert "images/rootfs.ext4" in str(exc.value)
    assert "boom" in str(exc.value)


def test_main_sign_failure_maps_to_one_line_error(tmp_path, capsys):
    """End-to-end: a signing failure through ``main`` is a clean one-line error."""
    # An empty build dir has no artifacts to sign -> missing-artifact SealError.
    rc = cli.main(["run", "--build", str(tmp_path), "--key", "seed"])
    assert rc == cli.EXIT_FAIL
    err = capsys.readouterr().err
    assert err.count("\n") == 1  # exactly one line, no traceback body
    assert "not found" in err


# --- repro (surface_repro) --------------------------------------------------


class _DiffRunner(MemoryRunner):
    """A MemoryRunner whose ``pengyos-repro-report diff`` seeds the report.

    Models the real diff writing ``repro-report.json`` to the witness path
    (``images/repro-report.json``): the report appears in the tree *after* the
    diff runs, not before — a report seeded beforehand would make the plan see
    the status as ``existing`` rather than ``diff``.
    """

    def run_command(self, args) -> RunResult:
        cmd = tuple(str(a) for a in args)
        if cmd[:2] == ("pengyos-repro-report", "diff"):
            self.seed_file(f"{BUILD}/images/repro-report.json", b"{}")
        return super().run_command(args)


def _repro_runner(
    *,
    repro: str | None = None,
    diff: bool = False,
    diff_rc: int = 0,
    diff_stderr: str = "",
    witness_rc: int = 0,
    verify_rc: int = 0,
) -> MemoryRunner:
    """A runner with artifacts + SBOM seeded and the repro report as given.

    ``repro`` is the build-relative path of an already-present report (or
    ``None``); when ``diff`` is True and no report is present, the runner is a
    :class:`_DiffRunner` so the diff seeds the report when it runs. The witness
    and verifier results default to a clean pass (rc=0); override ``witness_rc``
    / ``verify_rc`` to model a failing witness/verifier.
    """
    if diff:
        r: MemoryRunner = _DiffRunner()
    else:
        r = MemoryRunner()
    r.seed_dir(BUILD)
    for artifact in ("images/rootfs.ext4", "images/bzImage"):
        r.seed_file(f"{BUILD}/{artifact}", b"payload")
    r.seed_file(f"{BUILD}/images/m2.spdx.json", b"{}")
    r.results["pengyos-sign sign"] = RunResult(args=(), rc=0, stdout="signed")
    # The witness + verifier results are seeded (override the defaults below).
    r.results["pengyos-witness-bundle run"] = RunResult(args=(), rc=witness_rc)
    r.results["pengyos-verifier check"] = RunResult(args=(), rc=verify_rc)
    if repro is not None:
        r.seed_file(f"{BUILD}/{repro}", b"{}")
    r.results["pengyos-repro-report diff"] = RunResult(
        args=(), rc=diff_rc, stdout="diffed", stderr=diff_stderr,
    )
    return r


# --- repro: existing ---------------------------------------------------------


def test_surface_repro_existing_images_symlinks_to_root():
    """A report at images/repro-report.json is symlinked to the root name."""
    r = _repro_runner(repro="images/repro-report.json")
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).surface_repro()
    assert result.repro is not None
    assert result.repro.status == plan.REPRO_EXISTING
    assert result.repro.symlinked == ("images/repro-report.json", "repro-report.json")
    # One symlink: the witness path -> the conventional root name.
    assert r.links_created == [
        (f"{BUILD}/repro-report.json", f"{BUILD}/images/repro-report.json"),
    ]
    # No repro command ran (it was already present).
    assert not [c for c in r.commands if c[0] == "pengyos-repro-report"]


def test_surface_repro_existing_at_root_needs_no_symlink():
    """A report already at the root name is left in place — no symlink."""
    r = _repro_runner(repro="repro-report.json")
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).surface_repro()
    assert result.repro is not None
    assert result.repro.status == plan.REPRO_EXISTING
    assert result.repro.symlinked is None
    assert r.links_created == []


def test_surface_repro_existing_resolves_through_symlink():
    """The root name resolves through the symlink to the real report."""
    r = _repro_runner(repro="images/repro-report.json")
    seal.Seal(BUILD, "seed", r, plan=_plan(r)).surface_repro()
    assert r.readlink(f"{BUILD}/repro-report.json") == (
        f"{BUILD}/images/repro-report.json"
    )


# --- repro: diff -------------------------------------------------------------


def test_surface_repro_diff_runs_and_symlinks_result():
    """No report + --prev -> the diff runs and its result is symlinked to root."""
    r = _repro_runner(diff=True)
    # The plan must see --prev so its repro status is ``diff`` (a report seeded
    # beforehand would make it ``existing``; the _DiffRunner seeds it on run).
    p = plan.build_plan(BUILD, runner=r, prev="/prev/build")
    result = seal.Seal(BUILD, "seed", r, plan=p, prev="/prev/build").surface_repro()
    assert result.repro is not None
    assert result.repro.status == plan.REPRO_EXISTING
    assert result.repro.symlinked == ("images/repro-report.json", "repro-report.json")
    # The diff ran once, with the build dir and prev.
    diff_cmds = [c for c in r.commands if c[0] == "pengyos-repro-report"]
    assert len(diff_cmds) == 1
    assert diff_cmds[0] == (
        "pengyos-repro-report", "diff", "--a", BUILD, "--b", "/prev/build",
    )
    # The result was symlinked to the root name.
    assert r.links_created == [
        (f"{BUILD}/repro-report.json", f"{BUILD}/images/repro-report.json"),
    ]


def test_surface_repro_diff_nonzero_exit_fails_cleanly():
    """A diff that exits non-zero is a clean SealError, not a masked skip."""
    r = _repro_runner(diff=True, diff_rc=1, diff_stderr="missing --b")
    p = plan.build_plan(BUILD, runner=r, prev="/prev/build")
    with pytest.raises(cli.SealError) as exc:
        seal.Seal(BUILD, "seed", r, plan=p, prev="/prev/build").surface_repro()
    msg = str(exc.value)
    assert "pengyos-repro-report diff failed" in msg
    assert "missing --b" in msg


def test_surface_repro_diff_no_output_fails_cleanly():
    """A diff that runs but produces nothing discoverable is a clean SealError."""
    # A runner whose diff is a no-op that writes nothing.
    r = MemoryRunner()
    r.seed_dir(BUILD)
    for artifact in ("images/rootfs.ext4", "images/bzImage"):
        r.seed_file(f"{BUILD}/{artifact}", b"payload")
    r.seed_file(f"{BUILD}/images/m2.spdx.json", b"{}")
    r.results["pengyos-sign sign"] = RunResult(args=(), rc=0, stdout="signed")
    r.results["pengyos-repro-report diff"] = RunResult(args=(), rc=0, stdout="")
    p = plan.build_plan(BUILD, runner=r, prev="/prev/build")
    with pytest.raises(cli.SealError) as exc:
        seal.Seal(BUILD, "seed", r, plan=p, prev="/prev/build").surface_repro()
    assert "produced no repro report" in str(exc.value)


# --- repro: skip -------------------------------------------------------------


def test_surface_repro_skip_without_prev_is_a_note():
    """No report and no --prev: a recorded skip, not a failure."""
    r = _repro_runner(repro=None)  # no report, no --prev
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).surface_repro()
    assert result.repro is not None
    assert result.repro.status == plan.REPRO_SKIP
    assert result.repro.symlinked is None
    assert r.links_created == []
    # The skip is a non-fatal note.
    assert any("cannot compare itself" in e for e in result.errors)
    # No diff ran.
    assert not [c for c in r.commands if c[0] == "pengyos-repro-report"]


def test_surface_repro_no_plan_is_a_note():
    """A seal with no plan records a repro note rather than raising."""
    r = _repro_runner(repro=None)
    result = seal.Seal(BUILD, "seed", r).surface_repro()
    assert result.repro is None
    assert any("no seal plan" in e for e in result.errors)


# --- run: repro wired in -----------------------------------------------------


def test_run_runs_sign_name_and_repro_in_order():
    """Seal.run runs sign, then name, then repro, in provenance order."""
    r = _repro_runner(repro="images/repro-report.json")
    result = seal.run_seal(BUILD, "seed", r, plan=_plan(r))
    assert result.signed == ["images/rootfs.ext4", "images/bzImage"]
    assert result.sbom_symlinked == ("images/m2.spdx.json", "sbom.spdx.json")
    assert result.repro is not None
    assert result.repro.symlinked == ("images/repro-report.json", "repro-report.json")


def test_run_repro_skip_does_not_fail_the_run():
    """A repro skip is a note, so the whole run still exits clean."""
    r = _repro_runner(repro=None)
    result = seal.run_seal(BUILD, "seed", r, plan=_plan(r))
    assert result.signed == ["images/rootfs.ext4", "images/bzImage"]
    assert result.repro.status == plan.REPRO_SKIP
    assert any("cannot compare itself" in e for e in result.errors)


# --- cmd_run: repro wiring ---------------------------------------------------


def test_cmd_run_repro_existing_symlink(capsys):
    r = _repro_runner(repro="images/repro-report.json")
    rc = cli.cmd_run(_run_args(), r=r)
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "repro images/repro-report.json -> repro-report.json" in out


def test_cmd_run_repro_existing_at_root(capsys):
    r = _repro_runner(repro="repro-report.json")
    rc = cli.cmd_run(_run_args(), r=r)
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "repro report already at repro-report.json" in out


def test_cmd_run_repro_diff(capsys):
    r = _repro_runner(diff=True)
    rc = cli.cmd_run(_run_args(prev="/prev/build"), r=r)
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "repro images/repro-report.json -> repro-report.json" in out


def test_cmd_run_repro_skip_surfaces_note(capsys):
    r = _repro_runner(repro=None)
    rc = cli.cmd_run(_run_args(), r=r)
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "cannot compare itself" in out
    # No diff ran for a skip.
    assert not [c for c in r.commands if c[0] == "pengyos-repro-report"]


def test_cmd_run_repro_diff_failure_raises_seal_error():
    """A diff failure propagates as a SealError through cmd_run (mapped by main)."""
    r = _repro_runner(diff=True, diff_rc=1, diff_stderr="boom")
    with pytest.raises(cli.SealError) as exc:
        cli.cmd_run(_run_args(prev="/prev/build"), r=r)
    assert "pengyos-repro-report diff failed" in str(exc.value)
    assert "boom" in str(exc.value)


def test_cmd_run_repro_diff_failure_maps_to_one_line_error(tmp_path, capsys):
    """End-to-end: a diff failure through ``main`` is a clean one-line error."""
    # A real build dir with the artifacts + SBOM, so the seal reaches the diff.
    build = tmp_path / "build"
    (build / "images").mkdir(parents=True)
    (build / "images" / "rootfs.ext4").write_bytes(b"payload")
    (build / "images" / "bzImage").write_bytes(b"payload")
    (build / "images" / "m2.spdx.json").write_bytes(b"{}")
    # A sign-bin that exits 0 (sign succeeds) and a repro-bin that exits 1 (the
    # diff fails cleanly) — both real binaries, so the seal reaches the diff.
    sign_marker = tmp_path / "sign-ok.sh"
    sign_marker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    sign_marker.chmod(0o755)
    repro_marker = tmp_path / "repro-fail.sh"
    repro_marker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    repro_marker.chmod(0o755)
    rc = cli.main(
        [
            "run", "--build", str(build), "--key", "seed",
            "--prev", "/prev/build",
            "--sign-bin", str(sign_marker),
            "--repro-bin", str(repro_marker),
        ]
    )
    assert rc == cli.EXIT_FAIL
    err = capsys.readouterr().err
    assert "pengyos-repro-report diff failed" in err
    assert err.count("\n") == 1  # exactly one line, no traceback body


# --- witness (build_witness) -------------------------------------------------


def _witness_runner(*, witness_rc: int = 0, witness_stderr: str = "") -> MemoryRunner:
    """A seeded full-build runner with a configurable witness result."""
    r = _seeded_sbom_runner()
    r.results["pengyos-witness-bundle run"] = RunResult(
        args=(), rc=witness_rc, stdout="", stderr=witness_stderr,
    )
    return r


def test_build_witness_runs_golden_argv():
    """The witness stage runs ``pengyos-witness-bundle run --build DIR``."""
    r = _witness_runner()
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).build_witness()
    assert result.witness is not None
    assert result.witness.status == seal.WITNESS_BUILT
    assert result.witness.argv == ("pengyos-witness-bundle", "run", "--build", BUILD)
    # Exactly one witness command ran, with the golden argv.
    witness_cmds = [c for c in r.commands if c[0] == "pengyos-witness-bundle"]
    assert witness_cmds == [("pengyos-witness-bundle", "run", "--build", BUILD)]


def test_build_witness_passes_pub_when_given():
    """``--pub`` is threaded into the witness argv (via the plan, as cmd_run does)."""
    r = _witness_runner()
    seal.Seal(
        BUILD, "seed", r, plan=_plan(r, pub="k.pub"), pub="k.pub",
    ).build_witness()
    witness_cmds = [c for c in r.commands if c[0] == "pengyos-witness-bundle"]
    assert witness_cmds == [
        ("pengyos-witness-bundle", "run", "--build", BUILD, "--pub", "k.pub"),
    ]


def test_build_witness_nonzero_exit_fails_cleanly():
    """A failing witness is a clean SealError naming the tool + stderr."""
    r = _witness_runner(witness_rc=1, witness_stderr="no SBOM to witness")
    with pytest.raises(cli.SealError) as exc:
        seal.Seal(BUILD, "seed", r, plan=_plan(r)).build_witness()
    msg = str(exc.value)
    assert "pengyos-witness-bundle failed" in msg
    assert "no SBOM to witness" in msg


def test_build_witness_nonzero_exit_reports_exit_code_when_no_stderr():
    r = _witness_runner(witness_rc=3, witness_stderr="")
    with pytest.raises(cli.SealError) as exc:
        seal.Seal(BUILD, "seed", r, plan=_plan(r)).build_witness()
    assert "exit code 3" in str(exc.value)


def test_build_witness_no_plan_is_a_note():
    """No plan -> a recorded note, no witness command, no raise."""
    r = _seeded_sbom_runner()
    result = seal.Seal(BUILD, "seed", r).build_witness()
    assert result.witness is None
    assert any("no seal plan" in e for e in result.errors)
    assert not [c for c in r.commands if c[0] == "pengyos-witness-bundle"]


# --- verify (run_verifier) ---------------------------------------------------


def _verify_runner(*, verify_rc: int = 0, verify_stdout: str = "",
                   verify_stderr: str = "") -> MemoryRunner:
    """A seeded full-build runner with a configurable verifier result."""
    r = _seeded_sbom_runner()
    r.results["pengyos-verifier check"] = RunResult(
        args=(), rc=verify_rc, stdout=verify_stdout, stderr=verify_stderr,
    )
    return r


def test_run_verifier_runs_golden_argv():
    """The verify stage runs ``pengyos-verifier check --build DIR``."""
    r = _verify_runner()
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).run_verifier()
    assert result.verifier is not None
    assert result.verifier.exit_code == 0
    assert result.verifier.verdict == seal.VERDICT_PASS
    verify_cmds = [c for c in r.commands if c[0] == "pengyos-verifier"]
    assert verify_cmds == [("pengyos-verifier", "check", "--build", BUILD)]


def test_run_verifier_threads_attest_and_pub():
    """``--attest`` then ``--pub`` are threaded into the verifier argv (via the plan)."""
    r = _verify_runner()
    seal.Seal(
        BUILD, "seed", r, plan=_plan(r, pub="k.pub", attest="log"),
        attest="log", pub="k.pub",
    ).run_verifier()
    verify_cmds = [c for c in r.commands if c[0] == "pengyos-verifier"]
    assert verify_cmds == [
        ("pengyos-verifier", "check", "--build", BUILD, "--attest", "log", "--pub", "k.pub"),
    ]


def test_run_verifier_pass_records_pass_verdict():
    """rc=0 -> verdict PASS, exit_code 0 recorded."""
    r = _verify_runner(verify_rc=0, verify_stdout="all checks passed")
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).run_verifier()
    assert result.verifier.exit_code == 0
    assert result.verifier.verdict == seal.VERDICT_PASS
    assert result.verifier.stdout == "all checks passed"


def test_run_verifier_fail_records_fail_verdict_and_exit_code():
    """A failing verifier is NOT raised — its exit code + FAIL verdict recorded."""
    r = _verify_runner(verify_rc=2, verify_stderr="sbom check failed")
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).run_verifier()
    # The seal never raises on a failing verifier — the exit code propagates.
    assert result.verifier.exit_code == 2
    assert result.verifier.verdict == seal.VERDICT_FAIL


def test_run_verifier_structured_stdout_is_honoured():
    """A structured ``{"verdict": ...}`` stdout overrides the exit-code verdict."""
    # rc=0 but the verifier reports FAIL in its structured output.
    r = _verify_runner(verify_rc=0, verify_stdout='{"verdict": "FAIL", "checks": {}}')
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).run_verifier()
    assert result.verifier.verdict == seal.VERDICT_FAIL


def test_run_verifier_structured_verdict_normalises_case():
    """A lower-case structured verdict is normalised to upper-case."""
    r = _verify_runner(verify_rc=0, verify_stdout='{"verdict": "pass"}')
    result = seal.Seal(BUILD, "seed", r, plan=_plan(r)).run_verifier()
    assert result.verifier.verdict == seal.VERDICT_PASS


def test_run_verifier_no_plan_is_a_note():
    """No plan -> a recorded note, no verifier command, no raise."""
    r = _seeded_sbom_runner()
    result = seal.Seal(BUILD, "seed", r).run_verifier()
    assert result.verifier is None
    assert any("no seal plan" in e for e in result.errors)
    assert not [c for c in r.commands if c[0] == "pengyos-verifier"]


# --- verdict parsing (unit) --------------------------------------------------


def test_verdict_from_result_exit_code_only():
    """Without structured stdout the exit code is the sole verdict source."""
    assert seal._verdict_from_result(RunResult((), 0)) == seal.VERDICT_PASS
    assert seal._verdict_from_result(RunResult((), 1)) == seal.VERDICT_FAIL
    assert seal._verdict_from_result(RunResult((), 2)) == seal.VERDICT_FAIL


def test_verdict_from_result_structured_beats_exit_code():
    """Structured stdout wins over the exit code when it is a known verdict."""
    stdout = '{"verdict": "PASS"}'
    # rc=2 but structured PASS -> PASS (the structured verdict is honoured).
    assert seal._verdict_from_result(
        RunResult((), 2, stdout=stdout)
    ) == seal.VERDICT_PASS


def test_structured_verdict_ignores_unknown_and_non_json():
    """Unknown verdicts / non-JSON stdout fall back to the exit code."""
    # Unknown verdict -> not structured -> exit code drives the verdict.
    assert seal._structured_verdict('{"verdict": "UNKNOWN"}') is None
    assert seal._structured_verdict("not json at all") is None
    assert seal._structured_verdict("") is None
    # A non-object payload -> not structured.
    assert seal._structured_verdict("[1, 2, 3]") is None


# --- run: witness + verify wired in ------------------------------------------


def test_run_runs_all_five_stages_in_order():
    """Seal.run runs sign, name, repro, witness, verify in provenance order."""
    r = _repro_runner(repro="images/repro-report.json")
    result = seal.run_seal(BUILD, "seed", r, plan=_plan(r))
    assert result.signed == ["images/rootfs.ext4", "images/bzImage"]
    assert result.sbom_symlinked == ("images/m2.spdx.json", "sbom.spdx.json")
    assert result.repro is not None
    assert result.witness is not None
    assert result.witness.status == seal.WITNESS_BUILT
    assert result.verifier is not None
    assert result.verifier.verdict == seal.VERDICT_PASS
    # Provenance order: two signs, then witness, then verifier.
    ordered = [c[0] for c in r.commands]
    assert ordered == [
        "pengyos-sign", "pengyos-sign",
        "pengyos-witness-bundle", "pengyos-verifier",
    ]


def test_run_verifier_failure_returns_its_exit_code():
    """A failing verifier makes the whole run return the verifier's exit code."""
    r = _repro_runner(repro="images/repro-report.json", verify_rc=2)
    result = seal.run_seal(BUILD, "seed", r, plan=_plan(r))
    assert result.verifier.exit_code == 2
    assert result.verifier.verdict == seal.VERDICT_FAIL


# --- cmd_run: witness + verify wiring ----------------------------------------


def test_cmd_run_surfaces_witness_and_verifier(capsys):
    """``cmd_run`` prints the witness + verifier verdict lines."""
    r = _repro_runner(repro="images/repro-report.json")
    rc = cli.cmd_run(_run_args(), r=r)
    assert rc == cli.EXIT_OK  # verifier PASS -> exit 0
    out = capsys.readouterr().out
    assert "witness built the layout" in out
    assert "verifier PASS" in out


def test_cmd_run_returns_verifier_exit_code(capsys):
    """``cmd_run`` returns the verifier's exit code (its verdict's code)."""
    r = _repro_runner(repro="images/repro-report.json", verify_rc=2)
    rc = cli.cmd_run(_run_args(), r=r)
    assert rc == 2  # the verifier's exit code, not a mapped 1
    out = capsys.readouterr().out
    assert "verifier FAIL" in out


def test_cmd_run_witness_failure_maps_to_clean_error(capsys):
    """A failing witness is a SealError (mapped by main to a clean error)."""
    r = _witness_runner(witness_rc=1, witness_stderr="boom")
    with pytest.raises(cli.SealError) as exc:
        cli.cmd_run(_run_args(), r=r)
    assert "pengyos-witness-bundle failed" in str(exc.value)
    assert "boom" in str(exc.value)
