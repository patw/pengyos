"""Golden + round-trip tests for ``layout.toml`` serialization.

The emitter (``pengyos_btrfs.toml_io.layout_to_toml``) is a small stdlib TOML
writer — there is no stdlib TOML *emitter* (only read-only ``tomllib``), so the
emitted text is pinned by a byte-for-byte golden, and every parse goes through
``tomllib`` (the authoritative reader) so the emitter can never drift away
from real TOML. ``layout_from_toml`` re-validates through
``validate_layout``, so a round-trip is a load-bearing guarantee.

No network, no root, no real btrfs.
"""

import tomllib

import pytest

from pengyos_btrfs.errors import LayoutError
from pengyos_btrfs.layout import Subvolume, canonical_layout, validate_layout
from pengyos_btrfs.toml_io import layout_from_toml, layout_to_toml


# --- Golden: the canonical layout, byte for byte --------------------------
GOLDEN_CANONICAL_TOML = """\
# PengyOS btrfs subvolume layout
# filesystem: pengyos

filesystem = "pengyos"
snapshot_dir = "@snapshots"

[[subvolumes]]
name = "@"
path = "@"
read_only = false
mount = "/"
description = "OS root; the frozen base image (agent mutation lands in home/var, never here)"

[[subvolumes]]
name = "@home"
path = "/@home"
read_only = false
mount = "/home"
description = "user homes; the mutable, agent-editable layer"

[[subvolumes]]
name = "@var"
path = "/@var"
read_only = false
mount = "/var"
description = "logs and state; mutable, snapshot-covered"

[[subvolumes]]
name = "@snapshots"
path = "/@snapshots"
read_only = true
description = "parent directory for read-only named snapshots (never shadowed by a live mount point)"
"""


def test_toml_io_canonical_layout_golden():
    assert layout_to_toml(canonical_layout()) == GOLDEN_CANONICAL_TOML


def test_toml_io_emitted_document_is_valid_toml_for_tomllib():
    """The authoritative reader (tomllib) accepts the emitted document, and
    the parsed structure matches ``Layout.to_dict`` — with ``mount`` omitted
    (TOML has no null) for the unmounted snapshot parent."""
    parsed = tomllib.loads(layout_to_toml(canonical_layout()))
    assert parsed["filesystem"] == "pengyos"
    assert parsed["snapshot_dir"] == "@snapshots"
    assert [s["name"] for s in parsed["subvolumes"]] == [
        "@",
        "@home",
        "@var",
        "@snapshots",
    ]
    by_name = {s["name"]: s for s in parsed["subvolumes"]}
    assert "mount" not in by_name["@snapshots"]
    assert by_name["@snapshots"]["read_only"] is True
    assert by_name["@"]["mount"] == "/"


def test_toml_io_mount_key_emitted_only_for_mounted_subvolumes():
    text = layout_to_toml(canonical_layout())
    assert text.count("mount = ") == 3  # @, @home, @var — not @snapshots


def test_toml_io_round_trip_canonical_layout():
    layout = canonical_layout()
    assert layout_from_toml(layout_to_toml(layout)) == layout


def test_toml_io_round_trip_custom_layout():
    """A non-canonical, valid layout (custom snapshot dir, unmounted subvolume,
    read-only mounted subvolume) round-trips exactly."""
    layout = validate_layout(
        "my-box",
        (
            Subvolume(name="@", path="@", mount="/", description="root"),
            Subvolume(name="@data", path="/@data", read_only=True, mount="/data",
                      description="data"),
            Subvolume(name="@cache", path="/@cache", mount=None, description=""),
        ),
        snapshot_dir="@cache",
    )
    assert layout_from_toml(layout_to_toml(layout)) == layout
    parsed = tomllib.loads(layout_to_toml(layout))
    assert parsed["snapshot_dir"] == "@cache"
    by_name = {s["name"]: s for s in parsed["subvolumes"]}
    assert "mount" not in by_name["@cache"]
    assert by_name["@data"]["read_only"] is True
    assert by_name["@data"]["description"] == "data"
    assert by_name["@cache"]["description"] == ""


# --- Escaping ---------------------------------------------------------------
def test_toml_io_escapes_quotes_backslashes_and_control_chars():
    nasty = 'he said "hi" \\ and left\ta\nnewline\tand a \x01 control \x7f del \u009b'
    layout = validate_layout(
        'fs "q"',
        (
            Subvolume(name="@", path="@", mount="/", description=nasty),
            Subvolume(name="@snapshots", path="/@snapshots", read_only=True,
                      mount=None, description=""),
        ),
    )
    text = layout_to_toml(layout)
    # Every parse goes through the authoritative reader.
    parsed = tomllib.loads(text)
    assert parsed["filesystem"] == 'fs "q"'
    assert parsed["subvolumes"][0]["description"] == nasty
    # The golden round-trip survives byte-identically.
    assert layout_from_toml(text) == layout


def test_toml_io_escapes_unicode_but_keeps_it_utf8():
    layout = validate_layout(
        "café",
        (
            Subvolume(name="@", path="@", mount="/", description="café ☕ 🎉"),
            Subvolume(name="@snapshots", path="/@snapshots", read_only=True,
                      mount=None, description=""),
        ),
    )
    text = layout_to_toml(layout)
    # Non-ASCII is raw UTF-8 in the file (TOML strings are UTF-8).
    assert "café ☕ 🎉" in text
    assert layout_from_toml(text) == layout


def test_toml_io_rejects_non_layout_input():
    with pytest.raises(LayoutError, match="expects a Layout"):
        layout_to_toml({"filesystem": "x"})  # type: ignore[arg-type]


# --- Parser error paths (never a traceback) ---------------------------------
def _minimal_toml():
    return (
        'filesystem = "fs"\n'
        'snapshot_dir = "@snapshots"\n'
        "\n"
        "[[subvolumes]]\n"
        'name = "@"\n'
        'path = "@"\n'
        'read_only = false\n'
        'mount = "/"\n'
        "\n"
        "[[subvolumes]]\n"
        'name = "@snapshots"\n'
        'path = "/@snapshots"\n'
        'read_only = true\n'
    )


def test_toml_io_from_minimal_toml():
    layout = layout_from_toml(_minimal_toml())
    assert layout.filesystem == "fs"
    assert [s.name for s in layout.subvolumes] == ["@", "@snapshots"]
    # Missing description defaults to "" and missing mount to None.
    assert layout.subvolumes[1].mount is None
    assert layout.subvolumes[1].description == ""
    assert layout.subvolumes[0].description == ""


@pytest.mark.parametrize(
    "bad, pattern",
    [
        ("this is [not toml", "not valid TOML"),
        ('filesystem = 5\n', "missing or non-string 'filesystem' key"),
        ("snapshot_dir = \"@snapshots\"\n", "missing or non-string 'filesystem' key"),
        ('filesystem = "fs"\n', "missing or non-string 'snapshot_dir' key"),
        ('filesystem = "fs"\nsnapshot_dir = "@snapshots"\n',
         "missing or empty 'subvolumes' table array"),
    ],
)
def test_toml_io_from_invalid_documents(bad, pattern):
    with pytest.raises(LayoutError, match=pattern):
        layout_from_toml(bad)


def test_toml_io_subvolumes_entry_must_be_a_table():
    with pytest.raises(LayoutError, match="subvolumes\\[0\\] is not a table"):
        layout_from_toml('filesystem = "fs"\nsnapshot_dir = "@s"\nsubvolumes = ["x"]')


def test_toml_io_subvolume_requires_name_and_path():
    with pytest.raises(LayoutError, match="missing a string 'name' key"):
        layout_from_toml(
            'filesystem = "fs"\nsnapshot_dir = "@snapshots"\n'
            "[[subvolumes]]\npath = \"@\"\n"
        )
    with pytest.raises(LayoutError, match="missing a string 'path' key"):
        layout_from_toml(
            'filesystem = "fs"\nsnapshot_dir = "@snapshots"\n'
            "[[subvolumes]]\nname = \"@\"\n"
        )


def test_toml_io_read_only_must_be_a_boolean():
    with pytest.raises(LayoutError, match="read_only must be a boolean"):
        layout_from_toml(
            'filesystem = "fs"\nsnapshot_dir = "@snapshots"\n'
            "[[subvolumes]]\nname = \"@\"\npath = \"@\"\nread_only = \"yes\"\n"
        )


def test_toml_io_mount_must_be_a_string_when_present():
    with pytest.raises(LayoutError, match="mount must be a string"):
        layout_from_toml(
            'filesystem = "fs"\nsnapshot_dir = "@snapshots"\n'
            "[[subvolumes]]\nname = \"@\"\npath = \"@\"\nmount = true\n"
        )


def test_toml_io_structurally_invalid_layout_is_rejected_by_validation():
    """A well-formed TOML document that no longer describes a valid layout
    (here: a subvolume with a path whose first component is not its own name)
    must fail through ``validate_layout``, not silently parse."""
    bad = (
        'filesystem = "fs"\nsnapshot_dir = "@snapshots"\n'
        "\n"
        "[[subvolumes]]\n"
        'name = "@"\n'
        'path = "/@wrong"\n'
        "\n"
        "[[subvolumes]]\n"
        'name = "@snapshots"\n'
        'path = "/@snapshots"\n'
    )
    with pytest.raises(LayoutError, match="must start with its own name"):
        layout_from_toml(bad)
