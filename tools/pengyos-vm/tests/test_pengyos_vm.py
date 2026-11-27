"""Tests for the `pengyos-vm` shipped tool.

The shipped script is run as a program with HOME in a temp dir (its VMs go to
HOME/vms) and `--root DIR`: fake systemctl/qemu-img/networkctl/ip/ssh under
DIR/usr/bin, and DIR/sys/class/net, DIR/dev/kvm and DIR/etc standing in for
the real ones. Asserted: the VM folder and vm.json `create` writes, the QEMU
command `_run` would exec (NAT forwards, bridge helper, firmware, credentials,
boot order), the unit verbs each command sends to systemd, the networkd and
bridge.conf files `bridge enable` writes and `disable` removes, and every
misuse refused with nothing written.

That VMs boot, shut down cleanly and get a LAN address over the bridge is
proven on a Server (spec.md, "Verification").
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_DIR.parents[1]
BOARD = REPO_ROOT / "buildroot-external" / "board"
SCRIPT = BOARD / "server" / "rootfs-overlay" / "usr" / "bin" / "pengyos-vm"
UNIT = BOARD / "server" / "rootfs-overlay" / "usr" / "lib" / "systemd" / "user" / "pengyos-vm@.service"
HERE = Path(__file__).resolve().parent
FAKES = ("systemctl", "qemu-img", "networkctl", "ip", "ssh", "ssh-keygen", "journalctl", "loginctl")
NIC_MAC = "54:e1:ad:5a:60:8a"
QEMU_BIN = "/usr/bin/qemu-system-x86_64"


class Box:
    def __init__(self, tmp: Path):
        self.root = tmp / "root"
        self.home = tmp / "home"
        self.home.mkdir()
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh" / "id_ed25519.pub").write_text("ssh-ed25519 AAAAC3Nza test@host\n")
        bindir = self.root / "usr" / "bin"
        bindir.mkdir(parents=True)
        for name in FAKES:
            shutil.copy(HERE / "fakes.py", bindir / name)
            (bindir / name).chmod(0o755)
        (self.root / "dev").mkdir()
        (self.root / "dev" / "kvm").touch()
        share = self.root / "usr" / "share" / "qemu"
        share.mkdir(parents=True)
        (share / "edk2-i386-vars.fd").write_bytes(b"VARS")
        libexec = self.root / "usr" / "libexec"
        libexec.mkdir(parents=True)
        (libexec / "qemu-bridge-helper").touch()
        (libexec / "qemu-bridge-helper").chmod(0o4755)
        self.iso = tmp / "debian.iso"
        self.iso.write_bytes(b"ISO")
        self.nic("enp0s31f6", NIC_MAC)
        self.nic("wlp4s0", "ac:ed:5c:63:0a:01", wireless=True)

    def nic(self, name: str, mac: str, wireless: bool = False, physical: bool = True) -> None:
        d = self.root / "sys" / "class" / "net" / name
        d.mkdir(parents=True)
        (d / "type").write_text("1\n")
        (d / "address").write_text(mac + "\n")
        if physical:
            (d / "device").mkdir()
        if wireless:
            (d / "wireless").mkdir()

    def run(self, *args: str, ok: int | None = 0, stdin: str = "") -> subprocess.CompletedProcess:
        env = dict(os.environ, HOME=str(self.home))
        cp = subprocess.run(["python3", str(SCRIPT), "--root", str(self.root), *args],
                            capture_output=True, text=True, env=env, input=stdin)
        if ok is not None:
            assert cp.returncode == ok, (cp.returncode, cp.stdout, cp.stderr)
        return cp

    def vm(self, name: str) -> dict:
        return json.loads((self.home / "vms" / name / "vm.json").read_text())

    def calls(self, prog: str) -> list[list[str]]:
        log = self.root / "calls.log"
        if not log.exists():
            return []
        return [c[1:] for c in map(json.loads, log.read_text().splitlines()) if c[0] == prog]

    def argv(self, name: str) -> list[str]:
        return json.loads(self.run("_run", name).stdout)

    def net(self, name: str) -> Path:
        return self.root / "etc" / "systemd" / "network" / name


@pytest.fixture()
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def opt(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


# ------------------------------------------------------------------ create
def test_create_from_an_iso_makes_a_blank_disk_and_uefi_vars(box: Box):
    out = box.run("create", "deb", "--iso", str(box.iso)).stdout
    assert "created deb: 2 CPUs, 2G memory, 20G disk, nat" in out
    assert "pengyos-vm start deb --console" in out and "--eject" in out
    d = box.home / "vms" / "deb"
    assert json.loads((d / "disk.qcow2").read_text())["virtual-size"] == 20 << 30
    assert (d / "vars.fd").read_bytes() == b"VARS"
    c = box.vm("deb")
    assert c["iso"] == str(box.iso.resolve())
    assert c["ssh_port"] == 2201 and c["vnc"] == 1 and c["net"] == "nat"
    assert c["mac"].startswith("52:54:00:") and c["ssh_keys"] is None


def test_create_from_an_image_converts_and_grows_it(box: Box, tmp_path: Path):
    img = tmp_path / "cloud.qcow2"
    img.write_text(json.dumps({"virtual-size": 3 << 30}))
    box.run("create", "web", "--image", str(img), "--disk", "10G", "--memory", "1G", "--cpus", "1")
    assert json.loads((box.home / "vms" / "web" / "disk.qcow2").read_text())["virtual-size"] == 10 << 30
    verbs = [c[0] for c in box.calls("qemu-img")]
    assert verbs[:2] == ["convert", "info"] and "resize" in verbs
    assert box.vm("web")["memory"] == 1 << 30
    # Only the disk and the UEFI vars are left in the folder.
    assert sorted(p.name for p in (box.home / "vms" / "web").iterdir()) == ["disk.qcow2", "vars.fd", "vm.json"]


def test_an_image_is_never_shrunk(box: Box, tmp_path: Path):
    img = tmp_path / "big.img"
    img.write_text(json.dumps({"virtual-size": 40 << 30}))
    box.run("create", "big", "--image", str(img), "--disk", "10G")
    assert "resize" not in [c[0] for c in box.calls("qemu-img")]


def test_an_image_grows_to_the_default_disk_without_disk(box: Box, tmp_path: Path):
    """A 1 GB Core image left 362 MB free (ThinkPad, 2026-10-31)."""
    img = tmp_path / "core.img"
    img.write_text(json.dumps({"virtual-size": 1 << 30}))
    out = box.run("create", "core", "--image", str(img)).stdout
    assert json.loads((box.home / "vms" / "core" / "disk.qcow2").read_text())["virtual-size"] == 20 << 30
    assert "20G disk" in out
    big = tmp_path / "big.img"
    big.write_text(json.dumps({"virtual-size": 40 << 30}))
    box.run("create", "big", "--image", str(big))
    assert json.loads((box.home / "vms" / "big" / "disk.qcow2").read_text())["virtual-size"] == 40 << 30


def test_a_forward_says_to_open_the_port_in_the_guest(box: Box):
    """A forward to a default-deny PengyOS guest answered nothing (ThinkPad, 2026-10-31)."""
    out = box.run("create", "web", "--iso", str(box.iso), "--forward", "8080:80",
                  "--forward", "5353:53/udp").stdout
    assert "pengyos-firewall allow 53/udp" in out or "pengyos-firewall allow 80" in out
    assert "80" in out and "53/udp" in out
    assert "open them inside" not in box.run("create", "plain", "--iso", str(box.iso)).stdout
    out = box.run("set", "web", "--forward", "9090:9000").stdout
    assert "pengyos-firewall allow 9000" in out


def test_xz_images_are_unpacked(box: Box, tmp_path: Path):
    import lzma
    img = tmp_path / "core.img.xz"
    img.write_bytes(lzma.compress(json.dumps({"virtual-size": 1 << 30}).encode()))
    box.run("create", "core", "--image", str(img))
    convert = [c for c in box.calls("qemu-img") if c[0] == "convert"][0]
    assert convert[-2].endswith("core.img.unpacked")
    assert not (box.home / "vms" / "core" / "core.img.unpacked").exists()


def test_second_vm_gets_the_next_ports(box: Box):
    box.run("create", "a", "--iso", str(box.iso))
    box.run("create", "b", "--iso", str(box.iso), "--forward", "8080:80")
    assert box.vm("b")["ssh_port"] == 2202 and box.vm("b")["vnc"] == 2
    assert box.run("create", "c", "--iso", str(box.iso), "--forward", "8080:81", ok=2).stderr \
        .count("already forwarded") == 1
    assert not (box.home / "vms" / "c").exists()


def test_ssh_key_defaults_to_the_users_key(box: Box):
    box.run("create", "k", "--iso", str(box.iso), "--ssh-key")
    assert box.vm("k")["ssh_keys"] == "ssh-ed25519 AAAAC3Nza test@host\n"


@pytest.mark.parametrize("args, says", [
    (["create", "Bad_Name", "--iso", "ISO"], "lowercase letters"),
    (["create", "x"], "something to boot"),
    (["create", "x", "--iso", "ISO", "--image", "ISO"], "something to boot"),
    (["create", "x", "--iso", "/nope.iso"], "does not exist"),
    (["create", "x", "--iso", "ISO", "--memory", "lots"], "512M, 2G"),
    (["create", "x", "--iso", "ISO", "--memory", "64M"], "at least 128M"),
    (["create", "x", "--iso", "ISO", "--cpus", "9999"], "--cpus"),
    (["create", "x", "--iso", "ISO", "--forward", "80:80"], "below 1024"),
    (["create", "x", "--iso", "ISO", "--forward", "2222:22"], "already forwards"),
    (["create", "x", "--iso", "ISO", "--forward", "nonsense"], "a forward is"),
    (["create", "x", "--iso", "ISO", "--net", "bridge"], "bridge enable"),
    (["create", "x", "--iso", "ISO", "--ssh-key", "/nope.pub"], "does not exist"),
])
def test_create_misuse_writes_nothing(box: Box, args, says):
    args = [str(box.iso) if a == "ISO" else a for a in args]
    cp = box.run(*args, ok=2)
    assert says in cp.stderr
    assert not (box.home / "vms" / "x").exists() and not (box.home / "vms" / "Bad_Name").exists()


def test_private_keys_are_refused(box: Box):
    key = box.home / ".ssh" / "id_ed25519"
    key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nxx\n")
    assert "not a public key" in box.run("create", "x", "--iso", str(box.iso),
                                         "--ssh-key", str(key), ok=2).stderr


def test_create_refuses_an_existing_name(box: Box):
    box.run("create", "a", "--iso", str(box.iso))
    assert "already exists" in box.run("create", "a", "--iso", str(box.iso), ok=2).stderr


# ------------------------------------------------------------- QEMU command
def test_nat_command(box: Box):
    box.run("create", "deb", "--iso", str(box.iso), "--forward", "8080:80",
            "--forward", "0.0.0.0:5353:53/udp", "--ssh-key")
    argv = box.argv("deb")
    d = box.home / "vms" / "deb"
    assert argv[0] == "/usr/bin/qemu-system-x86_64"
    assert opt(argv, "-machine") == ["q35,accel=kvm"] and opt(argv, "-cpu") == ["host"]
    assert opt(argv, "-m") == ["2048M"] and opt(argv, "-smp") == ["2"]
    nic = opt(argv, "-nic")[0].split(",")
    assert nic[:2] == ["user", "model=virtio-net-pci"]
    mac = box.vm("deb")["mac"]
    assert f"mac={mac}" in nic
    assert "hostfwd=tcp:127.0.0.1:2201-:22" in nic
    assert "hostfwd=tcp:127.0.0.1:8080-:80" in nic
    assert "hostfwd=udp:0.0.0.0:5353-:53" in nic
    drives = opt(argv, "-drive")
    assert drives[0] == "if=pflash,format=raw,readonly=on,file=/usr/share/qemu/edk2-x86_64-code.fd"
    assert drives[1] == f"if=pflash,format=raw,file={d / 'vars.fd'}"
    devices = opt(argv, "-device")
    assert "virtio-blk-pci,drive=disk0,bootindex=0" in devices
    assert "ide-cd,drive=cd0,bootindex=1" in devices
    assert any(x.startswith(f"if=none,id=cd0,media=cdrom,readonly=on,file={box.iso.resolve()}") for x in drives)
    # The screen is a socket in the VM's own folder, never a TCP port (0.5.0).
    assert opt(argv, "-vnc") == [f"unix:{d / 'vnc.sock'}"]
    assert opt(argv, "-qmp") == [f"unix:{d / 'qmp.sock'},server=on,wait=off"]
    smbios = opt(argv, "-smbios")
    # base64("deb") and base64 of the key line.
    assert "type=11,value=io.systemd.credential.binary:system.hostname=ZGVi" in smbios
    assert any(s.startswith("type=11,value=io.systemd.credential.binary:ssh.authorized_keys.root=") for s in smbios)


def test_without_kvm_it_still_runs_slowly(box: Box):
    box.run("create", "slow", "--iso", str(box.iso), "--bios")
    (box.root / "dev" / "kvm").unlink()
    argv = box.argv("slow")
    assert opt(argv, "-machine") == ["q35,accel=tcg"] and opt(argv, "-cpu") == ["max"]
    assert not any("pflash" in d for d in opt(argv, "-drive")), "--bios has no UEFI flash"
    assert "without hardware virtualisation" in box.run("show", "slow").stdout


def test_eject_drops_the_cdrom(box: Box):
    box.run("create", "deb", "--iso", str(box.iso))
    box.run("set", "deb", "--eject")
    argv = box.argv("deb")
    assert not any("media=cdrom" in d for d in opt(argv, "-drive"))
    assert box.vm("deb")["iso"] is None


def test_run_refuses_a_missing_iso(box: Box):
    box.run("create", "deb", "--iso", str(box.iso))
    box.iso.unlink()
    assert "the ISO" in box.run("_run", "deb", ok=1).stderr
    assert "cannot start" in box.run("start", "deb", ok=2).stderr
    assert not [c for c in box.calls("systemctl") if c[1] == "start"]


# -------------------------------------------------------------- unit verbs
def test_start_stop_go_through_the_user_manager(box: Box):
    box.run("create", "deb", "--iso", str(box.iso))
    out = box.run("start", "deb").stdout
    assert "deb is running" in out and "pengyos-vm ssh deb" in out
    assert "stops when you log out" in out and "loginctl enable-linger" in out
    import pwd
    linger = box.root / "var" / "lib" / "systemd" / "linger"
    linger.mkdir(parents=True)
    (linger / pwd.getpwuid(os.getuid()).pw_name).touch()
    box.run("stop", "deb")
    assert "log out" not in box.run("start", "deb").stdout
    assert ["--user", "start", "pengyos-vm@deb.service"] in box.calls("systemctl")
    rows = json.loads(box.run("list", "--json").stdout)
    assert rows[0]["state"] == "running" and rows[0]["ssh"] == "127.0.0.1:2201"
    assert "already running" in box.run("start", "deb").stdout
    box.run("stop", "deb")
    assert ["--user", "stop", "pengyos-vm@deb.service"] in box.calls("systemctl")
    assert "not running" in box.run("stop", "deb").stdout


def test_force_stop_kills(box: Box):
    box.run("create", "deb", "--iso", str(box.iso))
    box.run("start", "deb")
    box.run("stop", "deb", "--force")
    assert ["--user", "kill", "--signal=SIGKILL", "pengyos-vm@deb.service"] in box.calls("systemctl")


def test_a_vm_that_dies_at_once_says_so(box: Box):
    box.run("create", "deb", "--iso", str(box.iso))
    (box.root / "fail-start").touch()
    assert "did not stay up (journalctl" in box.run("start", "deb", ok=1).stderr
    (box.home / "vms" / "deb" / "qemu.log").write_text("qemu-system-x86_64: -vnc: invalid option\n")
    assert "-vnc: invalid option" in box.run("start", "deb", ok=1).stderr


def test_autostart_enables_the_unit_and_explains_linger(box: Box):
    box.run("create", "deb", "--iso", str(box.iso))
    out = box.run("autostart", "deb", "on").stdout
    assert ["--user", "enable", "pengyos-vm@deb.service"] in box.calls("systemctl")
    assert "linger could not be turned on" in out  # the fake loginctl refuses
    assert json.loads(box.run("list", "--json").stdout)[0]["autostart"] is True
    box.run("autostart", "deb", "off")
    assert json.loads(box.run("list", "--json").stdout)[0]["autostart"] is False


def test_list_table(box: Box):
    assert "no VMs yet" in box.run("list").stdout
    box.run("create", "deb", "--iso", str(box.iso))
    lines = box.run("list").stdout.splitlines()
    assert lines[0].split() == ["NAME", "STATE", "CPUS", "MEMORY", "NET", "SSH", "AUTOSTART"]
    assert lines[1].split() == ["deb", "stopped", "2", "2G", "nat", "127.0.0.1:2201", "no"]


def test_ssh_uses_the_forward_and_a_per_vm_known_hosts(box: Box):
    box.run("create", "deb", "--iso", str(box.iso), "--ssh-key")
    assert "not running" in box.run("ssh", "deb", ok=2).stderr
    box.run("start", "deb")
    argv = json.loads(box.run("ssh", "deb", "uname", "-a").stdout)
    assert argv[:2] == ["-p", "2201"]
    assert f"UserKnownHostsFile={box.home / 'vms' / 'deb' / 'known_hosts'}" in argv
    assert argv[-3:] == ["root@127.0.0.1", "uname", "-a"], "root: the key went to root"
    argv = json.loads(box.run("ssh", "pat@deb").stdout)
    assert argv[-1] == "pat@127.0.0.1"


def test_delete_needs_yes_and_a_stopped_vm(box: Box):
    box.run("create", "deb", "--iso", str(box.iso))
    box.run("start", "deb")
    assert "stop deb first" in box.run("delete", "deb", "--yes", ok=2).stderr
    box.run("stop", "deb")
    assert "add --yes" in box.run("delete", "deb", ok=2).stderr
    box.run("delete", "deb", "--yes")
    assert not (box.home / "vms" / "deb").exists()
    assert ["--user", "disable", "pengyos-vm@deb.service"] in box.calls("systemctl")


def test_set_changes_and_unforwards(box: Box):
    box.run("create", "deb", "--iso", str(box.iso), "--forward", "8080:80")
    out = box.run("set", "deb", "--memory", "4G", "--cpus", "1", "--unforward", "8080",
                  "--forward", "9090:90").stdout
    assert "memory 4G" in out and "no forward from 8080" in out and "127.0.0.1:9090->90" in out
    c = box.vm("deb")
    assert c["memory"] == 4 << 30 and c["cpus"] == 1
    assert [f["host"] for f in c["forwards"]] == [9090]
    assert "nothing to change" in box.run("set", "deb", ok=2).stderr
    assert "no forward from host port 1234" in box.run("set", "deb", "--unforward", "1234", ok=2).stderr
    box.run("start", "deb")
    assert "next time it starts" in box.run("set", "deb", "--memory", "3G").stdout


def test_unknown_vm(box: Box):
    assert "no VM called" in box.run("start", "ghost", ok=2).stderr


# -------------------------------------------------------------- throwaway run
def core_image(box: Box) -> Path:
    img = box.root.parent / "pengyos-core-x86_64.img"
    img.write_text(json.dumps({"virtual-size": 1 << 30}))
    return img


def ssh_runs(box: Box) -> list[list[str]]:
    """The ssh calls that ran something other than the boot probe or tar."""
    return [c for c in box.calls("ssh") if c[-1] != "true" and "tar -" not in c[-1]]


def test_run_boots_an_overlay_runs_and_deletes_everything(box: Box):
    img = core_image(box)
    cp = box.run("run", "--image", str(img), "--name", "job", "--", "uname", "-a")
    argv = json.loads(cp.stdout)
    assert argv[-2] == "root@127.0.0.1"
    assert argv[-1] == "mkdir -p /root/run && cd /root/run && uname -a"
    assert "-T" in argv, "no tty in a pipe"
    assert "-i" in argv and argv[argv.index("-i") + 1].endswith("/vms/job/id_ed25519")
    assert "job: deleted" in cp.stderr
    assert not (box.home / "vms" / "job").exists()
    calls = box.calls("systemctl")
    assert ["--user", "start", "pengyos-vm@job.service"] in calls
    assert ["--user", "kill", "--signal=SIGKILL", "pengyos-vm@job.service"] in calls
    assert not any(c[1] == "enable" for c in calls), "a throwaway VM never autostarts"
    create = [c for c in box.calls("qemu-img") if c[0] == "create"][0]
    base = create[create.index("-b") + 1]
    assert base.startswith(str(box.home / ".cache" / "pengyos-vm" / "images"))
    assert create[-1] == str(8 << 30), "the 8G default, sparse"
    assert Path(base).is_file(), "the converted image stays cached"


def test_run_converts_the_image_once(box: Box):
    img = core_image(box)
    box.run("run", "--image", str(img), "true")
    box.run("run", "--image", str(img), "true")
    assert len([c for c in box.calls("qemu-img") if c[0] == "convert"]) == 1
    img.write_text(json.dumps({"virtual-size": 2 << 30}))  # a rebuilt image
    os.utime(img, (1, 1))
    box.run("run", "--image", str(img), "true")
    assert len([c for c in box.calls("qemu-img") if c[0] == "convert"]) == 2


def test_run_gives_the_guest_its_own_key_as_roots(box: Box):
    img = core_image(box)
    box.run("run", "--image", str(img), "--name", "job", "--keep", "true")
    argv = box.argv("job")
    b64 = [s for s in opt(argv, "-smbios") if "ssh.authorized_keys.root" in s][0].split("root=", 1)[1]
    import base64
    assert base64.b64decode(b64).decode() == "ssh-ed25519 AAAArun pengyos-vm run\n"
    assert "run_owner" not in box.vm("job"), "a kept VM is not swept"


def test_run_passes_the_exit_status_through(box: Box):
    (box.root / "ssh-exit").write_text("3")
    cp = box.run("run", "--image", str(core_image(box)), "--name", "job", "false", ok=3)
    assert not (box.home / "vms" / "job").exists()


def test_run_is_offline_by_default(box: Box):
    """QEMU's NAT maps 10.0.2.2 onto the host's 127.0.0.1; a throwaway job must not
    reach the host's loopback-only services, or the internet, unless asked."""
    cp = box.run("run", "--image", str(core_image(box)), "--name", "job", "--keep", "true")
    nic = opt(box.argv("job"), "-nic")[0].split(",")
    assert "restrict=on" in nic
    assert "hostfwd=tcp:127.0.0.1:2201-:22" in nic, "ssh still reaches it"
    assert "offline" in cp.stderr
    assert box.vm("job")["restrict"] is True
    assert "offline" in box.run("list").stdout


def test_run_online_reaches_the_internet(box: Box):
    box.run("run", "--image", str(core_image(box)), "--name", "job", "--keep", "--online", "true")
    nic = opt(box.argv("job"), "-nic")[0].split(",")
    assert "restrict=on" not in nic


def test_a_failed_offline_run_suggests_online(box: Box):
    (box.root / "ssh-exit").write_text("1")
    cp = box.run("run", "--image", str(core_image(box)), "pip", "install", "x", ok=1)
    assert "--online" in cp.stderr
    cp = box.run("run", "--image", str(core_image(box)), "--online", "false", ok=1)
    assert "--online" not in cp.stderr


def test_set_net_brings_a_kept_offline_run_online(box: Box):
    box.run("run", "--image", str(core_image(box)), "--name", "job", "--keep", "true")
    box.run("set", "job", "--net", "nat")
    assert "restrict=on" not in opt(box.argv("job"), "-nic")[0]


def test_created_vms_stay_on_nat(box: Box):
    box.run("create", "deb", "--iso", str(box.iso))
    assert "restrict=on" not in opt(box.argv("deb"), "-nic")[0]


def test_run_copies_in_and_fetches_back(box: Box, tmp_path: Path):
    payload = tmp_path / "tool"
    payload.mkdir()
    (payload / "main.py").write_text("print('hi')\n")
    (box.root / "guest" / "out").mkdir(parents=True)
    (box.root / "guest" / "out" / "result.txt").write_text("42\n")
    work = tmp_path / "work"
    work.mkdir()
    env = dict(os.environ, HOME=str(box.home))
    cp = subprocess.run(["python3", str(SCRIPT), "--root", str(box.root), "run",
                         "--image", str(core_image(box)), "--copy", str(payload),
                         "--fetch", "out", "--", "python3", "tool/main.py"],
                        capture_output=True, text=True, env=env, cwd=work)
    assert cp.returncode == 0, cp.stderr
    import tarfile
    with tarfile.open(box.root / "pushed.tar") as tar:
        assert sorted(tar.getnames()) == ["tool", "tool/main.py"]
    assert (work / "out" / "result.txt").read_text() == "42\n"
    assert ssh_runs(box)[0][-1].endswith("python3 tool/main.py")


def test_run_fetch_of_a_missing_path_fails_the_run(box: Box, tmp_path: Path):
    cp = box.run("run", "--image", str(core_image(box)), "--fetch", "nope", "true", ok=1)
    assert "--fetch nope" in cp.stderr and "No such file" in cp.stderr


def test_run_without_a_command_is_a_shell(box: Box):
    argv = json.loads(box.run("run", "--image", str(core_image(box))).stdout)
    assert argv[-1].endswith("exec ${SHELL:-sh} -l")


def test_run_image_comes_from_the_environment(box: Box):
    assert "PENGYOS_VM_IMAGE" in box.run("run", "true", ok=2).stderr
    env = dict(os.environ, HOME=str(box.home), PENGYOS_VM_IMAGE=str(core_image(box)))
    cp = subprocess.run(["python3", str(SCRIPT), "--root", str(box.root), "run", "true"],
                        capture_output=True, text=True, env=env)
    assert cp.returncode == 0, cp.stderr


@pytest.mark.parametrize("args,says", [
    (["--timeout", "soon"], "--timeout must look like"),
    (["--fetch", "/etc/shadow"], "--fetch takes a path inside /root/run"),
    (["--fetch", "../x"], "--fetch takes a path inside /root/run"),
    (["--copy", "/nonexistent/x"], "--copy: /nonexistent/x does not exist"),
    (["--name", "Bad_Name"], "VM names are"),
    (["--memory", "64M"], "at least 128M"),
])
def test_run_misuse_writes_nothing(box: Box, args, says):
    cp = box.run("run", "--image", str(core_image(box)), *args, "true", ok=2)
    assert says in cp.stderr
    assert not (box.home / "vms").exists() or not any((box.home / "vms").iterdir())
    assert not box.calls("systemctl")


def test_run_that_never_answers_ssh_says_so_and_cleans_up(box: Box):
    (box.root / "ssh-down").touch()
    cp = box.run("run", "--image", str(core_image(box)), "--name", "job",
                 "--boot-timeout", "2s", "true", ok=1)
    assert "did not answer SSH within 2s" in cp.stderr
    assert not (box.home / "vms" / "job").exists()


def test_run_sweeps_the_vms_of_killed_runs(box: Box):
    img = core_image(box)
    box.run("run", "--image", str(img), "--name", "dead", "--keep", "true")
    conf = box.vm("dead")
    conf["run_owner"] = 2 ** 22 + 12345  # above pid_max's default: no such process
    (box.home / "vms" / "dead" / "vm.json").write_text(json.dumps(conf))
    box.run("create", "pet", "--iso", str(box.iso))
    cp = box.run("run", "--image", str(img), "true")
    assert "deleted dead, left behind by a run that was killed" in cp.stderr
    assert not (box.home / "vms" / "dead").exists()
    assert (box.home / "vms" / "pet").exists(), "only run VMs are swept"


def test_ssh_into_a_kept_run_uses_its_key(box: Box):
    box.run("run", "--image", str(core_image(box)), "--name", "job", "--keep", "true")
    box.run("start", "job")
    argv = json.loads(box.run("ssh", "job").stdout)
    assert argv[2:4] == ["-i", str(box.home / "vms" / "job" / "id_ed25519")]
    assert argv[-1] == "root@127.0.0.1"


# ----------------------------------------------------------------- shutdown
NOTICE = BOARD / "server" / "rootfs-overlay" / "usr" / "libexec" / "pengyos" / "pengyos-vm-shutdown-notice"
NOTICE_UNIT = BOARD / "server" / "rootfs-overlay" / "usr" / "lib" / "systemd" / "system" / "pengyos-vm-shutdown-notice.service"


def fake_qmp(path: Path):
    """A QEMU monitor that takes system_powerdown and then exits (closes)."""
    import socket
    import threading
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        f = conn.makefile("rwb", buffering=0)
        f.write(b'{"QMP": {}}\n')
        for _ in range(2):
            f.readline()
            f.write(b'{"return": {}}\n')
        conn.close()
        srv.close()
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return t


@pytest.mark.parametrize("state,wait", [("running", "100s"), ("stopping", "30s")])
def test_powerdown_waits_less_when_everything_is_going_down(box: Box, state, wait):
    box.run("create", "deb", "--iso", str(box.iso))
    (box.root / "system-state").write_text(state)
    t = fake_qmp(box.home / "vms" / "deb" / "qmp.sock")
    cp = box.run("_powerdown", "deb")
    t.join(5)
    assert f"deb: asked the guest to shut down; it is switched off after {wait}" in cp.stderr


def test_shutdown_notice_names_the_running_vms(tmp_path: Path):
    proc = tmp_path / "proc"
    for pid, argv in {
        "101": [QEMU_BIN, "-name", "core1,process=pengyos-vm-core1", "-m", "1024M"],
        "102": ["/usr/bin/sshd", "-D"],
        "103": [QEMU_BIN, "-name", "other"],  # QEMU, but not one of pengyos-vm's
        "104": [QEMU_BIN, "-name", "web,process=pengyos-vm-web"],
    }.items():
        (proc / pid).mkdir(parents=True)
        (proc / pid / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
    (proc / "self").mkdir()
    import pwd
    me = pwd.getpwuid(os.getuid()).pw_name
    out = subprocess.run(["python3", str(NOTICE), "--proc", str(proc)],
                         capture_output=True, text=True, check=True).stdout
    assert out == (f"Waiting for 2 virtual machines to shut down: core1 ({me}), web ({me}). "
                   f"A guest that ignores the power button is switched off after 30 s.\n")
    for pid in ("101", "104"):
        shutil.rmtree(proc / pid)
    assert subprocess.run(["python3", str(NOTICE), "--proc", str(proc)],
                          capture_output=True, text=True, check=True).stdout == ""


def test_the_notice_matches_the_tool_and_speaks_on_the_console():
    import re
    wait = re.search(r"^SHUTDOWN_WAIT = (\d+)$", SCRIPT.read_text(), re.M).group(1)
    assert f"WAIT = {wait}" in NOTICE.read_text()
    unit = NOTICE_UNIT.read_text()
    assert "StandardOutput=journal+console" in unit and "After=user.slice" in unit
    assert "ExecStop=/usr/libexec/pengyos/pengyos-vm-shutdown-notice" in unit
    assert os.access(NOTICE, os.X_OK)


# ------------------------------------------------------------------ bridge
def test_bridge_enable_writes_networkd_files_and_the_helper_acl(box: Box):
    box.run("bridge", "enable")  # the one wired NIC is picked
    netdev = box.net("05-pengyos-br0.netdev").read_text()
    assert "Kind=bridge" in netdev and f"MACAddress={NIC_MAC}" in netdev
    port = box.net("05-pengyos-br0-port.network").read_text()
    assert "Name=enp0s31f6" in port and "Bridge=pengyos-br0" in port
    br = box.net("05-pengyos-br0.network").read_text()
    assert "Name=pengyos-br0" in br and "DHCP=yes" in br and "UseHostname=no" in br
    # The bridge replaces 10-pengyos-ethernet.network on this machine, so it carries the
    # same link settings: without them bare LAN names and .local stop resolving.
    assert "UseDomains=yes" in br and "MulticastDNS=yes" in br
    assert (box.root / "etc" / "qemu" / "bridge.conf").read_text().endswith("allow pengyos-br0\n")
    assert ["reload"] in box.calls("networkctl")
    assert ["reconfigure", "enp0s31f6"] in box.calls("networkctl")
    status = json.loads(box.run("bridge", "status", "--json").stdout)
    assert status["enabled"] and status["port"] == "enp0s31f6" and status["helper_setuid"]
    assert status["up"] is False


def test_bridged_vm_uses_the_helper(box: Box):
    box.run("bridge", "enable", "enp0s31f6")
    (box.root / "sys" / "class" / "net" / "pengyos-br0").mkdir()
    box.run("create", "lan", "--iso", str(box.iso), "--net", "bridge")
    argv = box.argv("lan")
    mac = box.vm("lan")["mac"]
    assert opt(argv, "-nic") == [f"bridge,br=pengyos-br0,helper=/usr/libexec/qemu-bridge-helper,"
                                 f"model=virtio-net-pci,mac={mac}"]
    box.run("start", "lan")
    assert json.loads(box.run("list", "--json").stdout)[0]["ssh"] is None
    (box.root / "neigh.json").write_text(json.dumps([{"dst": "10.0.23.150", "lladdr": mac}]))
    assert json.loads(box.run("list", "--json").stdout)[0]["ssh"] == "10.0.23.150:22"
    assert json.loads(box.run("ssh", "lan").stdout)[:2] == ["-p", "22"]


@pytest.mark.parametrize("iface, says", [
    ("wlp4s0", "Wi-Fi, which cannot be bridged"),
    ("eth9", "no network interface eth9"),
    ("veth0", "not a physical wired port"),
])
def test_bridge_enable_refuses(box: Box, iface, says):
    box.nic("veth0", "aa:bb:cc:dd:ee:ff", physical=False)
    assert says in box.run("bridge", "enable", iface, ok=2).stderr
    assert not (box.root / "etc").exists()


def test_bridge_on_a_wifi_only_machine_explains_nat(box: Box):
    shutil.rmtree(box.root / "sys" / "class" / "net" / "enp0s31f6")
    assert "use --net nat" in box.run("bridge", "enable", ok=2).stderr


def test_bridge_disable_removes_only_its_own_files(box: Box):
    box.run("bridge", "enable")
    (box.root / "sys" / "class" / "net" / "pengyos-br0").mkdir()
    box.run("create", "lan", "--iso", str(box.iso), "--net", "bridge")
    keep = box.net("50-mine.network")
    keep.write_text("[Match]\nName=enp9s0\n")
    out = box.run("bridge", "disable").stdout
    assert "will not start" in out and "lan" in out
    assert sorted(p.name for p in box.net("").iterdir()) == ["50-mine.network"]
    assert not (box.root / "etc" / "qemu" / "bridge.conf").exists()
    assert ["link", "delete", "pengyos-br0"] in box.calls("ip")
    assert "already off" in box.run("bridge", "disable").stdout


def test_a_hand_written_bridge_conf_is_left_alone(box: Box):
    conf = box.root / "etc" / "qemu" / "bridge.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("allow br-lab\n")
    assert "already off" in box.run("bridge", "disable").stdout
    assert conf.read_text() == "allow br-lab\n"


# ----------------------------------------------------------------- wiring
def test_the_unit_runs_the_tool():
    text = UNIT.read_text()
    assert "ExecStart=/usr/bin/pengyos-vm _run %i" in text
    assert "ExecStop=/usr/bin/pengyos-vm _powerdown %i" in text
    assert "WantedBy=default.target" in text
    assert "StandardOutput=append:%h/vms/%i/qemu.log" in text


def test_the_helper_is_setuid_in_the_image_and_bridged_frames_pass():
    perms = (BOARD / "server" / "permissions.txt").read_text()
    assert "/usr/libexec/qemu-bridge-helper\tf\t4755\t0\t0" in perms
    nft = (BOARD / "core" / "rootfs-overlay" / "etc" / "nftables.conf").read_text()
    assert 'iifname "pengyos-br0" oifname "pengyos-br0" accept' in nft


# --- 0.5.0: the screen is private -------------------------------------------
# Before, every VM's screen was password-less VNC on 127.0.0.1:59xx, so any
# account on the machine could watch and type into any user's VM.

def test_vm_folder_and_vms_are_owner_only(box):
    box.run("create", "deb", "--iso", str(box.iso))
    d = box.home / "vms" / "deb"
    assert d.stat().st_mode & 0o777 == 0o700
    assert (box.home / "vms").stat().st_mode & 0o777 == 0o700


def test_start_makes_an_old_open_folder_private(box):
    box.run("create", "deb", "--iso", str(box.iso))
    d = box.home / "vms" / "deb"
    d.chmod(0o755)
    (box.home / "vms").chmod(0o755)
    box.run("_run", "deb")
    assert d.stat().st_mode & 0o777 == 0o700
    assert (box.home / "vms").stat().st_mode & 0o777 == 0o700


def test_no_tcp_vnc_anywhere(box):
    box.run("create", "deb", "--iso", str(box.iso))
    argv = json.loads(box.run("_run", "deb").stdout)
    assert [x.startswith("unix:") for x in opt(argv, "-vnc")] == [True]
    shown = json.loads(box.run("show", "deb", "--json").stdout)
    assert "vnc" not in shown
    assert shown["vnc_socket"] == str(box.home / "vms" / "deb" / "vnc.sock")


def test_vnc_tells_how_to_reach_the_socket(box):
    box.run("create", "deb", "--iso", str(box.iso))
    out = box.run("vnc", "deb").stdout
    sock = box.home / "vms" / "deb" / "vnc.sock"
    assert f"ssh -N -L 5901:{sock} " in out
    assert "vnc://localhost:5901" in out


# --- snapshots (0.6.0): disk only ---------------------------------------------

class FakeQMP:
    """A QMP socket for a 'running' VM: greets, accepts every command, records it,
    and applies the two snapshot commands to the fake qcow2 like QEMU would."""

    def __init__(self, vm_dir: Path, fail: str | None = None):
        self.calls: list[dict] = []
        self.dir, self.fail = vm_dir, fail
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(vm_dir / "qmp.sock"))
        self.sock.listen(4)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            f = conn.makefile("rwb", buffering=0)
            f.write(b'{"QMP": {}}\n')
            for line in f:
                msg = json.loads(line)
                self.calls.append(msg)
                if msg["execute"] == self.fail:
                    f.write(b'{"error": {"class": "GenericError", "desc": "Device has no medium"}}\n')
                    continue
                disk = self.dir / "disk.qcow2"
                d = json.loads(disk.read_text())
                args = msg.get("arguments", {})
                if msg["execute"] == "blockdev-snapshot-internal-sync":
                    d.setdefault("snapshots", []).append({"name": args["name"], "date-sec": 1764250000})
                elif msg["execute"] == "blockdev-snapshot-delete-internal-sync":
                    d["snapshots"] = [x for x in d.get("snapshots", []) if x["name"] != args["name"]]
                disk.write_text(json.dumps(d))
                f.write(b'{"return": {}}\n')
            conn.close()

    def close(self):
        self.sock.close()


def _running(box: Box, name: str) -> None:
    units = box.root / "units"
    units.mkdir(exist_ok=True)
    (units / f"pengyos-vm@{name}.service").write_text("active")


def _disk(box: Box, name: str) -> dict:
    return json.loads((box.home / "vms" / name / "disk.qcow2").read_text())


def test_snap_stopped_create_list_revert_delete(box):
    box.run("create", "deb", "--iso", str(box.iso))
    d = box.home / "vms" / "deb"
    out = box.run("snapshot", "deb", "create", "before", "the", "upgrade").stdout
    assert "snapshot s1 (before the upgrade) taken" in out and "power cut" not in out
    assert ["snapshot", "-c", "s1", str(d / "disk.qcow2")] in box.calls("qemu-img")
    assert (d / "snapshots" / "s1.vars.fd").read_bytes() == b"VARS"
    box.run("snapshot", "deb", "create")
    listed = json.loads(box.run("snapshot", "deb", "list", "--json").stdout)
    assert [(x["name"], x["label"], x["taken"]) for x in listed["snapshots"]] == [
        ("s1", "before the upgrade", "stopped"), ("s2", "", "stopped")]
    assert "s1" in box.run("snapshot", "deb", "list").stdout
    # The guest changed its UEFI variables after s1; revert puts s1's back.
    (d / "vars.fd").write_bytes(b"CHANGED")
    box.run("snapshot", "deb", "revert", "s1", ok=2)            # no tty, no --yes: refuses
    assert "reverted-to" not in _disk(box, "deb")
    box.run("snapshot", "deb", "revert", "s1", "--yes")
    assert _disk(box, "deb")["reverted-to"] == "s1"
    assert (d / "vars.fd").read_bytes() == b"VARS"
    box.run("snapshot", "deb", "delete", "s1")
    assert [x["name"] for x in _disk(box, "deb")["snapshots"]] == ["s2"]
    assert not (d / "snapshots" / "s1.json").exists() and not (d / "snapshots" / "s1.vars.fd").exists()
    # The next one never reuses a name still in use.
    box.run("snapshot", "deb", "create")
    assert [x["name"] for x in _disk(box, "deb")["snapshots"]] == ["s2", "s3"]
    assert [x["name"] for x in json.loads(box.run("show", "deb", "--json").stdout)["snapshots"]] == ["s2", "s3"]


def test_snap_running_uses_qmp_and_revert_restarts(box):
    box.run("create", "r", "--iso", str(box.iso))
    d = box.home / "vms" / "r"
    _running(box, "r")
    q = FakeQMP(d)
    try:
        out = box.run("snapshot", "r", "create", "pre").stdout
        assert "power cut" in out
        assert {"execute": "blockdev-snapshot-internal-sync",
                "arguments": {"device": "disk0", "name": "s1"}} in q.calls
        assert not any(c[:2] == ["snapshot", "-c"] for c in box.calls("qemu-img"))
        assert json.loads((d / "snapshots" / "s1.json").read_text())["taken"] == "running"
        box.run("snapshot", "r", "revert", "s1", "--yes")
        sysd = box.calls("systemctl")
        kill = sysd.index(["--user", "kill", "--signal=SIGKILL", "pengyos-vm@r.service"])
        start = max(i for i, c in enumerate(sysd) if c == ["--user", "start", "pengyos-vm@r.service"])
        assert kill < start                    # off, rolled back, then started again
        assert _disk(box, "r")["reverted-to"] == "s1"
        _running(box, "r")
        box.run("snapshot", "r", "delete", "s1")
        assert {"execute": "blockdev-snapshot-delete-internal-sync",
                "arguments": {"device": "disk0", "name": "s1"}} in q.calls
    finally:
        q.close()


def test_snap_qemu_error_is_reported_and_leaves_no_files(box):
    box.run("create", "e", "--iso", str(box.iso))
    d = box.home / "vms" / "e"
    _running(box, "e")
    q = FakeQMP(d, fail="blockdev-snapshot-internal-sync")
    try:
        err = box.run("snapshot", "e", "create", ok=1).stderr
        assert "Device has no medium" in err
        assert list((d / "snapshots").iterdir()) == []
    finally:
        q.close()


def test_snap_unknown_names_are_misuse(box):
    box.run("create", "u", "--iso", str(box.iso))
    assert "has no snapshot 's9'" in box.run("snapshot", "u", "revert", "s9", "--yes", ok=2).stderr
    assert "needs one snapshot name" in box.run("snapshot", "u", "delete", ok=2).stderr


# --- the ISO library (0.7.0) --------------------------------------------------

def _iso_bytes(n: int = 0x9000) -> bytes:
    data = bytearray(n)
    data[0x8001:0x8006] = b"CD001"
    return bytes(data)


def _serve(directory: Path):
    import functools
    import http.server
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_iso_add_file_list_use_and_remove(box, tmp_path):
    src = tmp_path / "debian-13.2.0-amd64-netinst.iso"
    src.write_bytes(_iso_bytes())
    out = box.run("iso", "add", str(src)).stdout
    assert "added debian-13.2.0-amd64-netinst.iso" in out
    lib = box.home / "vms" / "isos"
    kept = lib / src.name
    assert kept.stat().st_ino == src.stat().st_ino      # same filesystem: a hard link, no copy
    assert lib.stat().st_mode & 0o777 == 0o700
    assert not [p for p in lib.iterdir() if p.name.startswith(".")]
    # create takes the library name, with or without .iso
    box.run("create", "deb", "--iso", "debian-13.2.0-amd64-netinst")
    assert box.vm("deb")["iso"] == str(kept.resolve())
    listed = json.loads(box.run("iso", "list", "--json").stdout)["isos"]
    assert [(i["name"], i["used_by"]) for i in listed] == [(src.name, ["deb"])]
    assert "deb" in box.run("iso", "list").stdout
    # not while a VM has it attached
    err = box.run("iso", "remove", src.name, ok=2).stderr
    assert "attached to deb" in err and "pengyos-vm set deb --eject" in err
    box.run("set", "deb", "--eject")
    box.run("iso", "remove", src.name)
    assert not kept.exists() and src.exists()


def test_iso_add_refuses_non_iso_duplicates_and_bad_names(box, tmp_path):
    junk = tmp_path / "notes.iso"
    junk.write_text("<html>404</html>")
    assert "not an ISO image" in box.run("iso", "add", str(junk), ok=2).stderr
    good = tmp_path / "a.iso"
    good.write_bytes(_iso_bytes())
    box.run("iso", "add", str(good))
    assert "already has a.iso" in box.run("iso", "add", str(good), ok=2).stderr
    assert "ISO names" in box.run("iso", "add", str(good), "--name", "../x", ok=2).stderr
    box.run("iso", "add", str(good), "--name", "copy")          # .iso is added
    assert (box.home / "vms" / "isos" / "copy.iso").is_file()
    assert "not a file, nor in the ISO library" in box.run(
        "create", "x", "--iso", "missing", ok=2).stderr


def test_iso_add_url_with_progress_and_a_bad_url(box, tmp_path):
    www = tmp_path / "www"
    www.mkdir()
    (www / "alpine-virt.iso").write_bytes(_iso_bytes(3 << 20))
    (www / "error.iso").write_text("<html>not found</html>")
    srv, base = _serve(www)
    try:
        cp = box.run("iso", "add", f"{base}/alpine-virt.iso?mirror=1", "--progress")
        lines = [json.loads(x) for x in cp.stdout.splitlines()]
        assert lines[-2]["step"] == "download" and lines[-2]["done"] == 3 << 20
        assert lines[-1] == {"step": "done", "name": "alpine-virt.iso",
                             "path": str(box.home / "vms" / "isos" / "alpine-virt.iso")}
        err = box.run("iso", "add", f"{base}/error.iso", ok=1).stderr
        assert "not an ISO image" in err and "check the URL" in err
        err = box.run("iso", "add", f"{base}/missing.iso", ok=1).stderr
        assert "answered 404" in err and "Traceback" not in err
        assert sorted(p.name for p in (box.home / "vms" / "isos").iterdir()) == ["alpine-virt.iso"]
    finally:
        srv.shutdown()


def test_reserved_names_are_not_vms(box, tmp_path):
    assert "ISO library" in box.run("create", "isos", "--iso", str(box.iso), ok=2).stderr
    assert "disk images" in box.run("create", "images", "--iso", str(box.iso), ok=2).stderr
    good = tmp_path / "a.iso"
    good.write_bytes(_iso_bytes())
    box.run("iso", "add", str(good))
    assert "no VMs yet" in box.run("list").stdout
