"""Shared fixtures: the engine on sys.path, a fake payload, fake disks and keys."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ENGINE_SRC = Path(__file__).resolve().parents[2] / "pengyos-install" / "src"
sys.path.insert(0, str(ENGINE_SRC))

from pengyos_install.image import Disk, Manifest  # noqa: E402

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample pat@laptop"
GH_KEYS = ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGitHub1 patw@github\n"
           "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQGitHub2 patw@github")

DISKS = [
    Disk("sda", "/dev/sda", 8_000_000_000, "USB", removable=True, is_boot=True),
    Disk("vda", "/dev/vda", 20_000_000_000, "QEMU"),
    Disk("vdb", "/dev/vdb", 8_000_000_000, "tiny"),
]


def write_payload(root: Path, edition: str = "server") -> str:
    f = {"file": "x.zst", "sha256": "0", "raw_size": 1000, "raw_sha256": "0"}
    (root / "manifest.json").write_text(json.dumps({
        "format": "pengyos-layout-1", "edition": edition, "version": "26.10.50",
        "files": {"rootfs": f, "esp": dict(f, file="e.zst")},
    }))
    return str(root)


@pytest.fixture
def payload(tmp_path):
    return write_payload(tmp_path)


@pytest.fixture
def desktop_payload(tmp_path):
    return write_payload(tmp_path, "desktop")


@pytest.fixture
def manifest(payload):
    return Manifest.load(payload)


@pytest.fixture
def desktop_manifest(desktop_payload):
    return Manifest.load(desktop_payload)


HOST_KEYS = ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHost1 pat@laptop\n"
             "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHost2 pat@desktop")
HOST_PASSWORD = "behopass"


class FakeFetch:
    """fetch_keys stand-in: GitHub and URLs give GH_KEYS; user@host gives
    HOST_KEYS (one line skipped) for HOST_PASSWORD and refuses anything else."""

    def __init__(self, error: Exception | None = None):
        self.error, self.calls = error, []

    def __call__(self, src, password):
        from pengyos_install_tui.keys import Imported, PasswordRefused

        self.calls.append((src.label, password))
        if self.error:
            raise self.error
        if src.kind == "ssh":
            if password != HOST_PASSWORD:
                raise PasswordRefused(f"{src.label} refused the password")
            return Imported(HOST_KEYS, skipped=1)
        return Imported(GH_KEYS)


@pytest.fixture
def fetcher():
    return FakeFetch()
