"""Tests for the shipped ``guests.toml``.

These lock in the concrete deliverable of the "Define guests.toml" subtask: the
repo-root ``guests.toml`` must be valid and must expose every declared field
(``name``, ``image``, ``packages``, ``exports``, ``mounts``) for each guest.
"""

import io
import pathlib

import pytest

from pengyos_guest import cli
from pengyos_guest.engines import GuestManager
from pengyos_guest.runner import FakeRunner
from pengyos_guest.config import load

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def shipped_guests_toml() -> str:
    """Path to the repo-root ``guests.toml``."""
    return str(REPO_ROOT / "guests.toml")


def test_shipped_guests_toml_exists_and_parses(shipped_guests_toml):
    book = load(shipped_guests_toml)
    assert book.names() == ["wolfi", "nixos"]


def test_shipped_guests_have_all_five_fields(shipped_guests_toml):
    book = load(shipped_guests_toml)
    for guest in book.guests:
        # name — represented by the key, present on the model
        assert guest.name
        # image — required
        assert guest.image
        # packages / exports / mounts — the four declarative bodies
        assert isinstance(guest.packages, list)
        assert isinstance(guest.exports, dict)
        assert isinstance(guest.mounts, list)


def test_shipped_wolfi_fields(shipped_guests_toml):
    wolfi = load(shipped_guests_toml).get("wolfi")
    assert wolfi.image == "quay.io/aliyunos/wolfi:latest"
    assert "git" in wolfi.packages
    assert "git-editor" in wolfi.exports
    assert wolfi.exports["git-editor"].endswith("/git-editor")
    assert any(p.endswith("/projects:rw") for p in wolfi.mounts)


def test_shipped_nixos_fields(shipped_guests_toml):
    nixos = load(shipped_guests_toml).get("nixos")
    assert nixos.image == "ghcr.io/pengyos/nixos:latest"
    assert "ripgrep" in nixos.packages
    assert nixos.exports == {"rg": "/usr/bin/rg"}
    assert any(p.endswith(":ro") for p in nixos.mounts)


def test_list_command_reads_shipped_file(shipped_guests_toml):
    out = io.StringIO()
    code = cli.main(["--config", shipped_guests_toml, "list"], out=out,
                    manager=GuestManager(runner=FakeRunner()))
    assert code == 0
    assert [line.split("\t")[0] for line in out.getvalue().splitlines()] == ["wolfi", "nixos"]
