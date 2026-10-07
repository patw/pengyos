"""Tests for the medium-plan data model.

Covers the closed-vocabulary enums, the sub-models, :class:`MediumPlan`'s
deterministic serialisation (``to_dict`` / ``to_json`` / ``from_dict``), the
``make_plan`` factory, and the human-readable renderer.
"""

import dataclasses

from pengyos_media.model import (
    BootEntry,
    Edition,
    LiveRootfs,
    LiveRootfsFormat,
    MediumKind,
    MediumPlan,
    OsRelease,
    Release,
    Installer,
    field_order,
    make_plan,
    render_plan_text,
)

# The committed golden plan (tests/test_plan.py compares the CLI output to it).
EXPECTED_JSON = (
    '{\n'
    '  "boot_entry": {\n'
    '    "kind": "efi",\n'
    '    "label": "PengyOS",\n'
    '    "loader": "/EFI/BOOT/bootx64.efi"\n'
    '  },\n'
    '  "edition": "desktop",\n'
    '  "installer": {\n'
    '    "binary": "pengyos-install",\n'
    '    "live_user": "live"\n'
    '  },\n'
    '  "kind": "iso",\n'
    '  "live_rootfs": {\n'
    '    "format": "squashfs",\n'
    '    "label": "PENGYOS-LIVE",\n'
    '    "name": "pengyos-live.sqfs"\n'
    '  },\n'
    '  "os_release": {\n'
    '    "id": "desktop",\n'
    '    "pretty_name": "PengyOS",\n'
    '    "version_id": ""\n'
    '  },\n'
    '  "release": {\n'
    '    "channel": "testing",\n'
    '    "edition": "desktop",\n'
    '    "version": "2026.09.1"\n'
    '  }\n'
    '}\n'
)


def test_medium_kinds_are_closed_string_enum():
    assert [k.value for k in MediumKind] == ["iso", "disk-image"]
    assert MediumKind.ISO == "iso"  # str-enum: compares to its value
    assert MediumKind("disk-image") is MediumKind.DISK_IMAGE


def test_editions_are_closed_string_enum():
    assert [e.value for e in Edition] == ["desktop", "core", "server"]
    assert Edition.DESKTOP == "desktop"
    assert list(Edition) == [Edition.DESKTOP, Edition.CORE, Edition.SERVER]


def test_live_rootfs_formats():
    assert [f.value for f in LiveRootfsFormat] == ["squashfs", "dir"]
    assert LiveRootfsFormat.SQUASHFS == "squashfs"


def test_make_plan_defaults():
    plan = make_plan("desktop", "2026.09.1")
    assert plan.kind == MediumKind.ISO
    assert plan.edition == Edition.DESKTOP
    assert plan.release.edition == "desktop"
    assert plan.release.version == "2026.09.1"
    assert plan.release.channel == "testing"
    # The shared constants (spec): installer + live user.
    assert plan.installer.binary == "pengyos-install"
    assert plan.installer.live_user == "live"
    # The os-release names the edition (item 5).
    assert plan.os_release.id == "desktop"
    # A squashfs live rootfs by default.
    assert plan.live_rootfs.format == LiveRootfsFormat.SQUASHFS


def test_make_plan_accepts_edition_enum_and_overrides():
    plan = make_plan(
        Edition.SERVER,
        "2026.09.1",
        kind=MediumKind.DISK_IMAGE,
        channel="stable",
        os_release_id="pengyos-server",
    )
    assert plan.kind == MediumKind.DISK_IMAGE
    assert plan.edition == Edition.SERVER
    assert plan.release.channel == "stable"
    assert plan.os_release.id == "pengyos-server"


def test_to_dict_has_fixed_key_order():
    plan = make_plan("desktop", "2026.09.1")
    assert list(plan.to_dict()) == list(field_order())
    # The medium's parts are all present.
    d = plan.to_dict()
    for key in ("kind", "edition", "release", "os_release",
                "live_rootfs", "installer", "boot_entry"):
        assert key in d


def test_to_dict_uses_enum_values_not_members():
    plan = make_plan("core", "2026.09.1")
    d = plan.to_dict()
    assert d["kind"] == "iso"
    assert d["edition"] == "core"
    assert d["live_rootfs"]["format"] == "squashfs"
    assert isinstance(d["kind"], str)


def test_to_json_is_deterministic_and_trailing_newline():
    plan = make_plan("desktop", "2026.09.1")
    # Two builds over the same inputs are byte-identical.
    assert plan.to_json() == make_plan("desktop", "2026.09.1").to_json()
    # Canonical form: sorted keys + a single trailing newline.
    assert plan.to_json() == EXPECTED_JSON
    assert plan.to_json().endswith("}\n")
    assert "\n" not in plan.to_json().rstrip("\n")[-1]  # no blank lines inside


def test_from_dict_round_trips():
    plan = make_plan("desktop", "2026.09.1")
    rebuilt = MediumPlan.from_dict(plan.to_dict())
    assert rebuilt == plan
    assert rebuilt.to_json() == plan.to_json()


def test_from_dict_reads_optional_sub_fields():
    data = {
        "kind": "disk-image",
        "edition": "server",
        "release": {"edition": "server", "version": "2026.09.1", "channel": "stable"},
        "os_release": {"id": "server", "pretty_name": "PengyOS Server", "version_id": "2026.09.1"},
        "live_rootfs": {"format": "dir", "name": "rootfs", "label": "ROOT"},
        "installer": {"binary": "pengyos-install", "live_user": "live"},
        "boot_entry": {"kind": "efi", "label": "PengyOS", "loader": "/EFI/BOOT/bootx64.efi"},
    }
    plan = MediumPlan.from_dict(data)
    assert plan.kind == MediumKind.DISK_IMAGE
    assert plan.edition == Edition.SERVER
    assert plan.release.channel == "stable"
    assert plan.os_release.pretty_name == "PengyOS Server"
    assert plan.live_rootfs.format == LiveRootfsFormat.DIR
    assert plan.live_rootfs.name == "rootfs"


def test_medium_plan_is_frozen():
    plan = make_plan("desktop", "2026.09.1")
    try:
        plan.edition = Edition.CORE  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:  # pragma: no cover - a mutable plan would be a bug
        raise AssertionError("MediumPlan must be frozen")


def test_replace_builds_a_mutated_copy():
    plan = make_plan("desktop", "2026.09.1")
    disk = dataclasses.replace(plan, kind=MediumKind.DISK_IMAGE)
    assert disk.kind == MediumKind.DISK_IMAGE
    assert disk.edition == plan.edition  # untouched fields preserved
    assert disk is not plan


def test_render_plan_text_names_every_part():
    text = render_plan_text(make_plan("desktop", "2026.09.1"))
    for needle in (
        "iso", "desktop", "2026.09.1", "testing", "desktop",
        "squashfs", "pengyos-install", "live", "efi", "/EFI/BOOT/bootx64.efi",
    ):
        assert needle in text


def test_sub_model_defaults():
    assert LiveRootfs().format == LiveRootfsFormat.SQUASHFS
    assert LiveRootfs().name == "pengyos-live.sqfs"
    assert Installer().binary == "pengyos-install"
    assert BootEntry().loader == "/EFI/BOOT/bootx64.efi"
    assert Release(edition="core", version="1").channel == "testing"
    assert OsRelease(id="core").pretty_name == "PengyOS"
