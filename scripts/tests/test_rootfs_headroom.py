"""board/common/rootfs-headroom.sh: a nearly full rootfs fails the image step."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "buildroot-external/board/common/rootfs-headroom.sh"
SBIN = os.pathsep.join(["/sbin", "/usr/sbin", os.environ.get("PATH", "")])


def _mkfs():
    return shutil.which("mkfs.ext4", path=SBIN)


def _run(img, min_free):
    env = dict(os.environ, PATH=SBIN, PENGYOS_ROOTFS_MIN_FREE_MB=str(min_free))
    return subprocess.run(["sh", str(SCRIPT), str(img)], env=env, capture_output=True, text=True)


@pytest.mark.skipif(not _mkfs(), reason="needs e2fsprogs")
def test_passes_with_room_and_fails_without(tmp_path):
    img = tmp_path / "rootfs.ext2"
    img.write_bytes(b"")
    os.truncate(img, 32 << 20)
    subprocess.run([_mkfs(), "-q", "-F", str(img)], check=True)
    ok = _run(img, 1)
    assert ok.returncode == 0, ok.stderr
    assert "MiB free of 32 MiB" in ok.stdout
    full = _run(img, 100)
    assert full.returncode == 1
    assert "BR2_TARGET_ROOTFS_EXT2_SIZE" in full.stderr


def test_every_edition_checks_before_genimage():
    for board in ("core", "server", "desktop"):
        post = (ROOT / f"buildroot-external/board/{board}/post-image.sh").read_text()
        check = post.index('rootfs-headroom.sh" "$BINARIES_DIR/rootfs.ext2"')
        assert check < post.index("support/scripts/genimage.sh"), board
