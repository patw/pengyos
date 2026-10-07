"""Tests for the pinned PengyOS ESP boot-chain layout (``bootchain``).

This pins the *concrete, researched* set of EFI binaries the PengyOS ESP ships
and that ``sign-chain``/``lint`` account for:

* ``/EFI/systemd/systemd-bootx64.efi`` -- systemd-boot at its ``bootctl install``
  path;
* ``/EFI/BOOT/BOOTX64.EFI`` -- the UEFI-spec mandatory fallback;
* ``/EFI/Linux/pengyos-<edition>+<ver>-<slot>.efi`` -- one UKI per installed
  version/slot (the A/B boot state).

Pure data, no I/O, no crypto, no network. The pinned layout is asserted against
the documented artifact layout and the canonical systemd-boot install paths.
"""

import pytest

from pengyos_secureboot_enroll import bootchain as bc


# --- pinned constants -------------------------------------------------------


def test_systemd_boot_path_is_bootctl_install_path():
    # The systemd-boot boot manager lives at its `bootctl install` path.
    assert bc.SYSTEMD_BOOT_PATH == "/EFI/systemd/systemd-bootx64.efi"


def test_systemd_boot_fallback_is_uefi_spec_fallback():
    # The mandatory UEFI-spec fallback, a copy of systemd-boot.
    assert bc.SYSTEMD_BOOT_FALLBACK_PATH == "/EFI/BOOT/BOOTX64.EFI"


def test_efi_linux_dir_is_bls_uki_directory():
    # Signed UKIs follow the Boot Loader Specification `EFI/Linux` convention.
    assert bc.EFI_LINUX_DIR == "EFI/Linux"


def test_fixed_bootloader_paths_are_canonical_posix():
    # Canonical POSIX form: leading slash, no trailing slash, no `..`.
    assert bc.SYSTEMD_BOOT_PATH.startswith("/")
    assert not bc.SYSTEMD_BOOT_PATH.endswith("/")
    assert ".." not in bc.SYSTEMD_BOOT_FALLBACK_PATH


# --- uki_path_for -----------------------------------------------------------


def test_uki_path_for_matches_pinned_artifact_layout():
    # install-update-architecture.md: EFI/Linux/pengyos-<edition>+<ver>-<slot>.efi
    assert bc.uki_path_for("server", "1.0.0", "a") == "/EFI/Linux/pengyos-server+1.0.0-a.efi"


def test_uki_path_for_other_slot_and_version():
    assert bc.uki_path_for("desktop", "2.3.1", "b") == "/EFI/Linux/pengyos-desktop+2.3.1-b.efi"


def test_uki_path_for_is_canonical_posix():
    path = bc.uki_path_for("server", "1.0.0", "a")
    assert path.startswith("/")
    assert not path.endswith("/")
    assert path.endswith(".efi")


# --- fixed_bootloader_entries -----------------------------------------------


def test_fixed_bootloader_entries_are_two_in_boot_order():
    entries = bc.fixed_bootloader_entries()
    assert [e.path for e in entries] == [
        "/EFI/systemd/systemd-bootx64.efi",
        "/EFI/BOOT/BOOTX64.EFI",
    ]


def test_fixed_bootloader_entries_have_descriptions():
    for entry in bc.fixed_bootloader_entries():
        assert entry.description, "every fixed entry needs a human description"


def test_fixed_bootloader_entries_key_id_is_none_until_keys_subtask():
    # key_id = None means "any db key is acceptable" (truststate semantics).
    for entry in bc.fixed_bootloader_entries():
        assert entry.key_id is None


# --- uki_entry ------------------------------------------------------------


def test_uki_entry_matches_uki_path_for():
    entry = bc.uki_entry("server", "1.0.0", "a")
    assert entry.path == bc.uki_path_for("server", "1.0.0", "a")
    assert entry.key_id is None
    assert "server" in entry.description
    assert "1.0.0" in entry.description
    assert "slot a" in entry.description


# --- expected_manifest ------------------------------------------------------


def test_expected_manifest_one_slot_has_three_entries():
    manifest = bc.expected_manifest("server", "1.0.0", ["a"])
    assert [e.path for e in manifest] == [
        "/EFI/systemd/systemd-bootx64.efi",
        "/EFI/BOOT/BOOTX64.EFI",
        "/EFI/Linux/pengyos-server+1.0.0-a.efi",
    ]


def test_expected_manifest_two_slots_appends_second_uki():
    manifest = bc.expected_manifest("server", "1.0.0", ["a", "b"])
    assert [e.path for e in manifest] == [
        "/EFI/systemd/systemd-bootx64.efi",
        "/EFI/BOOT/BOOTX64.EFI",
        "/EFI/Linux/pengyos-server+1.0.0-a.efi",
        "/EFI/Linux/pengyos-server+1.0.0-b.efi",
    ]


def test_expected_manifest_slots_are_insertion_ordered():
    # The A/B state is an ordered list; the manifest preserves that order.
    manifest = bc.expected_manifest("server", "1.0.0", ["b", "a"])
    assert [e.path for e in manifest][2:] == [
        "/EFI/Linux/pengyos-server+1.0.0-b.efi",
        "/EFI/Linux/pengyos-server+1.0.0-a.efi",
    ]


def test_expected_manifest_requires_at_least_one_slot():
    with pytest.raises(ValueError, match="at least one installed slot"):
        bc.expected_manifest("server", "1.0.0", [])


def test_expected_manifest_dedupes_identical_paths():
    # Two slots that resolve to the same UKI path collapse to one entry.
    manifest = bc.expected_manifest("server", "1.0.0", ["a", "a"])
    assert len(manifest) == 3  # systemd-boot + fallback + one UKI


def test_expected_manifest_all_key_ids_none_until_keys_subtask():
    manifest = bc.expected_manifest("server", "1.0.0", ["a", "b"])
    assert all(e.key_id is None for e in manifest)
    # key_ids() only reports pinned ids; with none pinned it is empty.
    assert manifest.key_ids() == []


def test_expected_manifest_to_json_structure():
    manifest = bc.expected_manifest("server", "1.0.0", ["a"])
    data = manifest.to_json()
    assert list(data) == ["binaries"]
    entry = data["binaries"][0]
    assert entry["path"] == "/EFI/systemd/systemd-bootx64.efi"
    assert entry["key_id"] is None
    assert entry["description"]
    assert len(data["binaries"]) == 3


def test_expected_manifest_contains_all_expected_paths():
    manifest = bc.expected_manifest("server", "1.0.0", ["a"])
    assert "/EFI/systemd/systemd-bootx64.efi" in manifest
    assert "/EFI/BOOT/BOOTX64.EFI" in manifest
    assert bc.uki_path_for("server", "1.0.0", "a") in manifest
    assert "/EFI/BOOT/WRONG.efi" not in manifest


def test_expected_manifest_lookup_by_path():
    manifest = bc.expected_manifest("server", "1.0.0", ["a"])
    uki = manifest.get(bc.uki_path_for("server", "1.0.0", "a"))
    assert uki is not None
    assert "kernel image" in uki.description
