"""Tests for the CLI: parsing, dispatch, help, and error handling."""

from __future__ import annotations

import pytest

import pengyos_release
from pengyos_release.cli import main


def test_help_lists_all_subcommands(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    captured = capsys.readouterr()
    assert exc.value.code == 0
    for cmd in ("publish", "index", "verify", "retain"):
        assert cmd in captured.out


def test_each_subcommand_has_help(capsys) -> None:
    for cmd, flag in (
        ("publish", "--build"),
        ("index", "--feed"),
        ("verify", "--feed"),
        ("retain", "--keep"),
    ):
        with pytest.raises(SystemExit) as exc:
            main([cmd, "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert flag in captured.out


def test_publish_requires_build() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["publish", "--edition", "server", "--version", "1.0.0", "--feed", "/tmp/x"])
    assert exc.value.code != 0


def test_publish_cli_writes_signed_feed(tmp_path, build_output, capsys) -> None:
    from pengyos_release.feed import index_path, manifest_path, read_channel_pointer, read_index

    feed = tmp_path / "feed"
    code = main(
        [
            "publish",
            "--build", str(build_output),
            "--edition", "server",
            "--version", "1.0.0",
            "--feed", str(feed),
        ]
    )
    assert code == 0
    # The release is laid out under <feed>/pengyos/<edition>/<version>/
    assert manifest_path(feed, "server", "1.0.0").is_file()
    assert index_path(feed).is_file()
    # The default channel points at the freshly published version.
    assert read_channel_pointer(feed, "stable") == "1.0.0"
    assert read_index(feed).channel("stable").version == "1.0.0"


def test_publish_cli_json_reports_artifacts(tmp_path, build_output, capsys) -> None:
    import json

    from pengyos_release import ARTIFACTS

    feed = tmp_path / "feed"
    code = main(
        [
            "publish",
            "--build", str(build_output),
            "--edition", "core",
            "--version", "2.1.0",
            "--feed", str(feed),
            "--channel", "testing",
            "--json",
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["edition"] == "core"
    assert out["version"] == "2.1.0"
    assert out["channel"] == "testing"
    assert {a["name"] for a in out["artifacts"]} == set(ARTIFACTS)


def test_publish_cli_missing_build_raises_exit2(tmp_path, capsys) -> None:
    code = main(
        ["publish", "--build", str(tmp_path / "nope"), "--edition", "server", "--version", "1.0.0", "--feed", str(tmp_path / "feed")]
    )
    assert code == 2
    assert "build output is not a directory" in capsys.readouterr().err


def test_index_dispatch_missing_feed_exit2(tmp_path, capsys) -> None:
    # index on a nonexistent feed raises FeedError -> exit code 2.
    assert main(["index", "--feed", str(tmp_path / "nope")]) == 2
    assert "feed directory not found" in capsys.readouterr().err


def test_verify_dispatch_missing_feed_exit2(tmp_path, capsys) -> None:
    # verify on a nonexistent feed raises FeedError -> exit code 2.
    assert main(["verify", "--feed", str(tmp_path / "nope")]) == 2
    assert "feed directory not found" in capsys.readouterr().err



def test_no_command_errors(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0


def test_version_flag(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    # Read the version from the package so it cannot drift from the declared
    # version (the same fix applied to test_github_ready's import test).
    assert pengyos_release.__version__ in capsys.readouterr().out


def test_main_returns_nonzero_on_pengyos_error(tmp_path, capsys) -> None:
    # verify on a nonexistent feed raises FeedError -> exit code 2.
    from pengyos_release.errors import FeedError
    from pengyos_release import cli

    # Temporarily make verify raise a FeedError to exercise the error path.
    original = cli.HANDLERS["verify"]
    cli.HANDLERS["verify"] = lambda args: (_ for _ in ()).throw(FeedError("boom"))
    try:
        code = main(["verify", "--feed", str(tmp_path)])
    finally:
        cli.HANDLERS["verify"] = original
    assert code == 2
    assert "boom" in capsys.readouterr().err
