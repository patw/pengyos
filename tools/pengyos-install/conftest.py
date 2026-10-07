"""Root conftest for the pengyos-install test suite.

The package uses a src layout. Rather than require an editable install (which may
pull from the network), we put ``src`` on ``sys.path`` so tests import the
library directly from the tree. Integration tests are opt-in via
``PENGYOS_IT=1``.
"""

from __future__ import annotations

import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Integration tests are gated behind an explicit opt-in env var so the default
# suite never needs root, a real device, network, or hardware.
IT = os.environ.get("PENGYOS_IT", "").lower() in {"1", "true", "yes"}


@pytest.fixture
def integration() -> bool:
    """True only when integration tests are enabled (PENGYOS_IT=1)."""
    return IT


@pytest.fixture
def release_json() -> str:
    """A representative release manifest (the signed artifact the updater reads)."""
    return """\
{
  "name": "pengyos",
  "edition": "core",
  "version": "2026.08-1",
  "slot": "a",
  "rootfs": {
    "path": "rootfs.squashfs",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "size": 1900000000
  },
  "uki": {
    "path": "boot/EFI/boot/bootx64.efi",
    "sha256": "1111111111111111111111111111111111111111111111111111111111111111",
    "size": 12000000
  },
  "signature": {
    "alg": "ed25519",
    "cert": "pengyos-signing-cert.pem",
    "data": "sig==",
    "verified": true
  }
}
"""


@pytest.fixture
def layout_json() -> str:
    """A representative target layout (where slot A, the ESP, and data live)."""
    return """\
{
  "target": "/dev/sda",
  "table": "gpt",
  "slots": {
    "a": { "partition": "/dev/sda2", "type": "root", "index": 2 },
    "b": { "partition": "/dev/sda3", "type": "root", "index": 3 }
  },
  "esp": { "partition": "/dev/sda1", "index": 1 },
  "data": { "partition": "/dev/sda4", "index": 4 }
}
"""


@pytest.fixture
def install_toml() -> str:
    """A representative first-boot provisioning config (install.toml)."""
    return """\
hostname = "alex-pengyos"

[timezone]
zone = "America/Toronto"

[network]
manager = "systemd"

[[users]]
name = "alex"
shell = "/bin/bash"
groups = ["wheel", "audio", "video"]
ssh_keys = ["ssh-ed25519 AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8= example@alex"]

[[users]]
name = "root"
shell = "/bin/bash"
ssh_keys = []
"""


@pytest.fixture
def write_file(tmp_path):
    """Write *text* to a file under *tmp_path* and return its path.

    Usage::

        path = write_file("name.json", "{ ... }")
    """

    def _write(name: str, text: str) -> str:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    return _write
