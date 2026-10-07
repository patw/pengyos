"""Tests for the committed tool catalogue (``pengyos_recipe_server_clis.catalogue``).

These assert the requirements of the "Model the tool catalogue as committed
data" subtask:

* the catalogue covers exactly the six Server comfort CLIs, in canonical order,
* each entry carries a name, repo, pinned version, tag, upstream URL, license
  and a 64-hex sha256 of the release tarball,
* the derived views (``source`` / ``homepage`` / ``tag_prefix`` / ``site_version``)
  are correct — including ``helix``'s no-``v`` tag, and
* the committed digests are the real upstream ones (pinned here; an opt-in
  integration test re-checks them against the live tarballs).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from pengyos_recipe_server_clis.catalogue import (
    CATALOGUE_PATH,
    TOOL_NAMES,
    Tool,
    get_tool,
    load_catalogue,
    parse_catalogue,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# The six Server comfort CLIs, in the canonical order they are emitted.
EXPECTED_NAMES: tuple[str, ...] = (
    "zoxide",
    "sd",
    "node_exporter",
    "btop",
    "helix",
    "neovim",
)

# The authoritative upstream release metadata, fetched from the live releases
# API + release tarballs.
# These are the values the catalogue must carry; drift here is a real bug.
EXPECTED = {
    "zoxide": {
        "repo": "ajeetdsouza/zoxide",
        "version": "0.10.0",
        "tag": "v0.10.0",
        "license": "MIT",
        "license_file": "LICENSE",
        "sha256": "4fcd4272b013a10b637dbcc299c58a9924b94470a9042677ca1a204cc2e9150e",
    },
    "sd": {
        "repo": "chmln/sd",
        "version": "1.1.0",
        "tag": "v1.1.0",
        "license": "MIT",
        "license_file": "LICENSE",
        "sha256": "defdce484f8c92f265e1282490572575028967c2c55d356111d1e49a3ea9a88e",
    },
    "node_exporter": {
        "repo": "prometheus/node_exporter",
        "version": "1.12.1",
        "tag": "v1.12.1",
        "license": "Apache-2.0",
        "license_file": "LICENSE",
        "sha256": "a90f1df0fde58d216d4a7d342e64831d2e3257db8084deadbe69754d34b15142",
    },
    "btop": {
        "repo": "aristocratos/btop",
        "version": "1.4.7",
        "tag": "v1.4.7",
        "license": "Apache-2.0",
        "license_file": "LICENSE",
        "sha256": "933de2e4d1b2211a638be463eb6e8616891bfba73aef5d38060bd8319baeefc6",
    },
    "helix": {
        "repo": "helix-editor/helix",
        "version": "25.07.1",
        "tag": "25.07.1",
        "license": "MPL-2.0",
        "license_file": "LICENSE",
        "sha256": "27c8bc3eba46bc7bab1e3629c6b28ff94882eeff17366b3ea69cd8ceffba7541",
    },
    "neovim": {
        "repo": "neovim/neovim",
        "version": "0.12.5",
        "tag": "v0.12.5",
        "license": "Apache-2.0",
        "license_file": "LICENSE.txt",
        "sha256": "a810c95332317bd0017e1ca07e376a8472c79075cbed00fa3737d190a8a0a45a",
    },
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MOVING_REF_RE = re.compile(
    r"^(git|head|auto|master|main|latest|trunk|stable|snapshot|current|next|dev)$",
    re.IGNORECASE,
)


@pytest.fixture(scope="module")
def catalogue() -> list[Tool]:
    return load_catalogue()


# --------------------------------------------------------------------------- #
# Coverage
# --------------------------------------------------------------------------- #
def test_catalogue_has_exactly_the_six_tools(catalogue: list[Tool]) -> None:
    assert [t.name for t in catalogue] == list(EXPECTED_NAMES)


def test_tool_names_constant_matches_spec() -> None:
    assert TOOL_NAMES == EXPECTED_NAMES


def test_catalogue_is_deterministic() -> None:
    """Two loads are identical — no timestamps, ordering or randomness."""
    first = load_catalogue()
    second = load_catalogue()
    assert first == second
    # Canonical order is part of the contract.
    assert [t.name for t in first] == list(EXPECTED_NAMES)


def test_catalogue_json_is_valid_and_committed() -> None:
    assert CATALOGUE_PATH.is_file(), "catalogue.json is not committed"
    data = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert set(data) >= {"tools"}, "catalogue.json must carry a 'tools' array"
    rows = data["tools"]
    assert isinstance(rows, list)
    assert [r["name"] for r in rows] == list(EXPECTED_NAMES)


# --------------------------------------------------------------------------- #
# Per-entry schema + the real upstream metadata
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", list(EXPECTED_NAMES))
def test_entry_matches_real_upstream_metadata(name: str, catalogue: list[Tool]) -> None:
    tool = next(t for t in catalogue if t.name == name)
    want = EXPECTED[name]
    assert tool.repo == want["repo"]
    assert tool.version == want["version"]
    assert tool.tag == want["tag"]
    assert tool.license == want["license"]
    assert tool.license_file == want["license_file"]
    assert tool.sha256 == want["sha256"]


@pytest.mark.parametrize("name", list(EXPECTED_NAMES))
def test_entry_is_well_formed(name: str, catalogue: list[Tool]) -> None:
    tool = next(t for t in catalogue if t.name == name)
    tool.validate()  # raises AssertionError on any violation
    assert tool.name == name
    assert tool.repo.count("/") == 1, "repo must be 'owner/name'"
    assert tool.version, "version must not be empty"
    assert not _MOVING_REF_RE.match(tool.version), (
        f"{name}: version {tool.version!r} is a moving ref, not a pin"
    )
    assert _SHA256_RE.match(tool.sha256), f"{name}: sha256 is not 64 hex chars"


@pytest.mark.parametrize("name", list(EXPECTED_NAMES))
def test_license_is_declared(name: str, catalogue: list[Tool]) -> None:
    tool = next(t for t in catalogue if t.name == name)
    assert tool.license, f"{name}: license must be declared"
    assert tool.license_file, f"{name}: license_file must be named"


# --------------------------------------------------------------------------- #
# Derived views
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", list(EXPECTED_NAMES))
def test_source_tracks_version(name: str, catalogue: list[Tool]) -> None:
    tool = next(t for t in catalogue if t.name == name)
    assert tool.source == f"{tool.name}-{tool.version}.tar.gz"


@pytest.mark.parametrize("name", list(EXPECTED_NAMES))
def test_homepage_points_at_the_repo(name: str, catalogue: list[Tool]) -> None:
    tool = next(t for t in catalogue if t.name == name)
    assert tool.homepage == f"https://github.com/{tool.repo}"


def test_tag_prefix_is_v_except_helix(catalogue: list[Tool]) -> None:
    by_name = {t.name: t for t in catalogue}
    for name, tool in by_name.items():
        if name == "helix":
            assert tool.tag_prefix == "", "helix tags carry no 'v' prefix"
            assert tool.tag == tool.version
        else:
            assert tool.tag_prefix == "v", f"{name} tags should be 'v'-prefixed"
            assert tool.tag.startswith("v")


def test_site_version_splices_prefix_into_version(catalogue: list[Tool]) -> None:
    for tool in catalogue:
        assert tool.site_version == f"{tool.tag_prefix}{tool.version}"
        assert tool.site_version == tool.tag


# --------------------------------------------------------------------------- #
# Loader behaviour
# --------------------------------------------------------------------------- #
def test_get_tool_returns_entry() -> None:
    tool = get_tool("helix")
    assert tool.name == "helix"
    assert tool.version == "25.07.1"


def test_get_tool_missing_raises() -> None:
    with pytest.raises(KeyError):
        get_tool("does-not-exist")


def test_parse_catalogue_accepts_list_shape() -> None:
    """A bare JSON array of rows is also accepted (not only {tools: [...]})."""
    rows = [
        {
            "name": "sd",
            "repo": "chmln/sd",
            "version": "1.1.0",
            "tag": "v1.1.0",
            "license": "MIT",
            "license_file": "LICENSE",
            "sha256": "defdce484f8c92f265e1282490572575028967c2c55d356111d1e49a3ea9a88e",
        }
    ]
    parsed = parse_catalogue(json.dumps(rows))
    assert len(parsed) == 1
    assert parsed[0].name == "sd"


def test_validate_rejects_moving_ref() -> None:
    tool = Tool(
        name="zoxide",
        repo="ajeetdsouza/zoxide",
        version="latest",
        tag="latest",
        license="MIT",
        license_file="LICENSE",
        sha256="a" * 64,
    )
    with pytest.raises(AssertionError):
        tool.validate()


def test_validate_rejects_bad_sha256() -> None:
    tool = Tool(
        name="zoxide",
        repo="ajeetdsouza/zoxide",
        version="0.10.0",
        tag="v0.10.0",
        license="MIT",
        license_file="LICENSE",
        sha256="not-a-hash",
    )
    with pytest.raises(AssertionError):
        tool.validate()


# --------------------------------------------------------------------------- #
# Opt-in integration: verify the committed digests against the live tarballs
# --------------------------------------------------------------------------- #
def _live_sha256(repo: str, tag: str) -> str:
    """Download the release tarball and return its sha256 (integration only)."""
    out = subprocess.run(
        [
            "curl", "-sSL",
            "-H", "User-Agent: pengy-agent",
            f"https://github.com/{repo}/archive/refs/tags/{tag}.tar.gz",
        ],
        check=True,
        capture_output=True,
    )
    import hashlib

    return hashlib.sha256(out.stdout).hexdigest()


def test_committed_digest_matches_live_tarball(catalogue: list[Tool]) -> None:
    """PENGYOS_IT=1: the committed sha256 must equal the live upstream tarball.

    These need the network and are skipped unless ``PENGYOS_IT=1``. They pin the
    catalogue to the real upstream artifact so a bad digest fails CI.
    """
    if not os.environ.get("PENGYOS_IT"):
        pytest.skip("opt-in integration test; set PENGYOS_IT=1 to run")

    for tool in catalogue:
        assert _live_sha256(tool.repo, tool.tag) == tool.sha256, (
            f"{tool.name}: digest drift — committed {tool.sha256} != live"
        )
