"""Feed selection, manifest + signature checks, settings and boot-config parsing."""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from pengyos_update.cli import build_parser
from pengyos_update.release import (
    FeedEntry,
    UpdateError,
    load_keys,
    parse_feed,
    parse_manifest,
    parse_public_key,
    parse_signature,
    parse_version,
    pick_update,
    verify_signed,
)
from pengyos_update.system import (
    cmdline_root_partuuid,
    kernel_args_from_grub_cfg,
    mount_of,
    read_config,
    read_os_release,
    write_config_value,
)

FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:pengyos="https://pengyos.catbee.ca/ns/update/1">
  <title>PengyOS releases</title>
  <entry><title>news only</title><id>urn:x</id></entry>
  <entry>
    <title>PengyOS Desktop 26.10.0</title>
    <pengyos:image>pengyos-desktop</pengyos:image><pengyos:version>26.10.0</pengyos:version>
    <pengyos:channel>testing</pengyos:channel>
    <pengyos:manifest>pengyos-desktop/26.10.0/manifest.json</pengyos:manifest>
  </entry>
  <entry>
    <pengyos:image>pengyos-desktop</pengyos:image><pengyos:version>26.09.1</pengyos:version>
    <pengyos:channel>testing</pengyos:channel><pengyos:channel>stable</pengyos:channel>
    <pengyos:manifest>pengyos-desktop/26.09.1/manifest.json</pengyos:manifest>
  </entry>
  <entry>
    <pengyos:image>pengyos-desktop-nvidia</pengyos:image><pengyos:version>26.11.0</pengyos:version>
    <pengyos:channel>stable</pengyos:channel>
    <pengyos:manifest>pengyos-desktop-nvidia/26.11.0/manifest.json</pengyos:manifest>
  </entry>
</feed>"""


def test_versions():
    assert parse_version("26.09.10") > parse_version("26.09.9")
    with pytest.raises(UpdateError):
        parse_version("26.x")


def test_feed_and_channels():
    entries = parse_feed(FEED.encode())
    assert len(entries) == 3  # the news-only entry is ignored
    assert pick_update(entries, "pengyos-desktop", "stable", "26.09.0").version == "26.09.1"
    assert pick_update(entries, "pengyos-desktop", "testing", "26.09.0").version == "26.10.0"
    assert pick_update(entries, "pengyos-desktop", "stable", "26.09.1") is None
    # never crosses to another image (NVIDIA vs not)
    assert pick_update(entries, "pengyos-server", "stable", "1.0") is None
    with pytest.raises(UpdateError, match="channel"):
        pick_update(entries, "pengyos-desktop", "nightly", "1.0")
    with pytest.raises(UpdateError, match="XML"):
        parse_feed(b"<feed")


def manifest_bytes(**over):
    m = {"format": "pengyos-update-1", "image": "pengyos-desktop", "version": "26.09.1",
         "build": "abc", "files": {
             "rootfs": {"file": "rootfs.ext4.zst", "size": 10, "sha256": "AA" * 32,
                        "raw_size": 20, "raw_sha256": "bb" * 32},
             "kernel": {"file": "bzImage", "size": 5, "sha256": "cc" * 32}}}
    m.update(over)
    return json.dumps(m).encode()


def test_manifest():
    m = parse_manifest(manifest_bytes())
    assert m.rootfs.sha256 == "aa" * 32 and m.rootfs.raw_size == 20 and m.kernel.file == "bzImage"
    with pytest.raises(UpdateError, match="format"):
        parse_manifest(manifest_bytes(format="other"))
    bad = json.loads(manifest_bytes())
    bad["files"]["kernel"]["file"] = "../../etc/passwd"
    with pytest.raises(UpdateError, match="bad file"):
        parse_manifest(json.dumps(bad).encode())
    with pytest.raises(UpdateError, match="malformed"):
        parse_manifest(b'{"format": "pengyos-update-1"}')


crypto = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="no openssl CLI")


def keypair(seed: int):
    k = Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32)
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return k, pub, hashlib.sha256(pub).hexdigest()


def sign(k, keyid, data: bytes) -> bytes:
    d = hashlib.sha256(data).digest()
    return f"pengyos-sig-v1 1 ed25519 {keyid} sha256 {d.hex()}\n".encode() + k.sign(d)


@needs_openssl
def test_signature_checks(tmp_path):
    k, pub, keyid = keypair(7)
    (tmp_path / "release.pub").write_text(f"pengyos-key-v1 ed25519 {keyid} {pub.hex()}\n")
    keys = load_keys(tmp_path)
    data = manifest_bytes()
    assert verify_signed(data, sign(k, keyid, data), keys).keyid == keyid
    # changed manifest
    with pytest.raises(UpdateError, match="does not match"):
        verify_signed(data + b" ", sign(k, keyid, data), keys)
    # signed by a key the image does not trust
    k2, _, keyid2 = keypair(9)
    with pytest.raises(UpdateError, match="unknown key"):
        verify_signed(data, sign(k2, keyid2, data), keys)
    # right key id, wrong signature bytes
    blob = bytearray(sign(k, keyid, data))
    blob[-1] ^= 1
    with pytest.raises(UpdateError, match="NOT valid"):
        verify_signed(data, bytes(blob), keys)
    with pytest.raises(UpdateError, match="malformed"):
        parse_signature(b"garbage")


def test_public_key_format():
    _, pub, keyid = keypair(3)
    assert parse_public_key(f"pengyos-key-v1 ed25519 {keyid} {pub.hex()}").raw == pub
    with pytest.raises(UpdateError, match="damaged"):
        parse_public_key(f"pengyos-key-v1 ed25519 {'0' * 64} {pub.hex()}")


def test_shipped_release_key_is_valid():
    import pathlib
    keys = pathlib.Path(__file__).resolve().parents[3] / (
        "buildroot-external/board/server/rootfs-overlay/usr/share/pengyos/keys")
    assert load_keys(keys), "the image must ship at least one release key"


def test_config(tmp_path):
    p = tmp_path / "update.conf"
    assert read_config(str(p))["channel"] == "stable"
    p.write_text("# settings\nfeed = https://example/feed.xml\nchannel = stable  # default\n")
    write_config_value("channel", "testing", str(p))
    cfg = read_config(str(p))
    assert cfg == {"feed": "https://example/feed.xml", "channel": "testing"}
    assert p.read_text().startswith("# settings\n")


def test_os_release(tmp_path):
    p = tmp_path / "os-release"
    p.write_text('NAME="PengyOS"\nIMAGE_ID=pengyos-desktop\nIMAGE_VERSION="26.09.0"\n')
    assert read_os_release(str(p))["IMAGE_VERSION"] == "26.09.0"


def test_cmdline_and_grub_cfg():
    assert cmdline_root_partuuid("BOOT_IMAGE=/slot-a/bzImage root=PARTUUID=ABC-1 ro") == "abc-1"
    with pytest.raises(UpdateError, match="live stick"):
        cmdline_root_partuuid("root=/dev/sda2 ro")
    cfg = ("menuentry x {\n    linux /slot-a/bzImage root=PARTUUID=8783 ro rootwait "
           "init=/usr/lib/pengyos/preinit pengyos.data=4 splash\n}\n")
    assert kernel_args_from_grub_cfg(cfg) == "ro rootwait init=/usr/lib/pengyos/preinit pengyos.data=4 splash"
    assert kernel_args_from_grub_cfg("nothing") is None


def test_grub_cfg_roundtrip():
    """What apply writes keeps the installed kernel arguments."""
    from pengyos_install.image import render_grub_cfg
    cfg = render_grub_cfg({"a": "AAA", "b": "BBB"}, "ro quiet pengyos.data=4")
    assert kernel_args_from_grub_cfg(cfg) == "ro quiet pengyos.data=4 ${pengyos_serial}"
    assert "--id pengyos-b" in cfg and "save_env pengyos_try" in cfg
    # apply re-renders from what it reads back: stable, one serial variable.
    again = render_grub_cfg({"a": "AAA", "b": "BBB"}, kernel_args_from_grub_cfg(cfg))
    assert again == cfg and again.count("${pengyos_serial}") == 2  # one per entry


def test_grub_cfg_from_before_the_serial_probe():
    """An install from before 26.10.5 (fixed console=ttyS0) gets the probe."""
    from pengyos_install.image import render_grub_cfg
    old = ("menuentry x {\n    linux /slot-a/bzImage root=PARTUUID=8783 ro pengyos.data=4 "
           "console=tty0 console=ttyS0,115200\n}\n")
    new = render_grub_cfg({"a": "8783"}, kernel_args_from_grub_cfg(old))
    assert "console=ttyS0,115200\n" not in new.split("fi\n")[-1]
    assert "root=PARTUUID=8783 ro pengyos.data=4 console=tty0 ${pengyos_serial}\n" in new


def test_parser():
    p = build_parser()
    a = p.parse_args(["apply", "--reboot", "--version", "26.09.1"])
    assert a.reboot and a.version == "26.09.1"
    assert p.parse_args(["check", "--channel", "testing"]).channel == "testing"
    assert FeedEntry("i", "1.0", ("stable",), "m").title == ""


def test_mount_of(tmp_path):
    m = tmp_path / "mounts"
    m.write_text("systemd-1 /efi autofs rw,relatime 0 0\n"
                 "/dev/nvme0n1p1 /efi vfat rw,relatime,fmask=0077 0 0\n"
                 "/dev/nvme0n1p2 / ext4 ro,relatime 0 0\n")
    assert mount_of("/dev/nvme0n1p1", str(m)) == ("/efi", ["rw", "relatime", "fmask=0077"])
    assert mount_of("/dev/nvme0n1p3", str(m)) is None


def test_feed_notes():
    xml = FEED.replace("<pengyos:version>26.10.0</pengyos:version>",
                       "<pengyos:version>26.10.0</pengyos:version><pengyos:notes>Faster.\n\nFixes.</pengyos:notes>")
    e = pick_update(parse_feed(xml.encode()), "pengyos-desktop", "testing", "26.09.0")
    assert e.notes == "Faster.\n\nFixes."


def test_progress_file(tmp_path):
    from pengyos_update.state import Progress
    p = tmp_path / "run" / "progress.json"
    prog = Progress(str(p))
    prog.update("download", 0.5, "Downloading")
    d = json.loads(p.read_text())
    assert d["phase"] == "download" and d["fraction"] == pytest.approx(0.02 + 0.43 * 0.5, abs=1e-3)
    prog.update("done", 1.0, "ready", version="26.10.0")
    d = json.loads(p.read_text())
    assert d == {**d, "phase": "done", "fraction": 1.0, "version": "26.10.0"}
    assert oct(p.stat().st_mode & 0o777) == "0o644"
    Progress(None).update("write", 0.3)  # no file: no-op


def test_check_save_and_motd(tmp_path, monkeypatch):
    from pengyos_update import cli
    monkeypatch.setattr(cli, "fetch", lambda url: FEED.encode())
    monkeypatch.setattr(cli, "identity", lambda: ("pengyos-desktop", "26.09.0",
                                                  {"NAME": "PengyOS", "VARIANT": "Desktop"}))
    monkeypatch.setattr(cli, "read_config", lambda: {"feed": "https://x/feed.xml", "channel": "stable"})
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    state, motd = tmp_path / "state.json", tmp_path / "motd"
    argv = ["check", "--save", "--quiet", "--state", str(state), "--motd", str(motd)]
    assert cli.main(argv) == 0
    s = json.loads(state.read_text())
    assert s["available"]["version"] == "26.09.1" and s["name"] == "PengyOS Desktop"
    assert s["error"] is None and s["checked"]
    assert "26.09.1 is available" in motd.read_text()

    def offline(url):
        raise UpdateError("could not download")
    monkeypatch.setattr(cli, "fetch", offline)
    assert cli.main(argv) == 0  # offline: recorded, not fatal
    s = json.loads(state.read_text())
    assert s["error"] and s["available"]["version"] == "26.09.1"  # last answer kept

    monkeypatch.setattr(cli, "fetch", lambda url: FEED.encode())
    monkeypatch.setattr(cli, "identity", lambda: ("pengyos-desktop", "26.09.1", {}))
    assert cli.main(argv) == 0
    assert json.loads(state.read_text())["available"] is None and not motd.exists()


def test_hook_selection(tmp_path, monkeypatch):
    """run_hooks runs the new root's executable update steps in order, stops on failure."""
    from pengyos_update import cli
    root = tmp_path / "new"
    hooks = root / cli.HOOKS_DIR
    hooks.mkdir(parents=True)
    log = tmp_path / "log"
    for name, rc in (("20-second", 0), ("10-first", 0), ("README", None), ("30-fails", 3)):
        f = hooks / name
        f.write_text(f"#!/bin/sh\necho {name} $PENGYOS_TO >> {log}\nexit {rc or 0}\n")
        if rc is not None:
            f.chmod(0o755)
    monkeypatch.setattr(cli, "NEW_ROOT", str(root))
    monkeypatch.setattr(cli, "run", lambda *a, **k: None)  # no real mount
    with pytest.raises(UpdateError, match="30-fails"):
        cli.run_hooks("/dev/null", {"PENGYOS_TO": "26.10.0"})
    assert log.read_text().splitlines() == ["10-first 26.10.0", "20-second 26.10.0", "30-fails 26.10.0"]
