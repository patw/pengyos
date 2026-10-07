"""Tests for the plan validator.

Every lint rule has one mutation: each test takes a valid plan, mutates one
thing, and asserts the validator reports the matching stable code. Also covers
the closed-vocabulary checks, the boolean wrapper, and that a valid plan has
no problems.
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
    make_plan,
)
from pengyos_media.validate import (
    BOOT_ENTRY_MISSING,
    EDITION_MISMATCH_RELEASE,
    INVALID_EDITION,
    INVALID_KIND,
    INVALID_LIVE_FS_FORMAT,
    INSTALLER_MISSING,
    OS_RELEASE_MISMATCH,
    RELEASE_MISSING_VERSION,
    plan_is_valid,
    validate_plan,
)


def _codes(plan: MediumPlan) -> list[str]:
    return sorted(e.code for e in validate_plan(plan))


def test_valid_plan_has_no_errors():
    assert validate_plan(make_plan("desktop", "2026.09.1")) == []
    assert plan_is_valid(make_plan("core", "2026.09.1"))


def test_invalid_kind():
    plan = dataclasses.replace(make_plan("desktop", "2026.09.1"), kind="cd")
    assert _codes(plan) == [INVALID_KIND]


def test_invalid_kind_reports_expected_values():
    plan = dataclasses.replace(make_plan("desktop", "2026.09.1"), kind="cd")
    errors = validate_plan(plan)
    assert errors[0].code == INVALID_KIND
    assert "iso" in errors[0].message and "disk-image" in errors[0].message


def test_invalid_edition():
    plan = dataclasses.replace(make_plan("desktop", "2026.09.1"), edition="tablet")
    assert _codes(plan) == [INVALID_EDITION]


def test_invalid_live_fs_format():
    plan = dataclasses.replace(
        make_plan("desktop", "2026.09.1"),
        live_rootfs=LiveRootfs(format="btrfs"),  # type: ignore[arg-type]
    )
    assert _codes(plan) == [INVALID_LIVE_FS_FORMAT]


def test_installer_missing():
    plan = dataclasses.replace(
        make_plan("desktop", "2026.09.1"), installer=Installer(binary="")
    )
    assert _codes(plan) == [INSTALLER_MISSING]


def test_boot_entry_missing():
    plan = dataclasses.replace(
        make_plan("desktop", "2026.09.1"),
        boot_entry=BootEntry(kind="", loader=""),
    )
    assert _codes(plan) == [BOOT_ENTRY_MISSING]


def test_release_missing_version():
    plan = dataclasses.replace(
        make_plan("desktop", "2026.09.1"), release=Release(edition="desktop", version="")
    )
    assert _codes(plan) == [RELEASE_MISSING_VERSION]


def test_edition_mismatch_release():
    # edition core, but the release still names desktop.
    plan = dataclasses.replace(make_plan("desktop", "2026.09.1"), edition=Edition.CORE)
    assert EDITION_MISMATCH_RELEASE in _codes(plan)


def test_os_release_mismatch():
    # os-release names core while the edition is desktop.
    plan = dataclasses.replace(
        make_plan("desktop", "2026.09.1"), os_release=OsRelease(id="core")
    )
    assert OS_RELEASE_MISMATCH in _codes(plan)


def test_multiple_defects_are_all_reported():
    # installer + boot entry + release version all empty, edition wrong.
    plan = MediumPlan(
        kind=MediumKind.ISO,
        edition="tablet",
        release=Release(edition="tablet", version=""),
        os_release=OsRelease(id="tablet"),
        installer=Installer(binary=""),
        boot_entry=BootEntry(kind="", loader=""),
    )
    codes = _codes(plan)
    # invalid edition, missing installer, missing boot entry, missing release.
    for code in (INVALID_EDITION, INSTALLER_MISSING, BOOT_ENTRY_MISSING,
                 RELEASE_MISSING_VERSION):
        assert code in codes


def test_valid_plan_from_serialised_dict():
    # A plan rebuilt from its JSON is well-formed (the round-trip path).
    plan = MediumPlan.from_dict(make_plan("desktop", "2026.09.1").to_dict())
    assert plan_is_valid(plan)


def test_serialised_omitted_installer_is_caught():
    # Simulate a serialized plan whose installer was emptied (item 5's "omits").
    d = make_plan("desktop", "2026.09.1").to_dict()
    d["installer"]["binary"] = ""
    plan = MediumPlan.from_dict(d)
    assert _codes(plan) == [INSTALLER_MISSING]


def test_error_carrys_message():
    (error,) = validate_plan(
        dataclasses.replace(make_plan("desktop", "2026.09.1"), kind="cd")
    )
    assert error.code == INVALID_KIND
    assert isinstance(error.message, str) and error.message


def test_live_fs_format_default_is_squashfs():
    # Sanity: the default format validates (not a validator gap).
    plan = make_plan("desktop", "2026.09.1")
    assert plan.live_rootfs.format == LiveRootfsFormat.SQUASHFS
    assert plan_is_valid(plan)
