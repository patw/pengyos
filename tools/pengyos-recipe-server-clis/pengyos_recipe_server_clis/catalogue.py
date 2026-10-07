"""The committed tool catalogue for PengyOS Server comfort CLIs.

Recipes are our data.  This module owns the *authoritative release metadata* for
the six CLIs Buildroot 2026.08 does not ship (``zoxide``, ``sd``,
``node_exporter``, ``btop``, ``helix``, ``neovim``): the pinned upstream
``name``, ``repo``, ``version``, ``tag``, upstream ``homepage`` URL, ``license``
and the source ``sha256`` of the release tarball.

The data lives in a committed JSON file
(:data:`CATALOGUE_PATH`) so it is obviously *data* — not code — and can be
regenerated from the live upstream release without touching the emit/lint logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
CATALOGUE_PATH = PACKAGE_DIR / "data" / "catalogue.json"

# The six Server comfort CLIs, in the canonical order they are emitted.
TOOL_NAMES: tuple[str, ...] = (
    "zoxide",
    "sd",
    "node_exporter",
    "btop",
    "helix",
    "neovim",
)

# A pinned Buildroot version is an upstream release tag, never a moving ref
# (HEAD / master / main / latest / stable / …).  Mirrors the guard in
# pengyos-recipe-uutils.
_MOVING_REF_RE = (
    r"^(git|head|auto|master|main|latest|trunk|stable|snapshot|current|next|dev)$"
)

# A sha256 digest is exactly 64 lowercase hex characters.
_SHA256_RE = "[0-9a-f]{64}"


@dataclass(frozen=True)
class Tool:
    """One entry in the committed tool catalogue.

    Attributes:
        name: The CLI name, also the recipe/package directory name
            (e.g. ``"zoxide"``).
        repo: The upstream ``owner/name`` GitHub repository (used to build the
            download URL).
        version: The pinned upstream release version, *without* any leading
            ``v`` (e.g. ``"0.10.0"``).
        tag: The exact upstream release tag (e.g. ``"v0.10.0"``).  ``helix`` is
            the exception: its tags carry no ``v`` prefix (``"25.07.1"``).
        license: The SPDX license identifier (e.g. ``"MIT"``).
        license_file: The license file's name *inside the release tarball*
            (e.g. ``"LICENSE"`` / ``"LICENSE.txt"``); the Buildroot
            ``PKG_LICENSE_FILES`` path is relative to the extracted source.
        sha256: The sha256 of the pinned release tarball.
    """

    name: str
    repo: str
    version: str
    tag: str
    license: str
    license_file: str
    sha256: str

    # --- derived, deterministic views of the committed data --------------- #

    @property
    def homepage(self) -> str:
        """The canonical upstream repository URL (used in the Config.in help)."""
        return f"https://github.com/{self.repo}"

    @property
    def source(self) -> str:
        """The release tarball filename, as Buildroot expects ``PKG_SOURCE``."""
        return f"{self.name}-{self.version}.tar.gz"

    @property
    def tag_prefix(self) -> str:
        """The prefix Buildroot's ``github`` macro must splice before the version.

        Upstream tags normally carry a leading ``v`` (``v0.10.0``); the
        ``$(call github, …)`` macro takes the version verbatim, so the emit
        layer splices a ``v`` in front.  ``helix`` tags have no ``v`` prefix, so
        its prefix is empty.
        """
        return "" if self.tag == self.version else "v"

    @property
    def site_version(self) -> str:
        """The exact tag string that goes into the ``$(call github, …)`` macro."""
        return f"{self.tag_prefix}{self.version}"

    def validate(self) -> None:
        """Assert the committed data is well-formed; raise ``AssertionError`` otherwise.

        * the version is a concrete, pinned release (never a moving ref),
        * the sha256 is a 64-hex-character digest.
        """
        import re

        assert self.name, f"{self.repo!r}: name must not be empty"
        assert "/" in self.repo, f"{self.name!r}: repo must be 'owner/name'"
        assert self.version, f"{self.name!r}: version must not be empty"
        assert not re.match(_MOVING_REF_RE, self.version), (
            f"{self.name!r}: version {self.version!r} is a moving ref, not a pin"
        )
        assert self.license, f"{self.name!r}: license must not be empty"
        assert self.license_file, f"{self.name!r}: license_file must not be empty"
        assert re.fullmatch(_SHA256_RE, self.sha256) is not None, (
            f"{self.name!r}: sha256 must be 64 hex characters, got {self.sha256!r}"
        )


def _tool_from_row(row: object) -> Tool:
    if not isinstance(row, dict):
        raise TypeError(f"catalogue row must be a mapping, got {row!r}")
    required = ("name", "repo", "version", "tag", "license", "license_file", "sha256")
    missing = [key for key in required if key not in row]
    assert not missing, f"catalogue row is missing required fields: {missing}"
    return Tool(**row)  # type: ignore[arg-type] — validated by the field check above


def parse_catalogue(text: str) -> list[Tool]:
    """Parse catalogue *text* (JSON) into a validated, ordered :class:`Tool` list."""
    data = json.loads(text)
    rows = data["tools"] if isinstance(data, dict) else data
    assert isinstance(rows, list), "catalogue 'tools' must be a list"
    tools = [_tool_from_row(row) for row in rows]
    for tool in tools:
        tool.validate()
    return tools


def load_catalogue(path: Path = CATALOGUE_PATH) -> list[Tool]:
    """Load and validate the committed catalogue from *path*."""
    return parse_catalogue(path.read_text(encoding="utf-8"))


def get_tool(name: str) -> Tool:
    """Return the catalogue entry for *name*, or raise ``KeyError``."""
    for tool in load_catalogue():
        if tool.name == name:
            return tool
    raise KeyError(name)
