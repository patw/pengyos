"""Tests for the image install path (pengyos_install.image)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pengyos_install import image as im
from pengyos_install.image import (
    Disk,
    ImageInstallError,
    ProvisionAnswers,
    install_image,
    list_disks,
    partition_path,
    KERNEL_ARGS,
    render_grub_cfg,
)

ANSWERS = ProvisionAnswers(hostname="pengy-box", username="pat", password="hunter22")


def test_partition_path():
    assert partition_path("/dev/sda", 2) == "/dev/sda2"
    assert partition_path("/dev/vda", 1) == "/dev/vda1"
    assert partition_path("/dev/nvme0n1", 2) == "/dev/nvme0n1p2"
    assert partition_path("/dev/mmcblk0", 1) == "/dev/mmcblk0p1"


def test_render_grub_cfg():
    cfg = render_grub_cfg({"a": "AAAA-1", "b": "bbbb-2"})
    assert 'set timeout_style="hidden"' in cfg and 'set timeout="1"' in cfg
    assert "load_env" in cfg
    assert 'if [ "${pengyos_slot}" = "b" ]; then set default="pengyos-b"; fi' in cfg
    assert ("linux /slot-a/bzImage root=PARTUUID=aaaa-1 ro rootwait "
            "init=/usr/lib/pengyos/preinit pengyos.data=4") in cfg
    assert "--id pengyos-b" in cfg and "/slot-b/bzImage root=PARTUUID=bbbb-2" in cfg
    assert "pengyos-b" not in render_grub_cfg({"a": "x"}).split("fi\n", 3)[-1]
    # One-shot trial boot of a freshly written slot (pengyos-update), cleared
    # before it boots so a dead image falls back to the committed slot.
    assert 'if [ -n "${pengyos_try}" ]; then' in cfg
    assert 'set default="pengyos-${pengyos_try}"' in cfg and "save_env pengyos_try" in cfg
    assert cfg.index("save_env pengyos_try") < cfg.index("menuentry")
    custom = render_grub_cfg({"a": "x"}, kernel_args="ro quiet")
    assert "root=PARTUUID=x ro quiet ${pengyos_serial}\n" in custom


def test_render_grub_cfg_quiets_the_kernel_on_a_real_uart_but_not_qemu():
    cfg = render_grub_cfg({"a": "x"})
    quiet = (
        '    smbios --type 1 --get-string 4 --set pengyos_vendor\n'
        '    if [ "${pengyos_vendor}" != "QEMU" ]; then\n'
        '        set pengyos_serial="loglevel=4 console=ttyS0,115200"\n'
        '    fi\nfi\n'
    )
    assert quiet in cfg and cfg.index(quiet) < cfg.index("menuentry")
    assert "loglevel" not in KERNEL_ARGS


def test_render_grub_cfg_serial_console_only_where_grub_finds_a_uart():
    cfg = render_grub_cfg({"a": "x", "b": "y"})
    assert "console=ttyS0" not in KERNEL_ARGS
    probe = 'if serial --unit=0 --speed=115200; then\n    set pengyos_serial="console=ttyS0,115200"\n'
    assert probe in cfg and cfg.index(probe) < cfg.index("menuentry")
    for line in (l for l in cfg.splitlines() if l.lstrip().startswith("linux ")):
        assert line.endswith(" console=tty0 ${pengyos_serial}")
        assert "console=ttyS0" not in line


@pytest.mark.parametrize("old", [
    "ro quiet console=tty0 console=ttyS0,115200",          # before 2026-10-03
    "ro quiet console=tty0 ${pengyos_serial}",             # re-read from a new grub.cfg
])
def test_render_grub_cfg_rewrites_an_older_serial_console(old):
    line = next(l for l in render_grub_cfg({"a": "x"}, kernel_args=old).splitlines()
                if l.lstrip().startswith("linux "))
    assert line.endswith("root=PARTUUID=x ro quiet console=tty0 ${pengyos_serial}")


@pytest.mark.parametrize(
    "answers",
    [
        ProvisionAnswers("bad_host", "pat", "hunter22"),
        ProvisionAnswers("-lead", "pat", "hunter22"),
        ProvisionAnswers("ok", "Pat", "hunter22"),
        ProvisionAnswers("ok", "root", "hunter22"),
        ProvisionAnswers("ok", "pat", "short"),
        ProvisionAnswers("ok", "pat", "has:colon"),
        ProvisionAnswers("ok", "pat", "hunter22", authorized_keys="not a key"),
        ProvisionAnswers("ok", "pat", "hunter22", authorized_keys="ssh-ed25519\n"),
    ],
)
def test_answers_rejected(answers):
    with pytest.raises(ImageInstallError):
        answers.validate()


def _fake_sys(tmp_path: Path):
    """A fake /sys with sda (boot, holds / via sda2), vda (target), loop0, sr0."""
    sys_root = tmp_path / "sys"
    devices = sys_root / "devices"
    for name, sectors, removable, model in (
        ("sda", 4_000_000, "1", "USB Stick"),
        ("vda", 20_000_000, "0", "0x1af4"),
        ("nvme0n1", 0, "0", "empty"),
        ("loop0", 100, "0", ""),
        ("sr0", 100, "1", ""),
    ):
        d = devices / name
        (d / "device").mkdir(parents=True)
        (d / "size").write_text(str(sectors))
        (d / "removable").write_text(removable)
        (d / "device" / "model").write_text(model)
        (sys_root / "block").mkdir(parents=True, exist_ok=True)
        (sys_root / "block" / name).symlink_to(d)
    part = devices / "sda" / "sda2"
    part.mkdir()
    (part / "partition").write_text("2")
    (sys_root / "dev" / "block").mkdir(parents=True)
    (sys_root / "dev" / "block" / "8:2").symlink_to(part)
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("22 1 8:2 / / rw - ext4 /dev/sda2 rw\n")
    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/sda2 / ext4 rw 0 0\n/dev/sda1 /boot vfat rw 0 0\n")
    return sys_root, mounts, mountinfo


def test_list_disks(tmp_path):
    sys_root, mounts, mountinfo = _fake_sys(tmp_path)
    disks = {d.name: d for d in list_disks(sys_root=sys_root, mounts=mounts, mountinfo=mountinfo)}
    assert set(disks) == {"sda", "vda"}  # loop/sr skipped, 0-size skipped
    assert disks["sda"].is_boot and disks["sda"].removable
    assert disks["sda"].mounted == ["/", "/boot"]
    assert not disks["vda"].is_boot and disks["vda"].mounted == []
    assert disks["vda"].size_bytes == 20_000_000 * 512
    assert disks["sda"].model == "USB Stick"
    assert disks["vda"].model == ""  # a bare PCI vendor id is not a model


def _payload(tmp_path: Path, rootfs: bytes, esp: bytes = b"E" * 4096) -> Path:
    payload = tmp_path / "payload"
    payload.mkdir()
    files = {}
    for key, raw, name in (("rootfs", rootfs, "rootfs.ext4.zst"), ("esp", esp, "esp.vfat.zst")):
        raw_file = tmp_path / f"{key}.raw"
        raw_file.write_bytes(raw)
        subprocess.run(["zstd", "-q", "-o", str(payload / name), str(raw_file)], check=True)
        files[key] = {
            "file": name,
            "sha256": hashlib.sha256((payload / name).read_bytes()).hexdigest(),
            "raw_size": len(raw),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        }
    (payload / "manifest.json").write_text(json.dumps({
        "format": "pengyos-layout-1", "edition": "desktop", "version": "test", "files": files,
    }))
    return payload


class FakeRun:
    """Records argv; answers sgdisk -i like the real tool."""

    def __init__(self, root: Path):
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self.root = root
        self.guid = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
        self.partuuid = "11111111-2222-3333-4444-555555555555"

    def __call__(self, argv, *, input=None, check=True):
        self.calls.append(list(argv))
        self.inputs.append(input)
        stdout = ""
        if argv[:2] == ["sgdisk", "-G"]:
            self.partuuid = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
        if argv[0] == "mkpasswd":
            salt = argv[argv.index("-S") + 1]
            stdout = f"$6${salt}$hash-of-{input.strip()}\n"
        if argv[:2] == ["sgdisk", "-i"]:
            stdout = (f"Partition GUID code: {self.guid} (Linux filesystem)\n"
                      f"Partition unique GUID: {self.partuuid}\nFirst sector: 131136 (at 64.0 MiB)\n")
        return subprocess.CompletedProcess(argv, 0, stdout, "")


BIG = im.MIN_DISK


@pytest.mark.skipif(shutil.which("zstd") is None, reason="needs zstd")
def test_install_image_autologin_opt_in(tmp_path, monkeypatch):
    """"Log in automatically" on a Desktop writes greetd-config's setting."""
    payload = _payload(tmp_path, b"R" * 8192, b"E" * 4096)
    target = tmp_path / "vda"
    target.touch()
    for n in (1, 2, 3, 4):
        (tmp_path / f"vda{n}").touch()
    esp_dir, root = tmp_path / "esp", tmp_path / "root"
    (esp_dir / "EFI" / "BOOT").mkdir(parents=True)
    (root / "etc" / "ssh").mkdir(parents=True)
    (root / "etc" / "systemd" / "system").mkdir(parents=True)
    (root / "etc" / "systemd" / "system" / "greetd.service").touch()
    (root / "data").mkdir()
    (root / "etc" / "group").write_text("root:x:0:\nwheel:x:10:root\n")
    monkeypatch.setattr(im, "TARGET_ESP", str(esp_dir))
    monkeypatch.setattr(im, "TARGET_ROOT", str(root))
    monkeypatch.setattr(im, "EFIVARS", str(tmp_path / "no-efivars"))
    answers = ProvisionAnswers("desk", "pat", "hunter22", autologin=True)
    install_image(str(target), answers, payload_dir=str(payload), confirm=True, run=FakeRun(root),
                  disks=[Disk("vda", str(target), BIG)], nm_connections=str(tmp_path / "none"))
    text = (root / "etc/pengyos/autologin.conf").read_text()
    assert "[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=pat\n" in text


@pytest.mark.skipif(shutil.which("zstd") is None, reason="needs zstd")
def test_install_image_end_to_end(tmp_path, monkeypatch):
    rootfs, esp = bytes(range(256)) * 4096, b"FAT" * 5000
    payload = _payload(tmp_path, rootfs, esp)
    target = tmp_path / "vda"
    target.touch()
    for n in (1, 2, 3, 4):
        (tmp_path / f"vda{n}").touch()  # the partition nodes appear
    esp_dir, root = tmp_path / "esp", tmp_path / "root"
    (esp_dir / "EFI" / "BOOT").mkdir(parents=True)
    (root / "etc" / "ssh").mkdir(parents=True)
    (root / "etc" / "systemd" / "system").mkdir(parents=True)
    (root / "etc" / "systemd" / "system" / "greetd.service").touch()
    (root / "data").mkdir()
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "bin" / "zsh").touch()
    (root / "etc" / "hosts").write_text("127.0.0.1\tlocalhost\n127.0.1.1\told\n")
    (root / "etc" / "group").write_text("root:x:0:\nwheel:x:10:root\nsudo:x:1000:\nseat:x:1001:\nvideo:x:28:\n")
    live_nm = tmp_path / "live-nm"
    live_nm.mkdir()
    (live_nm / "Home WiFi.nmconnection").write_text("[wifi-security]\npsk=secret\n")
    (live_nm / "notes.txt").write_text("not a profile")
    for f in ("ssh_host_ed25519_key", "ssh_host_ed25519_key.pub", "sshd_config"):
        (root / "etc" / "ssh" / f).write_text("x")
    monkeypatch.setattr(im, "TARGET_ESP", str(esp_dir))
    monkeypatch.setattr(im, "TARGET_ROOT", str(root))
    (tmp_path / "efivars").mkdir()
    monkeypatch.setattr(im, "EFIVARS", str(tmp_path / "efivars"))

    run = FakeRun(root)
    phases = []
    install_image(
        str(target), ANSWERS, payload_dir=str(payload), confirm=True, run=run,
        report=lambda p, f, m: phases.append(p),
        disks=[Disk("vda", str(target), BIG)],
        nm_connections=str(live_nm),
    )

    # The user owns the desktop: admin + seat groups (only those the image has),
    # and the session drop-in.
    (useradd,) = [c[2:] for c in run.calls if c[2:3] == ["useradd"]]
    assert useradd == ["useradd", "-m", "-U", "-s", "/usr/bin/zsh", "-G", "wheel,sudo,seat,video", "pat"]
    for sub in ("etc", "var", "home", "root"):
        assert ["btrfs", "subvolume", "create", f"{root}/data/{sub}"] in run.calls
    # A Desktop starts at its login screen: no automatic login unless chosen.
    assert not (root / "etc/pengyos/autologin.conf").exists()
    # The live session's Wi-Fi came along, root-only; nothing else did.
    nm = root / "etc/NetworkManager/system-connections"
    assert [p.name for p in nm.iterdir()] == ["Home WiFi.nmconnection"]
    assert (nm / "Home WiFi.nmconnection").stat().st_mode & 0o777 == 0o600

    # The artifacts landed byte for byte in their partitions.
    assert (tmp_path / "vda1").read_bytes() == esp
    assert (tmp_path / "vda2").read_bytes() == rootfs
    # Partition table: ESP, two root slots, data — in one sgdisk call.
    (layout,) = [c for c in run.calls if c[:2] == ["sgdisk", "-o"]]
    assert "1:PENGYOS-ESP" in layout and "2:pengyos-root-a" in layout
    assert "3:pengyos-root-b" in layout and "4:pengyos-data" in layout and "4:0:0" in layout
    # root-b is empty: no stale filesystem from an old layout survives there.
    assert ["wipefs", "-a", str(tmp_path / "vda3")] in run.calls
    # Boot: grub.cfg for this disk's root-a PARTUUID, grubenv slot a.
    cfg = (esp_dir / "EFI" / "BOOT" / "grub.cfg").read_text()
    assert "root=PARTUUID=11111111-2222-3333-4444-555555555555 ro" in cfg
    assert ["grub-editenv", str(esp_dir / "EFI" / "BOOT" / "grubenv"), "set", "pengyos_slot=a"] in run.calls
    # A firmware boot entry for the ESP's loader.
    assert ["efibootmgr", "--create", "--disk", str(target), "--part", "1", "--label", "PengyOS",
            "--loader", "\\EFI\\BOOT\\BOOTX64.EFI"] in run.calls
    # Provision happens through the overlay + binds, after the data partition is formatted.
    cmds = [" ".join(c) for c in run.calls]
    idx = lambda s: next(i for i, c in enumerate(cmds) if c.startswith(s))
    assert idx("mkfs.btrfs -f -q -L pengyos-data") < idx("mount -t ext4 -o ro") \
        < idx(f"mount -t btrfs -o noatime,compress=zstd:1 {tmp_path}/vda4 {root}/data") \
        < idx(f"btrfs subvolume create {root}/data/etc") < idx("cp -a") \
        < idx("mount -t overlay") < idx(f"mount --bind {root}/data/home") < idx("chroot")
    assert f"lowerdir={root}/etc,upperdir={root}/data/etc/upper,workdir={root}/data/etc/work" in cmds[idx("mount -t overlay")]
    for d in ("etc/upper", "etc/work"):
        assert (root / "data" / d).is_dir()
    assert (root / "etc" / "hostname").read_text() == "pengy-box\n"
    assert (root / "etc" / "hosts").read_text() == "127.0.0.1\tlocalhost\n127.0.1.1\tpengy-box\n"
    assert sorted(p.name for p in (root / "etc" / "ssh").iterdir()) == ["sshd_config"]
    assert (root / "etc" / "fstab").read_text().endswith(
        "PARTUUID=11111111-2222-3333-4444-555555555555 /efi vfat "
        "umask=0077,noauto,x-systemd.automount,x-systemd.idle-timeout=60 0 2\n")
    passwd = run.inputs[[c[2:3] for c in run.calls].index(["chpasswd"])]
    (pat, rootline) = passwd.splitlines()
    assert pat.startswith("pat:$6$") and pat.endswith("$hash-of-hunter22")
    assert rootline.startswith("root:$6$") and rootline.endswith("$hash-of-hunter22")
    assert pat.split("$")[2] != rootline.split("$")[2]  # a fresh salt per account
    assert "hunter22" not in " ".join(cmds)  # never on argv
    # Everything mounted is unmounted, innermost first, then sync.
    umounts = [c[1] for c in run.calls if c[0] == "umount"]
    assert umounts == [f"{root}/dev", f"{root}/root", f"{root}/home", f"{root}/var",
                       f"{root}/etc", f"{root}/data", str(root), str(esp_dir)]
    assert run.calls[-1] == ["sync"]
    assert phases[0] == "verify" and phases[-1] == "done"


@pytest.mark.skipif(shutil.which("zstd") is None, reason="needs zstd")
def test_install_image_refusals(tmp_path):
    payload = _payload(tmp_path, b"x" * 1000)
    ok = Disk("vda", "/dev/vda", BIG)

    def go(disks, *, confirm=True, target="/dev/vda"):
        install_image(target, ANSWERS, payload_dir=str(payload), confirm=confirm,
                      run=FakeRun(tmp_path), disks=disks)

    with pytest.raises(ImageInstallError, match="confirmation"):
        go([ok], confirm=False)
    with pytest.raises(ImageInstallError, match="not a disk"):
        go([ok], target="/dev/sdq")
    with pytest.raises(ImageInstallError, match="running from"):
        go([Disk("vda", "/dev/vda", BIG, is_boot=True)])
    with pytest.raises(ImageInstallError, match="mounted"):
        go([Disk("vda", "/dev/vda", BIG, mounted=["/mnt"])])
    with pytest.raises(ImageInstallError, match="too small"):
        go([Disk("vda", "/dev/vda", BIG - 1)])


@pytest.mark.skipif(shutil.which("zstd") is None, reason="needs zstd")
def test_corrupt_payload_is_refused_before_writing(tmp_path):
    payload = _payload(tmp_path, b"y" * 5000)
    (payload / "rootfs.ext4.zst").write_bytes(b"garbage")
    run = FakeRun(tmp_path)
    with pytest.raises(ImageInstallError, match="corrupt"):
        install_image("/dev/vda", ANSWERS, payload_dir=str(payload), confirm=True, run=run,
                      disks=[Disk("vda", "/dev/vda", BIG)])
    assert run.calls == []  # nothing touched the disk


def test_manifest_rejects_old_format(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"edition": "desktop", "image": "x"}))
    with pytest.raises(ImageInstallError, match="format"):
        im.Manifest.load(tmp_path)


def test_server_install_keys_and_no_desktop_session(tmp_path, monkeypatch):
    """A Server image: no login screen (so automatic login is never written,
    even if asked for), and the user's SSH keys land in their home, owned by
    them."""
    payload = _payload(tmp_path, b"R" * 8192, b"E" * 4096)
    target = tmp_path / "vda"
    target.touch()
    for n in (1, 2, 3, 4):
        (tmp_path / f"vda{n}").touch()
    esp_dir, root = tmp_path / "esp", tmp_path / "root"
    (esp_dir / "EFI" / "BOOT").mkdir(parents=True)
    (root / "etc" / "ssh").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "etc" / "group").write_text("root:x:0:\nwheel:x:10:root\n")
    monkeypatch.setattr(im, "TARGET_ESP", str(esp_dir))
    monkeypatch.setattr(im, "TARGET_ROOT", str(root))
    monkeypatch.setattr(im, "EFIVARS", str(tmp_path / "no-efivars"))
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample pat@laptop"
    answers = ProvisionAnswers("srv", "pat", "hunter22", authorized_keys=f"\n{key}\n\n",
                               autologin=True)
    run = FakeRun(root)
    install_image(str(target), answers, payload_dir=str(payload), confirm=True, run=run,
                  disks=[Disk("vda", str(target), BIG)], nm_connections=str(tmp_path / "none"))

    assert not (root / "etc/pengyos/autologin.conf").exists()
    auth = root / "home/pat/.ssh/authorized_keys"
    assert auth.read_text() == key + "\n"
    assert auth.stat().st_mode & 0o777 == 0o600
    assert (root / "home/pat/.ssh").stat().st_mode & 0o777 == 0o700
    assert ["chroot", str(root), "chown", "-R", "pat:pat", "/home/pat/.ssh"] in run.calls
    (useradd,) = [c[2:] for c in run.calls if c[2:3] == ["useradd"]]
    assert useradd == ["useradd", "-m", "-U", "-s", "/bin/sh", "-G", "wheel", "pat"]


def test_find_payload_mounts_the_stick_partition(tmp_path, monkeypatch):
    """Nothing mounted the payload yet (an SSH user beat the Desktop's GUI to
    it): the engine mounts the stick's PENGYOS-PAYLOAD partition itself."""
    mount, device = tmp_path / "payload", tmp_path / "PENGYOS-PAYLOAD"
    device.touch()
    monkeypatch.setattr(im, "PAYLOAD_MOUNT", str(mount))
    monkeypatch.setattr(im, "PAYLOAD_DEVICE", str(device))
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        (mount / "manifest.json").write_text("{}")  # "mounted"

    assert im.find_payload(run=run) == mount
    assert calls == [["mount", "-o", "ro", str(device), str(mount)]]
    calls.clear()
    im.find_payload(run=run)  # already there: no second mount
    assert calls == []


# --------------------------------------------------------------------------- #
# The assistant's endpoint (the installer's Pengy page)
# --------------------------------------------------------------------------- #

def test_pengy_answers_are_optional():
    """No Pengy answers at all is valid — the page is skippable by design."""
    ProvisionAnswers("ok", "pat", "hunter22").validate()
    assert not ProvisionAnswers("ok", "pat", "hunter22").pengy_configured
    filled = ProvisionAnswers("ok", "pat", "hunter22",
                              pengy_base_url="http://10.0.23.6:8086/v1",
                              pengy_api_key="sk-secret", pengy_model="qwen3:8b")
    filled.validate()
    assert filled.pengy_configured


@pytest.mark.parametrize(
    "kw",
    [
        {"pengy_base_url": "10.0.23.6:8086/v1"},          # no scheme
        {"pengy_base_url": "ftp://host/v1"},              # not http(s)
        {"pengy_base_url": "http://host has a space/v1"},
        {"pengy_model": "qwen 3"},
        {"pengy_api_key": "sk bad"},
        {"pengy_api_key": "sk\nsecond line"},
    ],
)
def test_pengy_answers_rejected(kw):
    answers = ProvisionAnswers("ok", "pat", "hunter22", **kw)
    with pytest.raises(ImageInstallError):
        answers.validate()


def test_pengy_key_is_never_echoed_by_a_validation_error():
    """A rejected key says what is wrong with it, never what it is."""
    answers = ProvisionAnswers("ok", "pat", "hunter22",
                               pengy_base_url="http://127.0.0.1:8080/v1",
                               pengy_api_key="sk-super secret key")
    with pytest.raises(ImageInstallError) as exc:
        answers.validate()
    assert "sk-super" not in str(exc.value)


def test_render_pengy_settings_golden():
    """The seeded config carries exactly the four keys the wizard asks about.

    Writing only these is deliberate: Pengy merges its own defaults over
    whatever is missing, so the installer never pins a setting nobody chose.
    """
    answers = ProvisionAnswers("ok", "pat", "hunter22",
                               pengy_base_url="https://api.openai.com/v1",
                               pengy_api_key="sk-secret", pengy_model="gpt-4o")
    settings = json.loads(im.render_pengy_settings(answers))
    assert settings == {
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-secret",
        "model": "gpt-4o",
        "system_message": im.PENGY_SYSTEM_MESSAGE,
    }
    # The system message keeps its placeholders — Pengy fills them per request.
    for token in ("{date}", "{username}", "{hostname}", "{osinfo}"):
        assert token in settings["system_message"]
    assert "skill_index.md" in settings["system_message"]


def _pengy_target(tmp_path):
    """A minimal fake target root + payload for a provisioning-only install."""
    payload = _payload(tmp_path, b"R" * 8192, b"E" * 4096)
    target = tmp_path / "vda"
    target.touch()
    for n in (1, 2, 3, 4):
        (tmp_path / f"vda{n}").touch()
    esp_dir, root = tmp_path / "esp", tmp_path / "root"
    (esp_dir / "EFI" / "BOOT").mkdir(parents=True)
    (root / "etc" / "ssh").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "etc" / "group").write_text("root:x:0:\nwheel:x:10:root\n")
    return payload, target, esp_dir, root


@pytest.mark.skipif(shutil.which("zstd") is None, reason="needs zstd")
def test_install_image_seeds_the_pengy_config(tmp_path, monkeypatch):
    """The endpoint the wizard was given lands in the user's home, owned by
    them and readable only by them (it carries their API key)."""
    payload, target, esp_dir, root = _pengy_target(tmp_path)
    monkeypatch.setattr(im, "TARGET_ESP", str(esp_dir))
    monkeypatch.setattr(im, "TARGET_ROOT", str(root))
    monkeypatch.setattr(im, "EFIVARS", str(tmp_path / "no-efivars"))
    answers = ProvisionAnswers("box", "pat", "hunter22",
                               pengy_base_url="http://127.0.0.1:11434/v1",
                               pengy_api_key="ollama", pengy_model="qwen3:8b")
    run = FakeRun(root)
    install_image(str(target), answers, payload_dir=str(payload), confirm=True, run=run,
                  disks=[Disk("vda", str(target), BIG)], nm_connections=str(tmp_path / "none"))

    settings = root / "home/pat/.config/pengy/settings.json"
    assert json.loads(settings.read_text())["model"] == "qwen3:8b"
    assert json.loads(settings.read_text())["base_url"] == "http://127.0.0.1:11434/v1"
    assert settings.stat().st_mode & 0o777 == 0o600
    assert ["chroot", str(root), "chown", "-R", "pat:pat", "/home/pat/.config/pengy"] in run.calls
    # The config is written after the account exists (useradd -m made the home).
    cmds = [" ".join(c) for c in run.calls]
    idx = lambda s: next(i for i, c in enumerate(cmds) if c.startswith(s))
    assert idx("chroot") < idx(f"chroot {root} chown -R pat:pat /home/pat/.config/pengy")
    # The key never rides on argv.
    assert "ollama" not in " ".join(cmds)


@pytest.mark.skipif(shutil.which("zstd") is None, reason="needs zstd")
def test_install_image_skips_pengy_when_not_configured(tmp_path, monkeypatch):
    """Skipping the page leaves Pengy exactly as the image ships it: no config
    file, so its own defaults (local endpoint, no model) apply."""
    payload, target, esp_dir, root = _pengy_target(tmp_path)
    monkeypatch.setattr(im, "TARGET_ESP", str(esp_dir))
    monkeypatch.setattr(im, "TARGET_ROOT", str(root))
    monkeypatch.setattr(im, "EFIVARS", str(tmp_path / "no-efivars"))
    run = FakeRun(root)
    install_image(str(target), ANSWERS, payload_dir=str(payload), confirm=True, run=run,
                  disks=[Disk("vda", str(target), BIG)], nm_connections=str(tmp_path / "none"))
    assert not (root / "home/pat/.config").exists()
    assert not any(".config/pengy" in " ".join(c) for c in run.calls)
