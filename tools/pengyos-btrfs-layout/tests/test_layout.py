"""Tests for the subvolume layout, defined as data + validation.

Covers:

- the *golden* canonical layout (names, paths, read-only flags, mounts,
  order, descriptions) — this is what later command generation builds on;
- immutability of the layout data (frozen dataclasses);
- :meth:`Layout.find` / :meth:`Layout.snapshot_subvolume` / :meth:`Layout.to_dict`;
- every ``validate_layout`` rejection path, with precise, actionable
  ``LayoutError`` messages (malformed input never surfaces as a traceback).

No network, no root, no external tools — pure in-process data checks.
"""

import dataclasses

import pytest

from pengyos_btrfs import constants as c
from pengyos_btrfs.errors import LayoutError
from pengyos_btrfs.layout import (
    Layout,
    Subvolume,
    canonical_layout,
    validate_layout,
)


# ---------------------------------------------------------------------------
# Golden canonical layout
# ---------------------------------------------------------------------------
def test_layout_canonical_is_the_golden_tree():
    layout = canonical_layout()
    got = [(s.name, s.path, s.read_only, s.mount) for s in layout.subvolumes]
    assert got == [
        ("@", "@", False, "/"),
        ("@home", "/@home", False, "/home"),
        ("@var", "/@var", False, "/var"),
        ("@snapshots", "/@snapshots", True, None),
    ]


def test_layout_canonical_defaults_to_pengyos_filesystem():
    assert canonical_layout().filesystem == "pengyos"


def test_layout_canonical_accepts_a_filesystem_name():
    assert canonical_layout("pengyos-live").filesystem == "pengyos-live"
    # ...and that layout is still the same tree.
    assert [s.path for s in canonical_layout("pengyos-live").subvolumes] == [
        "@",
        "/@home",
        "/@var",
        "/@snapshots",
    ]


def test_layout_canonical_every_subvolume_has_a_description():
    for s in canonical_layout().subvolumes:
        assert s.description, f"{s.name!r} has no description"


def test_layout_snapshot_dir_constant_matches_canonical_tree():
    """The pinned SNAPSHOT_DIR constant and the canonical tree cannot drift."""
    snapshot = canonical_layout().snapshot_subvolume()
    assert snapshot.name == c.SNAPSHOT_DIR
    assert snapshot.path == "/" + c.SNAPSHOT_DIR
    assert snapshot.read_only is True
    assert snapshot.mount is None, "the snapshot dir must not be a mount point"


def test_layout_is_immutable():
    layout = canonical_layout()
    with pytest.raises(dataclasses.FrozenInstanceError):
        layout.filesystem = "other"
    with pytest.raises(dataclasses.FrozenInstanceError):
        layout.subvolumes = ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        layout.subvolumes[0].name = "@other"


def test_layout_find_returns_subvolumes_by_path():
    layout = canonical_layout()
    assert layout.find("/@home").name == "@home"
    assert layout.find("/@var").mount == "/var"


def test_layout_find_unknown_path_raises_layout_error():
    layout = canonical_layout()
    with pytest.raises(LayoutError, match="no subvolume at path '/@missing'"):
        layout.find("/@missing")


def test_layout_find_root_by_at_path():
    layout = canonical_layout()
    assert layout.find("@").mount == "/"


def test_layout_to_dict_golden():
    got = canonical_layout().to_dict()
    assert got == {
        "filesystem": "pengyos",
        "snapshot_dir": "@snapshots",
        "subvolumes": [
            {
                "name": "@",
                "path": "@",
                "read_only": False,
                "mount": "/",
                "description": (
                    "OS root; the frozen base image (agent mutation lands in "
                    "home/var, never here)"
                ),
            },
            {
                "name": "@home",
                "path": "/@home",
                "read_only": False,
                "mount": "/home",
                "description": "user homes; the mutable, agent-editable layer",
            },
            {
                "name": "@var",
                "path": "/@var",
                "read_only": False,
                "mount": "/var",
                "description": "logs and state; mutable, snapshot-covered",
            },
            {
                "name": "@snapshots",
                "path": "/@snapshots",
                "read_only": True,
                "mount": None,
                "description": (
                    "parent directory for read-only named snapshots (never "
                    "shadowed by a live mount point)"
                ),
            },
        ],
    }


def test_layout_roundtrip_through_validate_layout():
    """A canonical layout re-validates and stays golden."""
    d = canonical_layout().to_dict()
    again = validate_layout(
        d["filesystem"],
        [
            Subvolume(
                name=s["name"],
                path=s["path"],
                read_only=s["read_only"],
                mount=s["mount"],
                description=s["description"],
            )
            for s in d["subvolumes"]
        ],
    )
    assert again.to_dict() == d


# ---------------------------------------------------------------------------
# validate_layout: structural acceptance
# ---------------------------------------------------------------------------
def _sv(name, path, ro=False, mount="/", description=""):
    return Subvolume(name=name, path=path, read_only=ro, mount=mount,
                     description=description)


def test_validate_accepts_minimal_layout_with_snapshot_dir():
    layout = validate_layout(
        "fs",
        [
            _sv("@", "@"),
            _sv("@snapshots", "/@snapshots", ro=True, mount=None),
        ],
    )
    assert layout.filesystem == "fs"
    assert [s.name for s in layout.subvolumes] == ["@", "@snapshots"]


def test_validate_accepts_unmounted_subvolumes_without_mount_conflicts():
    layout = validate_layout(
        "fs",
        [
            _sv("@", "@"),
            _sv("@snapshots", "/@snapshots", ro=True, mount=None),
            _sv("@cache", "/@cache", ro=False, mount=None),
        ],
    )
    assert len(layout.subvolumes) == 3


# ---------------------------------------------------------------------------
# validate_layout: filesystem rules
# ---------------------------------------------------------------------------
def test_validate_empty_filesystem_rejected():
    with pytest.raises(LayoutError, match="filesystem must be a non-empty"):
        validate_layout("", [_sv("@", "@")])


def test_validate_whitespace_filesystem_rejected():
    with pytest.raises(LayoutError, match="filesystem must be a non-empty"):
        validate_layout("   ", [_sv("@", "@")])


def test_validate_filesystem_path_rejected():
    with pytest.raises(LayoutError, match="must be a filesystem name, not a path"):
        validate_layout("/dev/sda1", [_sv("@", "@")])


def test_validate_oversized_filesystem_name_rejected():
    with pytest.raises(LayoutError, match="longer than 255"):
        validate_layout("x" * 256, [_sv("@", "@")])


# ---------------------------------------------------------------------------
# validate_layout: subvolume element rules
# ---------------------------------------------------------------------------
def test_validate_non_subvolume_element_rejected():
    with pytest.raises(LayoutError, match=r"subvolume #0 is str, expected Subvolume"):
        validate_layout("fs", ["not-a-subvolume"])


def test_validate_invalid_name_rejected():
    for bad in ("home", "home@", "@ home", "@h/ome", "@h om", "@h.om.e!", "", "x@"):
        with pytest.raises(LayoutError, match="invalid subvolume name") as exc:
            validate_layout("fs", [_sv(bad, "@")])
        assert repr(bad) in str(exc.value)


def test_validate_invalid_path_prefix_rejected():
    with pytest.raises(LayoutError, match=r"path must be '@' or start with '/'"):
        validate_layout("fs", [_sv("@", "home")])
    with pytest.raises(LayoutError, match=r"path must be '@' or start with '/'"):
        validate_layout("fs", [_sv("@", "")])


def test_validate_path_name_mismatch_rejected():
    with pytest.raises(LayoutError, match=r"must start with its own name"):
        validate_layout("fs", [_sv("@home", "/@var")])


def test_validate_invalid_mount_rejected():
    for bad in ("home", "./home", "/home/..", "/home/", "//"):
        with pytest.raises(LayoutError, match="invalid mount target"):
            validate_layout("fs", [_sv("@home", "/@home", mount=bad)])


def test_validate_non_bool_read_only_rejected():
    with pytest.raises(LayoutError, match="read_only must be a bool"):
        validate_layout("fs", [_sv("@", "@"), _sv("@snapshots", "/@snapshots",
                                                 ro="yes", mount=None)])


# ---------------------------------------------------------------------------
# validate_layout: uniqueness / ordering rules
# ---------------------------------------------------------------------------
def test_validate_duplicate_names_rejected():
    with pytest.raises(LayoutError, match="duplicate subvolume name '@home'"):
        validate_layout(
            "fs",
            [
                _sv("@", "@"),
                _sv("@home", "/@home", mount="/home"),
                _sv("@home", "/@home-2", mount="/home2"),
            ],
        )


def test_validate_duplicate_paths_rejected():
    with pytest.raises(LayoutError, match="duplicate subvolume path '/@var'"):
        validate_layout(
            "fs",
            [
                _sv("@", "@"),
                _sv("@var", "/@var", mount="/var"),
                _sv("@var2", "/@var", mount="/var2"),
            ],
        )


def test_validate_duplicate_mounts_rejected():
    with pytest.raises(LayoutError, match="duplicate mount target '/home'"):
        validate_layout(
            "fs",
            [
                _sv("@", "@"),
                _sv("@home", "/@home", mount="/home"),
                _sv("@home2", "/@home2", mount="/home"),
            ],
        )


def test_validate_shadowing_mounts_rejected():
    with pytest.raises(LayoutError, match="mount targets shadow each other"):
        validate_layout(
            "fs",
            [
                _sv("@", "@"),
                _sv("@home", "/@home", mount="/home"),
                _sv("@home-x", "/@home-x", mount="/home/x"),
            ],
        )


def test_validate_root_mount_coexists_with_nested_mounts():
    """/ + /home + /var is the intended design and must not be flagged."""
    layout = validate_layout(
        "fs",
        [
            _sv("@", "@", mount="/"),
            _sv("@home", "/@home", mount="/home"),
            _sv("@var", "/@var", mount="/var"),
            _sv("@snapshots", "/@snapshots", ro=True, mount=None),
        ],
    )
    assert len(layout.subvolumes) == 4


# ---------------------------------------------------------------------------
# validate_layout: flat-tree + snapshot-dir rules
# ---------------------------------------------------------------------------
def test_validate_nested_subvolume_rejected():
    with pytest.raises(LayoutError, match="nested subvolumes are not allowed"):
        validate_layout(
            "fs",
            [
                _sv("@", "@"),
                _sv("@home", "/@home", mount="/home"),
                _sv("@sub", "/@home/@sub", mount="/home/sub"),
            ],
        )


def test_validate_missing_snapshot_dir_rejected():
    with pytest.raises(
        LayoutError, match=r"must include a '@snapshots' subvolume"
    ):
        validate_layout(
            "fs",
            [
                _sv("@", "@"),
                _sv("@home", "/@home", mount="/home"),
            ],
        )


def test_validate_custom_snapshot_dir_must_be_present():
    with pytest.raises(LayoutError, match=r"must include a '@snap' subvolume"):
        validate_layout("fs", [_sv("@", "@"), _sv("@snapshots", "/@snapshots",
                                          ro=True, mount=None)],
                        snapshot_dir="@snap")


def test_validate_custom_snapshot_dir_accepted():
    layout = validate_layout(
        "fs",
        [_sv("@", "@"), _sv("@snap", "/@snap", ro=True, mount=None)],
        snapshot_dir="@snap",
    )
    assert layout.snapshot_subvolume().name == "@snap"


def test_validate_invalid_snapshot_dir_rejected():
    with pytest.raises(LayoutError, match="snapshot_dir must be a valid"):
        validate_layout("fs", [_sv("@", "@")], snapshot_dir="snapshots")


def test_validate_empty_subvolume_list_rejected():
    with pytest.raises(LayoutError, match="must contain at least one subvolume"):
        validate_layout("fs", [])
