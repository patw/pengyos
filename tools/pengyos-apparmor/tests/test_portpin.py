"""Tests for the port-pin transition planner (``pengyos_apparmor.portpin``).

The follow-up "pin the local model server's port" is
blocked until PengyOS defines the model server's default port (the port
must not be guessed). This file pins the planner that turns the pinned
mechanical recipe into a tested, dry-run transition: exact rule/directive
strings, fail-loud preconditions on the profile text, strict-clean
verification of the transformed profile, and the ``plan-port-pin`` CLI
end to end. All checks run on in-memory fixtures and ``tmp_path`` files —
no network, no root, no AppArmor tooling.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pengyos_apparmor import portpin, validator
from pengyos_apparmor.cli import main

ROOT = Path(__file__).resolve().parent.parent
COMMITTED_AGENT = ROOT / "profiles" / "pengy-agent"

# The port used as a fixture throughout: it is NOT asserted to be the
# model server's port (PengyOS has not defined one); it is only a
# well-formed input to the planner.
PORT = 11434


def _fixture(indent: str = "  ", grant: str = portpin.UNBOUND_MODEL_SERVER_RULE + ",") -> str:
    """A minimal pengy-agent-shaped profile carrying the unbound grant."""
    return (
        "pengy-agent (pengy-agent.bin) {\n"
        f"{indent}{grant}\n"
        "}\n"
    )


# ---------------------------------------------------------------------------
# Module contract / lockstep
# ---------------------------------------------------------------------------


def test_portpin_public_api():
    for attr in (
        "MODEL_SERVER_PEER_IP",
        "UNBOUND_MODEL_SERVER_RULE",
        "REMAINING_MANUAL_STEPS",
        "PortPinTransitionError",
        "PortPinPlan",
        "normalize_port",
        "port_pinned_rule",
        "sanction_directive",
        "plan_port_pin",
        "apply_port_pin",
        "verify_port_pin",
        "PORT_PIN_TRANSITION_FILES",
        "render_transition_patch",
    ):
        assert hasattr(portpin, attr), f"portpin.{attr} missing"


def test_unbound_rule_is_the_sanctioned_and_committed_grant():
    # Lockstep: the planner's old rule is exactly the sole entry of
    # SANCTIONED_NETWORK_RULES and the exact line the committed
    # profiles/pengy-agent carries — so the planner cannot drift from
    # the committed profile or the sanction set.
    assert portpin.UNBOUND_MODEL_SERVER_RULE in validator.SANCTIONED_NETWORK_RULES
    assert sorted(validator.SANCTIONED_NETWORK_RULES) == [portpin.UNBOUND_MODEL_SERVER_RULE]
    lines = [l.strip() for l in COMMITTED_AGENT.read_text(encoding="utf-8").splitlines()]
    assert portpin.UNBOUND_MODEL_SERVER_RULE + "," in lines


def test_remaining_manual_steps_cover_the_recipe():
    # The dry run must never pretend the transition is complete: the
    # sanctioned-set lockstep entry, the test pins, and the docs are
    # still reviewed edits (the iteration-10 recipe, pinned verbatim-ish).
    steps = "\n".join(portpin.REMAINING_MANUAL_STEPS)
    assert "SANCTIONED_NETWORK_RULES" in steps
    assert "test_network_grants" in steps
    assert "test_committed_grant_is_the_unbound_port_form" in steps
    assert "CHANGELOG" in steps
    assert "validate --strict" in steps


# ---------------------------------------------------------------------------
# Port normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [(11434, 11434), ("11434", 11434), (1, 1), (65535, 65535), (" 8080 ", 8080)],
)
def test_normalize_port_accepts(raw, expected):
    assert portpin.normalize_port(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [0, -1, 65536, "0", "65536", "011434", "abc", "-1", "+5", "1.5", "1e4", "", " ", True, False, 1.5, None],
)
def test_normalize_port_rejects(raw):
    with pytest.raises(portpin.PortPinTransitionError):
        portpin.normalize_port(raw)


# ---------------------------------------------------------------------------
# Plan rendering
# ---------------------------------------------------------------------------


def test_port_pinned_rule_exact_shape():
    # The exact apparmor.d(5) string, including the space inside
    # peer=(…) — the verbatim sanction match depends on it.
    assert portpin.port_pinned_rule(11434) == (
        "network inet tcp peer=(ip=127.0.0.1 port=11434)"
    )
    assert portpin.port_pinned_rule("11434") == (
        "network inet tcp peer=(ip=127.0.0.1 port=11434)"
    )
    assert portpin.port_pinned_rule(1) == "network inet tcp peer=(ip=127.0.0.1 port=1)"
    assert portpin.port_pinned_rule(65535) == (
        "network inet tcp peer=(ip=127.0.0.1 port=65535)"
    )


def test_sanitize_directive_uses_the_sanction_prefix():
    assert portpin.sanction_directive(11434) == (
        validator.SANCTION_DIRECTIVE + "network inet tcp peer=(ip=127.0.0.1 port=11434)"
    )
    assert portpin.sanction_directive(11434).startswith("# sanctioned: ")


def test_plan_data():
    plan = portpin.plan_port_pin(PORT)
    assert plan.port == PORT
    assert plan.profile == "pengy-agent"
    assert plan.old_rule == portpin.UNBOUND_MODEL_SERVER_RULE
    assert plan.new_rule == (
        "network inet tcp peer=(ip=127.0.0.1 port=11434)"
    )
    assert plan.directive == (
        "# sanctioned: network inet tcp peer=(ip=127.0.0.1 port=11434)"
    )
    # The plan's new rule is NOT yet in the global sanction set: shipping
    # the pin without the lockstep update would be caught.
    assert plan.new_rule not in validator.SANCTIONED_NETWORK_RULES


def test_plan_profile_name_overridable():
    plan = portpin.plan_port_pin(PORT, profile="other")
    assert plan.profile == "other"


# ---------------------------------------------------------------------------
# apply_port_pin — the pure text transformation
# ---------------------------------------------------------------------------


def test_apply_replaces_rule_and_inserts_directive():
    text = _fixture()
    plan = portpin.plan_port_pin(PORT)
    out = portpin.apply_port_pin(text, plan)
    assert out == (
        "pengy-agent (pengy-agent.bin) {\n"
        "  # sanctioned: network inet tcp peer=(ip=127.0.0.1 port=11434)\n"
        "  network inet tcp peer=(ip=127.0.0.1 port=11434),\n"
        "}\n"
    )


def test_apply_preserves_custom_indentation():
    text = _fixture(indent="    ")
    out = portpin.apply_port_pin(text, portpin.plan_port_pin(PORT))
    lines = out.splitlines()
    assert lines[1] == "    # sanctioned: network inet tcp peer=(ip=127.0.0.1 port=11434)"
    assert lines[2] == "    network inet tcp peer=(ip=127.0.0.1 port=11434),"


def test_apply_changes_exactly_two_lines_of_a_real_profile():
    # Against the committed profile: the unbound rule line is replaced and
    # exactly one directive line is inserted — nothing else moves.
    text = COMMITTED_AGENT.read_text(encoding="utf-8")
    plan = portpin.plan_port_pin(PORT, profile="pengy-agent")
    out = portpin.apply_port_pin(text, plan)
    old_lines = text.splitlines()
    new_lines = out.splitlines()
    assert len(new_lines) == len(old_lines) + 1
    inserted = [l for l in new_lines if l.strip() == plan.directive]
    assert len(inserted) == 1
    replaced = [l for l in new_lines if l.strip() == plan.new_rule + ","]
    assert len(replaced) == 1
    assert portpin.UNBOUND_MODEL_SERVER_RULE + "," not in [l.strip() for l in new_lines]
    # The directive sits directly above the pinned rule.
    idx = new_lines.index(inserted[0])
    assert new_lines[idx + 1] == replaced[0]


def test_apply_result_is_strict_clean():
    text = _fixture()
    plan = portpin.plan_port_pin(PORT)
    report = portpin.verify_port_pin(text, plan)
    assert report.ok
    assert report.issues == []


def test_apply_result_is_strict_clean_for_committed_profile():
    text = COMMITTED_AGENT.read_text(encoding="utf-8")
    report = portpin.verify_port_pin(text, portpin.plan_port_pin(PORT, profile="pengy-agent"))
    assert report.ok
    assert report.issues == []


def test_apply_repairs_a_partially_done_transition_without_duplicating():
    # A partially-done transition leaves the new directive above the
    # still-unbound rule (an invalid intermediate state — the directive
    # matches no carried rule until the pin lands). apply must complete
    # it without duplicating the directive, and the result is clean.
    text = (
        "pengy-agent (pengy-agent.bin) {\n"
        "  # sanctioned: network inet tcp peer=(ip=127.0.0.1 port=11434)\n"
        "  network inet tcp peer=(ip=127.0.0.1),\n"
        "}\n"
    )
    plan = portpin.plan_port_pin(PORT)
    out = portpin.apply_port_pin(text, plan)
    assert out.count(plan.directive) == 1
    report = portpin.verify_port_pin(text, plan)
    assert report.issues == []


def test_apply_is_not_idempotent_on_a_pinned_profile():
    # A completed pin is NOT re-pinnable: applying again fails loudly
    # (the profile already pins a port), so a double transition cannot
    # silently stack.
    text = _fixture()
    plan = portpin.plan_port_pin(PORT)
    first = portpin.apply_port_pin(text, plan)
    with pytest.raises(portpin.PortPinTransitionError, match="already pins"):
        portpin.apply_port_pin(first, plan)


def test_apply_raises_when_grant_missing():
    text = "pengy-agent (pengy-agent.bin) {\n  /etc/hostname r,\n}\n"
    with pytest.raises(portpin.PortPinTransitionError, match="does not carry"):
        portpin.apply_port_pin(text, portpin.plan_port_pin(PORT))


def test_apply_raises_when_already_pinned():
    for grant in (
        "network inet tcp peer=(ip=127.0.0.1 port=9999),",
        "network inet tcp peer=(ip=127.0.0.1 port=11434),",
    ):
        text = _fixture(grant=grant)
        with pytest.raises(portpin.PortPinTransitionError, match="already pins"):
            portpin.apply_port_pin(text, portpin.plan_port_pin(PORT))


def test_apply_raises_when_grant_ambiguous():
    text = (
        "pengy-agent (pengy-agent.bin) {\n"
        "  network inet tcp peer=(ip=127.0.0.1),\n"
        "  network inet tcp peer=(ip=127.0.0.1),\n"
        "}\n"
    )
    with pytest.raises(portpin.PortPinTransitionError, match="ambig|times"):
        portpin.apply_port_pin(text, portpin.plan_port_pin(PORT))


def test_verify_raises_on_bad_profile_state():
    text = "pengy-agent (pengy-agent.bin) {\n}\n"
    with pytest.raises(portpin.PortPinTransitionError):
        portpin.verify_port_pin(text, portpin.plan_port_pin(PORT))


# ---------------------------------------------------------------------------
# CLI: plan-port-pin (end to end)
# ---------------------------------------------------------------------------


def _cli(args, capsys):
    try:
        code = main(args)
    except SystemExit as exc:
        # argparse type errors (bad port) surface as SystemExit(2).
        code = exc.code if isinstance(exc.code, int) else 1
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_plan_port_pin_strict_clean_exit_zero(tmp_path, capsys):
    p = tmp_path / "pengy-agent"
    p.write_text(_fixture(), encoding="utf-8")
    code, out, err = _cli(["plan-port-pin", str(PORT), str(p)], capsys)
    assert code == 0
    assert err == ""
    assert "port-pin transition plan for pengy-agent (port 11434)" in out
    assert "  old rule: network inet tcp peer=(ip=127.0.0.1)," in out
    assert "  new rule: network inet tcp peer=(ip=127.0.0.1 port=11434)," in out
    assert "  new line: # sanctioned: network inet tcp peer=(ip=127.0.0.1 port=11434)" in out
    assert "dry run" in out
    assert "SANCTIONED_NETWORK_RULES" in out
    assert "pengy-agent: OK (strict-clean)" in out
    # Dry run: the file on disk is untouched.
    assert p.read_text(encoding="utf-8") == _fixture()


def test_cli_plan_port_pin_committed_profile_is_strict_clean(capsys):
    code, out, _err = _cli(["plan-port-pin", "11434", str(COMMITTED_AGENT)], capsys)
    assert code == 0
    assert "pengy-agent: OK (strict-clean)" in out
    # Still a dry run: the committed file still carries the unbound form.
    committed = COMMITTED_AGENT.read_text(encoding="utf-8")
    assert portpin.UNBOUND_MODEL_SERVER_RULE + "," in [
        l.strip() for l in committed.splitlines()
    ]
    assert "port=" not in [
        l for l in committed.splitlines() if l.strip().startswith("network")
    ][0]


def test_cli_plan_port_pin_default_profile_is_committed_agent(tmp_path, capsys, monkeypatch):
    # No profile argument: defaults to profiles/pengy-agent (relative to
    # the CWD) — run it from a tree that contains the committed profile.
    monkeypatch.chdir(ROOT)
    code, out, _err = _cli(["plan-port-pin", "11434"], capsys)
    assert code == 0
    assert "port-pin transition plan for pengy-agent (port 11434)" in out
    assert "pengy-agent: OK (strict-clean)" in out


@pytest.mark.parametrize(
    "bad_port",
    ["0", "65536", "011434", "abc", "-1"],
)
def test_cli_plan_port_pin_bad_port_exits_two(tmp_path, capsys, bad_port):
    p = tmp_path / "pengy-agent"
    p.write_text(_fixture(), encoding="utf-8")
    code, _out, err = _cli(["plan-port-pin", bad_port, str(p)], capsys)
    assert code == 2
    assert "invalid value" in err or "port" in err


def test_cli_plan_port_pin_missing_file_exits_two(tmp_path, capsys):
    code, _out, err = _cli(
        ["plan-port-pin", str(PORT), str(tmp_path / "nope")], capsys
    )
    assert code == 2
    assert "no such file or directory" in err


def test_cli_plan_port_pin_profile_without_grant_exits_two(tmp_path, capsys):
    p = tmp_path / "sshd"
    p.write_text("sshd (sshd.bin) {\n  /etc/hostname r,\n}\n", encoding="utf-8")
    code, _out, err = _cli(["plan-port-pin", str(PORT), str(p)], capsys)
    assert code == 2
    assert "does not carry" in err


def test_cli_plan_port_pin_already_pinned_exits_two(tmp_path, capsys):
    p = tmp_path / "pengy-agent"
    p.write_text(
        _fixture(grant="network inet tcp peer=(ip=127.0.0.1 port=11434),"),
        encoding="utf-8",
    )
    code, _out, err = _cli(["plan-port-pin", str(PORT), str(p)], capsys)
    assert code == 2
    assert "already pins" in err


def test_cli_help_advertises_plan_port_pin(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "plan-port-pin" in out
    assert "validate" in out


# ---------------------------------------------------------------------------
# render_transition_patch — the complete mechanical transition diff
# ---------------------------------------------------------------------------


def _copy_repo_files(tmp_path, mutate=None):
    """Copy the four transition files into *tmp_path*, preserving their
    relative layout (the patch paths are relative to the tool root)."""
    for rel in portpin.PORT_PIN_TRANSITION_FILES:
        src = ROOT / rel
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    if mutate:
        mutate(tmp_path)
    return tmp_path


def _drop_line(root: Path, rel: str, needle: str):
    path = root / rel
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [l for l in lines if needle not in l]
    assert len(kept) < len(lines), f"anchor {needle!r} not found in {rel}"
    path.write_text("".join(kept), encoding="utf-8")


def _patch_body_lines(patch: str):
    """(added, removed) line lists with the unified-diff markers stripped;
    headers, hunk lines, and context are excluded."""
    added, removed = [], []
    for line in patch.splitlines():
        if line.startswith("--- ") or line.startswith("+++ ") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def test_transition_files_are_exactly_the_four_repo_files():
    assert portpin.PORT_PIN_TRANSITION_FILES == (
        "profiles/pengy-agent",
        "pengyos_apparmor/validator.py",
        "tests/test_profiles.py",
        "tests/test_validator.py",
    )
    for rel in portpin.PORT_PIN_TRANSITION_FILES:
        assert (ROOT / rel).is_file(), f"transition file missing: {rel}"


def test_rendered_patch_is_well_formed_unified_diff_over_the_four_files():
    patch = portpin.render_transition_patch(PORT, ROOT)
    lines = patch.splitlines()
    for rel in portpin.PORT_PIN_TRANSITION_FILES:
        assert f"--- a/{rel}" in lines
        assert f"+++ b/{rel}" in lines
        # The file headers come in pairs, in order.
        a_idx = lines.index(f"--- a/{rel}")
        b_idx = lines.index(f"+++ b/{rel}")
        assert b_idx == a_idx + 1
    order = [lines.index(f"--- a/{rel}") for rel in portpin.PORT_PIN_TRANSITION_FILES]
    assert order == sorted(order)
    # Every line is a header, a hunk line, or a diff line.
    for line in lines:
        assert line.startswith(("--- ", "+++ ", "@@", "+", "-", " ")), line
    added, removed = _patch_body_lines(patch)
    # Profile: 1 rule removed, 2 lines added (rule + directive);
    # validator: 1/1; the two test pins: 1/1 and 2/2 lines.
    assert len(removed) == 5
    assert len(added) == 6


def test_rendered_patch_contains_exact_old_and_new_lines():
    patch = portpin.render_transition_patch(PORT, ROOT)
    added, removed = _patch_body_lines(patch)
    pinned = portpin.port_pinned_rule(PORT)
    # The four old anchors (verbatim lines of the current repo).
    assert f"  {portpin.UNBOUND_MODEL_SERVER_RULE}," in removed
    assert f'        "{portpin.UNBOUND_MODEL_SERVER_RULE}",' in removed
    assert (
        f'    expected = {{"pengy-agent": ["{portpin.UNBOUND_MODEL_SERVER_RULE}"]}}'
        in removed
    )
    assert "    rule = _model_server_rule()" in removed
    assert '    assert "port=" not in rule' in removed
    # And the exact replacements the transition must land on.
    assert f"  {pinned}," in added
    assert f"  {validator.SANCTION_DIRECTIVE}{pinned}" in added
    assert f'        "{pinned}",' in added
    assert f'    expected = {{"pengy-agent": ["{pinned}"]}}' in added
    assert f'    rule = _model_server_rule("{PORT}")' in added
    assert f'    assert f"port={PORT}" in rule' in added


def test_rendered_patch_removed_lines_exist_verbatim_in_the_repo():
    # Drift guard, checked against the live repo: every line the patch
    # claims to remove is an exact current line of its file — otherwise
    # `git apply` would fail.
    patch = portpin.render_transition_patch(PORT, ROOT)
    current = {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in portpin.PORT_PIN_TRANSITION_FILES}
    rel = None
    for line in patch.splitlines():
        if line.startswith("--- a/"):
            rel = line[len("--- a/"):]
        elif line.startswith("-") and not line.startswith("--- "):
            assert line[1:] in current[rel].splitlines(), (
                f"{rel}: patch removes a line that is not there: {line[1:]!r}"
            )


def test_rendered_patch_applies_cleanly_with_git_and_leaves_the_transition_state(
    tmp_path,
):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    _copy_repo_files(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    patch = portpin.render_transition_patch(PORT, ROOT)
    # --check: the patch applies to the copied current content...
    subprocess.run(
        ["git", "apply", "--check", "-"],
        input=patch,
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    # ...and after applying, the repo is in the exact post-transition state.
    subprocess.run(
        ["git", "apply", "-"],
        input=patch,
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    pinned = portpin.port_pinned_rule(PORT)

    profile = (tmp_path / "profiles/pengy-agent").read_text(encoding="utf-8")
    assert f"  {pinned}," in profile.splitlines()
    assert f"  {validator.SANCTION_DIRECTIVE}{pinned}" in profile.splitlines()
    report = validator.validate_text(profile, "pengy-agent")
    assert report.issues == []

    code = (tmp_path / "pengyos_apparmor/validator.py").read_text(encoding="utf-8")
    assert f'"{pinned}",' in code
    assert f'"{portpin.UNBOUND_MODEL_SERVER_RULE}",' not in code

    tp = (tmp_path / "tests/test_profiles.py").read_text(encoding="utf-8")
    assert f'expected = {{"pengy-agent": ["{pinned}"]}}' in tp

    tv = (tmp_path / "tests/test_validator.py").read_text(encoding="utf-8")
    assert f'rule = _model_server_rule("{PORT}")' in tv
    assert f'assert f"port={PORT}" in rule' in tv
    assert 'assert "port=" not in rule' not in tv


@pytest.mark.parametrize(
    "drift",
    [
        ("profiles/pengy-agent", "network inet tcp peer=(ip=127.0.0.1)"),
        ("pengyos_apparmor/validator.py", '"network inet tcp peer=(ip=127.0.0.1)",'),
        ("tests/test_profiles.py", "expected = {\"pengy-agent\""),
        ("tests/test_validator.py", "rule = _model_server_rule()"),
    ],
    ids=["profile-grant", "sanction-entry", "grants-pin", "unbound-anchor"],
)
@pytest.mark.parametrize("duplicate", [False, True], ids=["missing", "duplicated"])
def test_render_raises_loudly_on_drift(tmp_path, drift, duplicate):
    rel, needle = drift

    def mutate(root):
        path = root / rel
        text = path.read_text(encoding="utf-8")
        if duplicate:
            lines = text.splitlines(keepends=True)
            idx = next(i for i, l in enumerate(lines) if needle in l)
            lines.insert(idx + 1, lines[idx])
            path.write_text("".join(lines), encoding="utf-8")
        else:
            _drop_line(root, rel, needle)

    _copy_repo_files(tmp_path, mutate)
    with pytest.raises(portpin.PortPinTransitionError, match=rel):
        portpin.render_transition_patch(PORT, tmp_path)


def test_render_raises_when_a_transition_file_is_missing(tmp_path):
    def mutate(root):
        (root / "tests/test_validator.py").unlink()

    _copy_repo_files(tmp_path, mutate)
    with pytest.raises(portpin.PortPinTransitionError, match="tests/test_validator.py"):
        portpin.render_transition_patch(PORT, tmp_path)


def test_render_raises_on_bad_port():
    with pytest.raises(portpin.PortPinTransitionError):
        portpin.render_transition_patch(0, ROOT)


# ---------------------------------------------------------------------------
# CLI: plan-port-pin --patch (end to end)
# ---------------------------------------------------------------------------


def test_cli_patch_flag_prints_full_diff_and_modifies_nothing(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.chdir(ROOT)
    before = {
        rel: (ROOT / rel).read_bytes() for rel in portpin.PORT_PIN_TRANSITION_FILES
    }
    code, out, err = _cli(["plan-port-pin", str(PORT), "--patch"], capsys)
    assert code == 0
    assert err == ""
    assert "mechanical transition diff (review it, then git apply):" in out
    for rel in portpin.PORT_PIN_TRANSITION_FILES:
        assert f"--- a/{rel}" in out
        assert f"+++ b/{rel}" in out
    assert f'        "{portpin.port_pinned_rule(PORT)}",' in out
    # Dry run: all four files are byte-identical.
    for rel in portpin.PORT_PIN_TRANSITION_FILES:
        assert (ROOT / rel).read_bytes() == before[rel]


def test_cli_patch_flag_bad_port_exits_two(tmp_path, capsys):
    code, _out, err = _cli(["plan-port-pin", "0", "--patch"], capsys)
    assert code == 2
    assert "port" in err


def test_cli_patch_flag_drift_exits_two(tmp_path, monkeypatch, capsys):
    # The profile is intact (the plan itself succeeds) but the validator
    # sanction entry is gone: rendering must fail loudly with the file.
    _copy_repo_files(tmp_path, lambda r: _drop_line(r, "pengyos_apparmor/validator.py", "network inet tcp peer"))
    monkeypatch.chdir(tmp_path)
    code, _out, err = _cli(["plan-port-pin", str(PORT), "--patch"], capsys)
    assert code == 2
    assert "validator.py" in err
    assert "drifted" in err

