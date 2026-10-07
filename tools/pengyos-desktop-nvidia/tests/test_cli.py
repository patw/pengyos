"""The CLI surface: exit codes and output."""

from __future__ import annotations

import json
import pathlib

import pytest

from pengyos_desktop_nvidia import __version__, cli
from test_lint import make_tree


def test_lint_clean_exits_zero(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = make_tree(tmp_path / "br2")
    assert cli.main(["lint", "--repo", str(tree)]) == 0
    assert capsys.readouterr().out.strip() == "lint: ok"


def test_lint_json(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = make_tree(tmp_path / "br2")
    assert cli.main(["lint", "--repo", str(tree), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_lint_violation_exits_two(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = make_tree(tmp_path / "br2")
    server = tree / "configs" / "pengyos_server_defconfig"
    server.write_text(server.read_text() + "BR2_PACKAGE_NVIDIA_OPEN=y\n")
    assert cli.main(["lint", "--repo", str(tree), "--json"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["rules"]["driver-not-in-default"] is False
    assert "pengyos_server_defconfig" in captured.err


def test_missing_tree_exits_two(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["lint", "--repo", str(tmp_path / "nope")]) == 2
    assert capsys.readouterr().err.startswith("error:")


def test_lint_requires_repo() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["lint"])
    assert exc.value.code == 2


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert __version__ in capsys.readouterr().out
