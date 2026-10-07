"""Tests for pengyos_seal.runner (the injected runner seam)."""

from __future__ import annotations

import os
import pathlib

import pytest

from pengyos_seal import runner
from pengyos_seal.runner import FileSystemRunner, MemoryRunner, RunResult

P = pathlib.Path


# --- RunResult --------------------------------------------------------------


def test_run_result_is_frozen():
    result = RunResult(args=("pengyos-sign", "sign"), rc=0, stdout="ok")
    assert result.args == ("pengyos-sign", "sign")
    assert result.rc == 0
    assert result.stdout == "ok"
    with pytest.raises(AttributeError):
        result.rc = 1  # frozen dataclass


# --- MemoryRunner: run_command ------------------------------------------------


def test_memory_runner_records_commands():
    r = MemoryRunner()
    result = r.run_command(["pengyos-sign", "sign", "a"])
    assert result.args == ("pengyos-sign", "sign", "a")
    assert r.commands == [("pengyos-sign", "sign", "a")]


def test_memory_runner_seeded_result_matches_prefix():
    r = MemoryRunner(
        results={"pengyos-sign sign": RunResult(args=("x",), rc=0, stdout="signed")}
    )
    result = r.run_command(["pengyos-sign", "sign", "images/bzImage"])
    assert result.rc == 0
    assert result.stdout == "signed"


def test_memory_runner_unmatched_command_fails_closed():
    r = MemoryRunner()
    result = r.run_command(["pengyos-verifier", "check"])
    assert result.rc == 127
    assert "command not found" in result.stderr


def test_memory_runner_first_matching_prefix_wins():
    r = MemoryRunner(
        results={
            "pengyos-sign": RunResult(args=(), rc=1, stderr="seeded-a"),
            "pengyos-sign sign": RunResult(args=(), rc=0, stdout="seeded-b"),
        }
    )
    # The first seeded prefix the command starts with wins (insertion order).
    result = r.run_command(["pengyos-sign", "sign"])
    assert result.stderr == "seeded-a"


# --- MemoryRunner: virtual build tree ----------------------------------------


def test_memory_runner_dir_and_file_lifecycle(tmp_path):
    r = MemoryRunner()
    p = tmp_path / "images" / "bzImage"
    assert not r.is_dir(tmp_path / "images")
    r.seed_dir(tmp_path / "images")
    assert r.is_dir(tmp_path / "images")
    r.write_bytes(p, b"payload")
    assert r.exists(p)
    assert r.read_bytes(p) == b"payload"
    assert r.signed == []  # not a .sig


def test_memory_runner_reads_back_written_sig():
    r = MemoryRunner()
    sig_path = P("images/bzImage.sig")
    r.write_bytes(sig_path, b"x" * 64)
    assert r.signed == [str(sig_path)]


def test_memory_runner_symlink_follows_to_target():
    r = MemoryRunner()
    r.seed_dir(P("."))
    real = P("images/m2.spdx.json")
    r.write_bytes(real, b"spdx")
    link = P("sbom.spdx.json")
    r.symlink(real, link)
    assert r.exists(link)
    assert r.read_bytes(link) == b"spdx"
    assert r.readlink(link) == real
    assert r.links_created == [(str(link), str(real))]


def test_memory_runner_readlink_missing_raises():
    r = MemoryRunner()
    with pytest.raises(NotADirectoryError):
        r.readlink(P("nope"))


def test_memory_runner_read_missing_file_raises():
    r = MemoryRunner()
    with pytest.raises(FileNotFoundError):
        r.read_bytes(P("missing"))


def test_memory_runner_written_by_name():
    r = MemoryRunner()
    r.write_bytes(P("images/bzImage.sig"), b"data")
    assert r.written_by_name["bzImage.sig"] == b"data"


# --- FileSystemRunner: overrides ----------------------------------------------


def test_filesystem_runner_resolves_override(tmp_path):
    marker = tmp_path / "marker.sh"
    marker.write_text("#!/bin/sh\necho seeded\n", encoding="utf-8")
    marker.chmod(0o755)
    r = FileSystemRunner(overrides={"pengyos-sign": str(marker)})
    result = r.run_command(["pengyos-sign", "sign", "a"])
    assert result.stdout.strip() == "seeded"
    # The recorded args keep the original tool name (argv[0] not mutated in the
    # result); only the executed argv was overridden.
    assert result.args == ("pengyos-sign", "sign", "a")


def test_filesystem_runner_missing_binary_fails_closed(tmp_path):
    r = FileSystemRunner(overrides={"pengyos-sign": str(tmp_path / "nope")})
    result = r.run_command(["pengyos-sign", "sign", "a"])
    assert result.rc == 127

def test_filesystem_runner_ignores_none_override(tmp_path):
    """A ``None`` override must mean "no override", not "argv[0] = None".

    Regression: an absent ``--sign-bin`` blanked ``argv[0]`` and crashed
    ``subprocess`` with ``TypeError: expected str ... not NoneType`` before the
    tool ever ran.
    """
    r = FileSystemRunner(overrides={"pengyos-sign": None})
    argv = r._resolve(["pengyos-sign", "sign", "a"])
    assert argv == ["pengyos-sign", "sign", "a"]


def test_filesystem_runner_roundtrip_file_and_symlink(tmp_path):
    r = FileSystemRunner()
    img = tmp_path / "images"
    img.mkdir(parents=True)
    bz = img / "bzImage"
    bz.write_bytes(b"payload")
    data = r.read_bytes(bz)
    assert data == b"payload"

    link = tmp_path / "sbom.spdx.json"
    target = img / "m2.spdx.json"
    target.write_bytes(b"spdx")
    r.symlink(target, link)
    assert r.readlink(link) == target.resolve()
    assert link.read_bytes() == b"spdx"
    # Re-symlinking over an existing link replaces it cleanly.
    r.symlink(target, link)
    assert r.readlink(link) == target.resolve()


def test_filesystem_runner_is_dir():
    r = FileSystemRunner()
    assert r.is_dir(os.path.dirname(__file__))
    assert not r.is_dir(os.path.join(os.path.dirname(__file__), "nope-nope"))


def test_module_exports():
    for name in ("RunResult", "Runner", "FileSystemRunner", "MemoryRunner"):
        assert hasattr(runner, name), f"runner module missing export: {name}"
    assert issubclass(FileSystemRunner, runner.Runner)
    assert issubclass(MemoryRunner, runner.Runner)
