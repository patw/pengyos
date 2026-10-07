"""Tests for the CLI wiring (spec: Interface).

The engine logic lives in later units; here we verify the argparse surface, the
dispatch, the ``--version``/help behaviour, and that the scaffolded command
entry points raise a clean, testable sentinel.
"""

from __future__ import annotations

import io

import pytest

from pengyos_install import __version__, cli
from pengyos_install.runner import FakeRunner


def run(argv, *, out=None, err=None, runner=None):
    buf = io.StringIO() if out is None else out
    errbuf = io.StringIO() if err is None else err
    code = cli.main(argv, out=buf, err=errbuf, runner=runner)
    return code, buf.getvalue(), errbuf.getvalue()


def _subcommand_names(parser) -> set[str]:
    return {
        choice
        for action in parser._actions
        if isinstance(getattr(action, "choices", None), dict)
        for choice in action.choices
    }


def test_version():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0


def test_requires_a_command():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0


def test_build_parser_has_all_subcommands():
    sub = _subcommand_names(cli.build_parser())
    assert {"plan", "install", "provision-plan"} <= sub


def test_plan_requires_release_and_layout():
    with pytest.raises(SystemExit) as exc:
        cli.main(["plan"])
    assert exc.value.code != 0


def test_install_requires_release_and_layout():
    with pytest.raises(SystemExit) as exc:
        cli.main(["install"])
    assert exc.value.code != 0


def test_plan_dispatch_renders(tmp_path, release_json, layout_json):
    # plan is now implemented: it loads the inputs and renders the plan.
    rel = tmp_path / "rel.json"
    lay = tmp_path / "layout.json"
    rel.write_text(release_json, encoding="utf-8")
    lay.write_text(layout_json, encoding="utf-8")
    code, out, err = run(
        ["plan", "--release", str(rel), "--layout", str(lay)]
    )
    assert code == 0
    assert out.startswith("#!/usr/bin/env bash")


def test_install_dispatch_refuses_without_yes():
    # install refuses the destructive write without --yes (exit 2, clean message).
    code, _, err = run(["install", "--release", "r.json", "--layout", "l.json"])
    assert code == 2
    assert "refusing" in err.lower()


def test_install_dispatch_refuses_unsigned_release(tmp_path, release_json, layout_json):
    # An unverified release is refused even with --yes (exit 2, clean message);
    # the runner never runs a step.
    rel = tmp_path / "rel.json"
    lay = tmp_path / "layout.json"
    unsigned = release_json.replace('"verified": true', '"verified": false')
    rel.write_text(unsigned, encoding="utf-8")
    lay.write_text(layout_json, encoding="utf-8")
    fake = FakeRunner()
    code, _, err = run(
        ["install", "--release", str(rel), "--layout", str(lay), "--yes"],
        runner=fake,
    )
    assert code == 2
    assert "unverified" in err.lower()
    assert not fake.calls  # refused before any step ran


def test_install_dispatch_runs_with_yes_via_injected_runner(
    tmp_path, release_json, layout_json
):
    # With --yes the plan executes through the injected runner (no root/hardware).
    rel = tmp_path / "rel.json"
    lay = tmp_path / "layout.json"
    rel.write_text(release_json, encoding="utf-8")
    lay.write_text(layout_json, encoding="utf-8")
    fake = FakeRunner()
    code, out, _ = run(
        ["install", "--release", str(rel), "--layout", str(lay), "--yes"],
        runner=fake,
    )
    assert code == 0
    # The first step is the partition table; the last is the teardown sync.
    assert fake.calls[0][:2] == ["parted", "/dev/sda"]
    assert "mklabel" in fake.calls[0]
    assert fake.calls[-1] == ["sync"]
    assert "installed" in out


def test_provision_dispatch_is_wired():
    # provision-plan is now implemented: a missing config is a clean user error
    # (exit 2), NOT the not-yet-implemented sentinel (exit 3).
    code, _, err = run(["provision-plan", "--config", "install.toml"])
    assert code == 2
    assert "not implemented" not in err.lower()


def test_plan_json_flag_is_accepted(tmp_path, release_json, layout_json):
    # The --json flag parses and plan renders as JSON.
    rel = tmp_path / "rel.json"
    lay = tmp_path / "layout.json"
    rel.write_text(release_json, encoding="utf-8")
    lay.write_text(layout_json, encoding="utf-8")
    code, out, _ = run(
        ["plan", "--release", str(rel), "--layout", str(lay), "--json"]
    )
    assert code == 0
    assert '"argv"' in out


def test_install_yes_flag_is_accepted(tmp_path, release_json, layout_json):
    # --yes is accepted by the parser and lets install past the refusal; with a
    # fake runner the plan executes (exit 0) instead of being refused.
    rel = tmp_path / "rel.json"
    lay = tmp_path / "layout.json"
    rel.write_text(release_json, encoding="utf-8")
    lay.write_text(layout_json, encoding="utf-8")
    fake = FakeRunner()
    code, _, _ = run(
        ["install", "--release", str(rel), "--layout", str(lay), "--yes"],
        runner=fake,
    )
    assert code == 0
    assert fake.calls  # the plan started executing


def test_console_scripts_delegate_to_main():
    # The pyproject console scripts point at these names; they must all work.
    assert cli.plan_main is cli.main
    assert cli.install_main is cli.main
    assert cli.provision_main is cli.main


def test_main_returns_int():
    # A not-yet-implemented command returns a concrete int exit code.
    assert isinstance(cli.main(["plan", "--release", "r", "--layout", "l"]), int)


def test_package_version_is_string():
    assert isinstance(__version__, str)
    assert __version__
