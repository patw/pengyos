"""The installer ISO (docs/iso-installer.md) is wired end to end: kernel,
Buildroot settings, initramfs, post-image, and the installers that find its
payload."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXT = ROOT / "buildroot-external"
INIT = EXT / "board/common/iso/init"
ISO_SH = EXT / "board/common/iso-image.sh"


def test_kernel_builds_erofs_in():
    frag = (EXT / "board/core/linux.fragment").read_text()
    for opt in ("CONFIG_EROFS_FS=y", "CONFIG_EROFS_FS_ZIP=y"):
        assert opt in frag


def test_server_layer_builds_the_iso_inputs():
    layer = (EXT / "configs/layers/server.config").read_text()
    for opt in ("BR2_TARGET_ROOTFS_EROFS=y", "BR2_TARGET_ROOTFS_EROFS_LZ4HC=y",
                "BR2_PACKAGE_HOST_XORRISO=y", "BR2_PACKAGE_PENGYOS_INITRAMFS_BUSYBOX=y"):
        assert opt in layer
    for ed in ("server", "server_nvidia", "desktop", "desktop_nvidia"):
        cfg = (EXT / f"configs/pengyos_{ed}_defconfig").read_text()
        assert "BR2_TARGET_ROOTFS_EROFS=y" in cfg, ed
        assert "BR2_PACKAGE_PENGYOS_INITRAMFS_BUSYBOX=y" in cfg, ed


def test_both_editions_build_the_iso_after_the_payload():
    for ed in ("server", "desktop"):
        post = (EXT / f"board/{ed}/post-image.sh").read_text()
        assert post.index("installer-image.sh") < post.index("iso-image.sh"), ed
        assert f'iso-image.sh" {ed} pengyos-{ed}-x86_64.iso "$UUID"' in post


def test_init_mounts_what_the_live_system_expects():
    init = INIT.read_text()
    assert "mount -t erofs -o ro,loop /medium/live/rootfs.erofs" in init
    assert "/newroot/run/pengyos/payload" in init
    # podman cannot store containers on overlayfs
    assert "/newroot/var/lib/containers" in init
    assert "exec switch_root /newroot /sbin/init" in init
    # the payload path is the installer's
    image = (ROOT / "tools/pengyos-install/src/pengyos_install/image.py").read_text()
    assert 'PAYLOAD_MOUNT = "/run/pengyos/payload"' in image


def test_initramfs_busybox_has_every_applet_init_uses():
    cfg = (EXT / "package/pengyos-initramfs-busybox/busybox.config").read_text()
    needed = {"mount": "MOUNT", "umount": "UMOUNT", "switch_root": "SWITCH_ROOT",
              "mkdir": "MKDIR", "cat": "CAT", "sleep": "SLEEP", "sed": "SED"}
    init = INIT.read_text()
    for cmd, sym in needed.items():
        if re.search(rf"\b{cmd}\b", init):
            assert f"CONFIG_{sym}=y" in cfg, cmd
    assert "CONFIG_STATIC=y" in cfg
    assert "CONFIG_BUSYBOX=y" in cfg and "CONFIG_FEATURE_INSTALLER=y" in cfg  # busybox --install
    assert "CONFIG_USE_BB_CRYPT=y" in cfg  # no static -lcrypt here
    assert "CONFIG_FEATURE_MOUNT_LOOP=y" in cfg
    assert "CONFIG_FEATURE_FLOAT_SLEEP=y" in cfg  # sleep 0.2
    if "$((" in init:
        assert "CONFIG_FEATURE_SH_MATH=y" in cfg  # ash arithmetic, or /init dies at boot


def test_the_medium_id_ties_grub_initramfs_and_iso_together():
    iso = ISO_SH.read_text()
    assert 'echo "$ID" > "$STAGE/pengyos/medium-id"' in iso
    assert "search --no-floppy --set=root --file /pengyos/medium-id" in iso
    assert "pengyos.live=%s" in iso
    assert "/medium/pengyos/medium-id" in INIT.read_text()


def test_installers_find_the_iso_payload_or_the_legacy_partition():
    unit = (EXT / "board/desktop/rootfs-overlay/etc/systemd/system/pengyos-live-installer.service").read_text()
    assert "ConditionPathExists=|/run/pengyos/payload/manifest.json" in unit
    assert "ConditionPathExists=|/dev/disk/by-label/PENGYOS-PAYLOAD" in unit
    assert "ConditionKernelCommandLine=!pengyos.data" in unit
    console = (EXT / "board/server/rootfs-overlay/usr/libexec/pengyos/console-installer").read_text()
    assert '[ -e "$PAYLOAD/manifest.json" ] || [ -e "$LABEL" ] || exit 0' in console
