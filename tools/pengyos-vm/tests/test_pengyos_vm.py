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
import subprocess
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_DIR.parents[1]
BOARD = REPO_ROOT / "buildroot-external" / "board"
SCRIPT = BOARD / "server" / "rootfs-overlay" / "usr" / "bin" / "pengyos-vm"
UNIT = BOARD / "server" / "rootfs-overlay" / "usr" / "lib" / "systemd" / "user" / "pengyos-vm@.service"
HERE = Path(__file__).resolve().parent
FAKES = ("systemctl", "qemu-img", "networkctl", "ip", "ssh", "journalctl", "loginctl")
NIC_MAC = "54:e1:ad:5a:60:8a"


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
    assert opt(argv, "-vnc") == ["127.0.0.1:1"]
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


# ------------------------------------------------------------------ bridge
def test_bridge_enable_writes_networkd_files_and_the_helper_acl(box: Box):
    box.run("bridge", "enable")  # the one wired NIC is picked
    netdev = box.net("05-pengyos-br0.netdev").read_text()
    assert "Kind=bridge" in netdev and f"MACAddress={NIC_MAC}" in netdev
    port = box.net("05-pengyos-br0-port.network").read_text()
    assert "Name=enp0s31f6" in port and "Bridge=pengyos-br0" in port
    br = box.net("05-pengyos-br0.network").read_text()
    assert "Name=pengyos-br0" in br and "DHCP=yes" in br and "UseHostname=no" in br
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
