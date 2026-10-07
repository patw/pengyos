"""Tests that the Desktop board is in a boot-ready state.

Booting the Desktop to a joined Wi-Fi link needs a full image build and a
boot, which is done by hand (docs/qemu-testing.md); the kconfig resolve is a
suite test in ``tests/test_board_network.py``.

This module closes the gap between "the wiring is correct" and "the
board actually boots": it asserts the board's **network-relevant build files**
are present and well-formed, so a human's rung-e boot starts from a
known-good board rather than a broken one. These are the files the image-build
pipeline consumes:

* ``post-build.sh`` drops the ``cosmic-applets`` bundle (which provides
  ``cosmic-applet-network``, the GUI path to Wi-Fi) from the cosmic-build
  release;
* ``post-image.sh`` assembles the bootable disk image via ``genimage``;
* ``genimage.cfg`` describes a bootable GPT image (EFI System Partition + ext4
  root) so the kernel can find root by PARTUUID;
* the rootfs overlay ships the network files (NetworkManager config,
  ``wpa_supplicant.conf``, ``wifi-firmware.txt``, the COSMIC panel default +
  cosmic-settings network defaults) and pins ``cosmic-applet-network``;
* the Desktop defconfig selects the real stock ``BR2_PACKAGE_NETWORK_MANAGER``
  symbol + nmcli + ``BR2_PACKAGE_WPA_SUPPLICANT`` (the wiring rung b asserts).

The suite is standard-library-only at test time, so it needs no network, root,
display, or hardware — it only reads committed files.
"""

from __future__ import annotations

import re
from pathlib import Path

from pengyos_desktop_net import lint as ln
from pengyos_desktop_net import profile as prof

# Repo root = parent of tools/ (the Buildroot-external board lives here).
ROOT = Path(__file__).resolve().parents[3]
BOARD_DIR = ROOT / "buildroot-external" / "board" / "desktop"
BOARD_OVERLAY = BOARD_DIR / "rootfs-overlay"
DESKTOP_DEFCONFIG = ROOT / "buildroot-external" / "configs" / "pengyos_desktop_defconfig"

# The *correct* stock 2026.08 symbol for NetworkManager (with underscore).
NM_SYMBOL = "BR2_PACKAGE_NETWORK_MANAGER"
NM_CLI_SYMBOL = "BR2_PACKAGE_NETWORK_MANAGER_CLI"
WPA_SUPPLICANT_SYMBOL = "BR2_PACKAGE_WPA_SUPPLICANT"
# The hallucinated no-underscore name the task warned about.
HALLUCINATED_NM_SYMBOL = "BR2_PACKAGE_NETWORKMANAGER"

# A ``BR2_X=y`` assignment line (Buildroot defconfigs use the bare name, no
# ``CONFIG_`` prefix).
ASSIGN_RE = re.compile(r"^(?P<name>BR2_[A-Z0-9_]+)=\s*y\s*$", re.MULTILINE)


def _board_is_present() -> bool:
    return BOARD_DIR.is_dir()


def _defconfig_text() -> str:
    assert DESKTOP_DEFCONFIG.is_file(), (
        f"Desktop defconfig missing at {DESKTOP_DEFCONFIG}"
    )
    return DESKTOP_DEFCONFIG.read_text("utf-8")


def _assigned_symbols(text: str) -> set[str]:
    return set(ASSIGN_RE.findall(text))


def _post_build_text() -> str:
    path = BOARD_DIR / "post-build.sh"
    assert path.is_file(), f"board post-build.sh missing at {path}"
    return path.read_text("utf-8")


def _post_image_text() -> str:
    path = BOARD_DIR / "post-image.sh"
    assert path.is_file(), f"board post-image.sh missing at {path}"
    return path.read_text("utf-8")


def _genimage_cfg_text() -> str:
    path = BOARD_DIR / "genimage.cfg"
    assert path.is_file(), f"board genimage.cfg missing at {path}"
    return path.read_text("utf-8")


def _overlay_files() -> dict[str, str]:
    """Return ``{overlay-relative-path: text}`` for the board overlay.

    Skips symlinks (systemd unit-enablement links; some are absolute and dangle
    on the host) and binary assets (e.g. the wallpaper PNG) — neither is a text
    overlay file these assertions care about.
    """
    import os
    files: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(BOARD_OVERLAY):
        for name in filenames:
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            rel = full.relative_to(BOARD_OVERLAY).as_posix()
            try:
                files[rel] = full.read_text("utf-8")
            except (UnicodeDecodeError, OSError):
                continue
    return files


# --- hermetic: the board is present and the Desktop defconfig wires NM --------

def test_board_present():
    """The Desktop qemu board directory exists (the rung-e boot target)."""
    assert _board_is_present(), (
        f"board directory missing at {BOARD_DIR} — nothing to boot"
    )


def test_defconfig_present():
    """The Desktop defconfig is present to configure against."""
    assert DESKTOP_DEFCONFIG.is_file(), (
        f"Desktop defconfig missing at {DESKTOP_DEFCONFIG}"
    )


def test_defconfig_selects_real_networkmanager():
    """The Desktop defconfig selects the real ``BR2_PACKAGE_NETWORK_MANAGER``."""
    symbols = _assigned_symbols(_defconfig_text())
    assert NM_SYMBOL in symbols, (
        "the Desktop defconfig must select "
        f"{NM_SYMBOL}=y (NetworkManager is the network service the boot manages)"
    )


def test_defconfig_selects_nmcli_and_wpa():
    """The Desktop defconfig enables nmcli + wpa_supplicant (the boot path)."""
    symbols = _assigned_symbols(_defconfig_text())
    assert NM_CLI_SYMBOL in symbols, (
        f"the Desktop defconfig must select {NM_CLI_SYMBOL}=y (nmcli is the "
        "CLI path to NetworkManager, used to confirm the managed link at boot)"
    )
    assert WPA_SUPPLICANT_SYMBOL in symbols, (
        f"the Desktop defconfig must keep {WPA_SUPPLICANT_SYMBOL}=y (the "
        "Wi-Fi backend NetworkManager drives at boot)"
    )


def test_defconfig_uses_real_symbol_not_hallucination():
    """The defconfig uses the underscored symbol, not the hallucinated one."""
    text = _defconfig_text()
    assert HALLUCINATED_NM_SYMBOL not in text, (
        f"the Desktop defconfig must not mention the non-existent "
        f"{HALLUCINATED_NM_SYMBOL} (select the real {NM_SYMBOL} instead)"
    )


# --- hermetic: post-build.sh drops the cosmic-applets (network applet) bundle --

def test_post_build_drops_cosmic_applets():
    """post-build.sh drops the cosmic-applets bundle (provides the applet).

    ``cosmic-applet-network`` ships from the cosmic-build release, so the
    board's post-build.sh copies the cosmic-applets bundle into the image — the
    GUI path to Wi-Fi ships from cosmic-build, not a Buildroot recipe.
    """
    content = _post_build_text()
    assert "cosmic-applets" in content, (
        "the board's post-build.sh must drop the cosmic-applets bundle "
        "(which provides cosmic-applet-network) from the cosmic-build release"
    )


# --- hermetic: post-image.sh assembles the bootable image via genimage --------

def test_post_image_assembles_genimage():
    """post-image.sh assembles the bootable disk image with genimage."""
    content = _post_image_text()
    assert "genimage" in content, (
        "the board's post-image.sh must invoke genimage to assemble the "
        "bootable disk image (the rung-e boot boots this image)"
    )


def test_genimage_cfg_has_bootable_gpt_image():
    """genimage.cfg describes a bootable GPT image (EFI + ext4 root)."""
    content = _genimage_cfg_text()
    # A bootable GPT disk: the partition table type is gpt...
    assert "partition-table-type" in content and ("gpt" in content.lower() or "hybrid" in content.lower()), (
        "genimage.cfg must describe a GPT (or hybrid MBR+GPT) partition table"
    )
    # ...with a bootable EFI System Partition and an ext4 root partition, so the
    # kernel can find root by PARTUUID at boot.
    assert "bootable = true" in content, (
        "genimage.cfg must mark the EFI partition bootable"
    )
    assert "rootfs" in content or "root" in content.lower(), (
        "genimage.cfg must describe an ext4 root partition"
    )


# --- hermetic: the overlay ships the network files, pins the applet, no networkd

def test_overlay_ships_network_files():
    """The overlay ships every network-relevant file the boot consumes."""
    board = _overlay_files()
    expected = {
        "etc/NetworkManager/NetworkManager.conf",
        "etc/NetworkManager/conf.d/10-pengyos.conf",
        "etc/wpa_supplicant.conf",
        "wifi-firmware.txt",
        "etc/skel/.config/cosmic/panel.toml",
        "etc/skel/.config/cosmic-settings/network.toml",
    }
    missing = expected - set(board)
    assert not missing, f"board overlay missing network files: {sorted(missing)}"


def test_overlay_pins_network_applet():
    """The overlay's panel default pins cosmic-applet-network (the GUI path)."""
    board = _overlay_files()
    panel = next((c for rel, c in board.items() if rel.endswith("panel.toml")), None)
    assert panel is not None, "board overlay has no panel.toml"
    assert f'applet="{prof.COSMIC_APPLET_NETWORK}"' in panel, (
        f"the overlay's panel default must pin {prof.COSMIC_APPLET_NETWORK}"
    )


def test_overlay_has_no_networkd_ownership():
    """systemd-networkd does not own a link in the overlay (NM is sole service)."""
    board = _overlay_files()
    network_files = [rel for rel in board if rel.lower().endswith(".network")]
    assert not network_files, (
        f"systemd-networkd owns a link via {network_files}; "
        "NetworkManager must be the sole network service at boot"
    )


def test_overlay_passes_lint():
    """The overlay passes every lint invariant (boot-ready, lint-clean)."""
    result = ln.lint_overlay(str(BOARD_OVERLAY))
    assert result.ok, result.messages
