"""Tests for pengyos_seal.plan (the deterministic seal plan model).

Covers discovery (the witness-bundle alias convention), the ordered step model
and its argv, the deterministic repro status, and the text/JSON rendering. Every
test injects a :class:`MemoryRunner` so discovery runs against a virtual build
tree — no real files, network, or root.
"""

from __future__ import annotations

import json

from pengyos_seal import cli, plan
from pengyos_seal.plan import (
    KEY_PLACEHOLDER,
    REPRO_DIFF,
    REPRO_EXISTING,
    REPRO_SKIP,
    SealPlan,
    build_plan,
    discover_repro,
    discover_sbom,
    target_from_sbom,
)
from pengyos_seal.runner import MemoryRunner

P = "."


def _full_build(r: MemoryRunner) -> None:
    """Seed a runner with a complete M1-style build tree under ``.``."""
    r.seed_dir(P)
    r.seed_file("images/rootfs.ext4", b"rootfs")
    r.seed_file("images/bzImage", b"bz")
    r.seed_file("images/m2.spdx.json", b'{"spdx": "2.3"}')


def _runner(*, full: bool = True) -> MemoryRunner:
    r = MemoryRunner()
    if full:
        _full_build(r)
    else:
        r.seed_dir(P)
    return r


# --- discovery --------------------------------------------------------------


def test_discover_sbom_finds_target_specific_path():
    r = MemoryRunner()
    _full_build(r)
    assert discover_sbom(P, r) == "images/m2.spdx.json"


def test_discover_sbom_none_when_absent():
    assert discover_sbom(P, _runner(full=False)) is None


def test_target_from_sbom_strips_spdx_json_suffix():
    # Not os.path.splitext (which would leave "m2.spdx"): strip the full suffix.
    assert target_from_sbom("images/m2.spdx.json") == "m2"
    assert target_from_sbom("images/pengyos-x86_64.spdx.json") == "pengyos-x86_64"
    # A path without the SPDX suffix yields nothing (reporting only).
    assert target_from_sbom("images/m2.cdx.json") is None


def test_discover_sbom_prefers_most_specific_alias():
    """``images/*.spdx.json`` (index 0) beats the broad ``*.spdx.json`` (index 2)."""
    r = MemoryRunner()
    r.seed_dir(P)
    r.seed_file("top.spdx.json", b"{}")  # matches only the broad alias
    r.seed_file("images/m2.spdx.json", b"{}")  # matches the specific alias
    assert discover_sbom(P, r) == "images/m2.spdx.json"


def test_discover_sbom_earliest_path_wins_on_tie():
    """Two files on the same lowest-index alias: the earliest sorted path wins."""
    r = MemoryRunner()
    r.seed_dir(P)
    r.seed_file("images/b.spdx.json", b"{}")
    r.seed_file("images/a.spdx.json", b"{}")
    assert discover_sbom(P, r) == "images/a.spdx.json"


def test_discover_repro_root_and_images():
    r = MemoryRunner()
    r.seed_dir(P)
    r.seed_file("repro-report.json", b"{}")
    assert discover_repro(P, r) == "repro-report.json"

    r2 = MemoryRunner()
    r2.seed_dir(P)
    r2.seed_file("images/repro-report.json", b"{}")
    assert discover_repro(P, r2) == "images/repro-report.json"


def test_discover_repro_none_when_absent():
    assert discover_repro(P, _runner(full=False)) is None


# --- build_plan: ordering ---------------------------------------------------


def test_build_plan_step_order():
    r = MemoryRunner()
    _full_build(r)
    p = build_plan(P, runner=r)
    kinds = [step.kind for step in p.steps]
    assert kinds == ["sign", "sign", "name", "repro", "witness", "verify"]


def test_build_plan_targets_and_sources():
    r = MemoryRunner()
    _full_build(r)
    p = build_plan(P, runner=r)
    assert p.target == "m2"
    assert p.sbom_source == "images/m2.spdx.json"


def test_build_plan_no_sbom_leaves_target_and_sbom_none():
    p = build_plan(P, runner=_runner(full=False))
    assert p.target is None
    assert p.sbom_source is None
    # No SBOM => no name step.
    assert "name" not in (s.kind for s in p.steps)


# --- build_plan: signing argv -----------------------------------------------


def test_build_plan_sign_argv_golden():
    r = MemoryRunner()
    _full_build(r)
    p = build_plan(P, runner=r)
    sign_steps = [s for s in p.steps if s.kind == "sign"]
    assert len(sign_steps) == 2
    expected = [
        (
            "pengyos-sign", "sign", "images/rootfs.ext4",
            "--key", KEY_PLACEHOLDER, "--sig", "images/rootfs.ext4.sig",
        ),
        (
            "pengyos-sign", "sign", "images/bzImage",
            "--key", KEY_PLACEHOLDER, "--sig", "images/bzImage.sig",
        ),
    ]
    assert [s.argv for s in sign_steps] == expected


def test_build_plan_sign_argv_uses_real_key_when_given():
    r = MemoryRunner()
    _full_build(r)
    p = build_plan(P, key="s3cr3t", runner=r)
    sign_steps = [s for s in p.steps if s.kind == "sign"]
    assert sign_steps[0].argv[4] == "s3cr3t"  # the value after "--key"


def test_build_plan_name_step_has_no_tool_argv():
    """The name step is a filesystem symlink, so it carries no external argv."""
    r = MemoryRunner()
    _full_build(r)
    p = build_plan(P, runner=r)
    name_step = next(s for s in p.steps if s.kind == "name")
    assert name_step.argv is None
    assert "sbom.spdx.json" in name_step.detail


# --- build_plan: repro status -----------------------------------------------


def test_repro_status_existing():
    r = MemoryRunner()
    _full_build(r)
    # Add a repro report to the tree.
    r.seed_file("images/repro-report.json", b"{}")
    p = build_plan(P, runner=r)
    assert p.repro_status == REPRO_EXISTING
    assert p.repro_source == "images/repro-report.json"
    repro_step = next(s for s in p.steps if s.kind == "repro")
    assert repro_step.argv is None  # already present, no diff to run


def test_repro_status_diff_with_prev():
    r = MemoryRunner()
    _full_build(r)
    p = build_plan(P, prev="/prev/build", runner=r)
    assert p.repro_status == REPRO_DIFF
    repro_step = next(s for s in p.steps if s.kind == "repro")
    assert repro_step.argv == (
        "pengyos-repro-report", "diff", "--a", P, "--b", "/prev/build",
    )


def test_repro_argv_carries_the_diff_command():
    """The plan carries the diff argv so the repro stage runs the exact argv."""
    r = MemoryRunner()
    _full_build(r)
    p = build_plan(P, prev="/prev/build", runner=r)
    assert p.repro_argv == (
        "pengyos-repro-report", "diff", "--a", P, "--b", "/prev/build",
    )


def test_repro_argv_none_when_not_diff():
    """No diff argv for an existing report or a skip (no diff to run)."""
    r = MemoryRunner()
    _full_build(r)
    r.seed_file("images/repro-report.json", b"{}")
    assert build_plan(P, runner=r).repro_argv is None
    assert build_plan(P, runner=_runner(full=False)).repro_argv is None


def test_repro_status_skip_without_prev_or_report():
    p = build_plan(P, runner=_runner(full=False))
    assert p.repro_status == REPRO_SKIP


# --- build_plan: witness + verify argv --------------------------------------


def test_witness_argv_includes_pub_when_given():
    r = MemoryRunner()
    _full_build(r)
    p = build_plan(P, pub="k.pub", runner=r)
    witness = next(s for s in p.steps if s.kind == "witness")
    assert witness.argv == ("pengyos-witness-bundle", "run", "--build", P, "--pub", "k.pub")


def test_witness_argv_without_pub():
    r = MemoryRunner()
    _full_build(r)
    p = build_plan(P, runner=r)
    witness = next(s for s in p.steps if s.kind == "witness")
    assert witness.argv == ("pengyos-witness-bundle", "run", "--build", P)


def test_verify_argv_includes_attest_and_pub():
    r = MemoryRunner()
    _full_build(r)
    p = build_plan(P, attest="a.log", pub="k.pub", runner=r)
    verify = next(s for s in p.steps if s.kind == "verify")
    assert verify.argv == (
        "pengyos-verifier", "check", "--build", P, "--attest", "a.log", "--pub", "k.pub",
    )


def test_verify_argv_without_optional_flags():
    r = MemoryRunner()
    _full_build(r)
    p = build_plan(P, runner=r)
    verify = next(s for s in p.steps if s.kind == "verify")
    assert verify.argv == ("pengyos-verifier", "check", "--build", P)


# --- determinism ------------------------------------------------------------


def test_build_plan_is_deterministic():
    r1 = MemoryRunner()
    r2 = MemoryRunner()
    _full_build(r1)
    _full_build(r2)
    assert build_plan(P, runner=r1).to_json() == build_plan(P, runner=r2).to_json()


# --- rendering --------------------------------------------------------------


def test_render_lists_every_step():
    r = MemoryRunner()
    _full_build(r)
    rendered = build_plan(P, runner=r).render()
    for marker in ("sign:", "name:", "repro:", "witness:", "verify:"):
        assert marker in rendered
    # The argv line is quoted and copy-pasteable.
    assert "pengyos-sign sign images/bzImage" in rendered


def test_to_json_is_valid_and_structured():
    r = MemoryRunner()
    _full_build(r)
    data = build_plan(P, runner=r).to_dict()
    assert data["target"] == "m2"
    assert [s["kind"] for s in data["steps"]] == [
        "sign", "sign", "name", "repro", "witness", "verify",
    ]
    assert data["steps"][0]["argv"][0] == "pengyos-sign"
    # Round-trips through JSON.
    json.loads(build_plan(P, runner=r).to_json())


def test_json_has_no_side_effect_fields():
    r = MemoryRunner()
    _full_build(r)
    data = build_plan(P, runner=r).to_dict()
    assert set(data) == {"build_dir", "target", "sbom_source", "repro_source",
                         "repro_status", "steps"}


# --- cmd_plan (CLI) ---------------------------------------------------------


def test_cmd_plan_text(capsys):
    r = MemoryRunner()
    _full_build(r)
    import argparse
    args = argparse.Namespace(build=P, json=False)
    rc = cli.cmd_plan(args, r=r)
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "seal plan for ." in out
    assert "images/m2.spdx.json" in out


def test_cmd_plan_json(capsys):
    r = MemoryRunner()
    _full_build(r)
    import argparse
    args = argparse.Namespace(build=P, json=True)
    rc = cli.cmd_plan(args, r=r)
    assert rc == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["target"] == "m2"
    assert len(data["steps"]) == 6


def test_cmd_plan_missing_build_dir_raises():
    import argparse
    r = MemoryRunner()  # "." is seeded in __post_init__, but not this path
    args = argparse.Namespace(build="/nope/missing", json=False)
    try:
        cli.cmd_plan(args, r=r)
    except cli.SealError as exc:
        assert "not a directory" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected a SealError for a missing build dir")
