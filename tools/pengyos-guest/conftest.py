"""Root conftest for the pengyos-guest test suite.

The package uses a src layout. Rather than require an editable install (which
may pull from the network), we put ``src`` on ``sys.path`` so tests import the
library directly from the tree. Integration tests are opt-in via
``PENGYOS_IT=1``.
"""

import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Integration tests are gated behind an explicit opt-in env var so the default
# suite never needs podman/distrobox, root, network, or a display.
IT = os.environ.get("PENGYOS_IT", "").lower() in {"1", "true", "yes"}


@pytest.fixture
def integration(monkeypatch) -> bool:
    """True only when integration tests are enabled (PENGYOS_IT=1)."""
    return IT


@pytest.fixture
def sample_guests_toml() -> str:
    """A representative ``guests.toml`` body used across tests."""
    return '''\
[guests]

  [guests.wolfi]
  image = "quay.io/aliyunos/wolfi:latest"
  packages = ["git", "curl"]
  exports = { "git-editor" = "/var/lib/distrobox/bin/git-editor" }
  mounts = ["/home/alex/projects:/projects:rw"]

  [guests.nixos]
  image = "ghcr.io/pengyos/nixos:latest"
  packages = ["ripgrep"]
  exports = { "rg" = "/usr/bin/rg" }
  mounts = ["/home/alex/data:/data:ro"]
'''


@pytest.fixture
def write_guests(tmp_path, sample_guests_toml):
    """Write *sample_guests_toml* to a temp file and return its path."""

    def _write(body: str | None = None, name: str = "guests.toml") -> str:
        path = tmp_path / name
        path.write_text(body if body is not None else sample_guests_toml, encoding="utf-8")
        return str(path)

    return _write
