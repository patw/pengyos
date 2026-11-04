"""Tests for `pengyos-services`, the shipped tool in the Core overlay.

The suite runs the real script against a sandbox root (`--root DIR`): DIR holds
the files the tool reads (/etc, /home, /proc/PID/cgroup, /run, /sys/fs/cgroup,
/var/lib/systemd/linger) and one fake (tests/fake-tool) stands in for systemctl,
ss, ip, pengyos-firewall, ssh and the podman generators, answering from
DIR/fixture.json. `Machine` below builds such a root.

The main fixture is the machine Pengy's Server review described (spec.md,
"Acceptance criteria"): a system Quadlet publishing 8282, a user Quadlet with no
linger, a Quadlet with an unsupported key, a VM forward on 127.0.0.1:8080 with
8080 opened in the firewall, a firewall rule on 9999 with nothing behind it, and
sshd. Each problem code and each autostart answer has a test of its own.

No root, no systemd, no podman, no network.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO = TOOL_DIR.parents[1]
SCRIPT = REPO / "buildroot-external/board/core/rootfs-overlay/usr/bin/pengyos-services"
FAKE = Path(__file__).resolve().parent / "fake-tool"
FEATURES = REPO / "buildroot-external/board/server/features-required.txt"

PASSWD = """root:x:0:0:root:/root:/bin/bash
pengy:x:1000:1000:Pengy:/home/pengy:/bin/bash
bob:x:1001:1001:Bob:/home/bob:/bin/bash
"""


class Machine:
    def __init__(self, root: Path):
        self.root = root
        self.fx: dict = {"self": "pengy", "systemctl": {"system": {}}, "ss": "", "ip": [
            {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1",
                                            "prefixlen": 8, "scope": "host"}]},
            {"ifname": "enp1s0", "addr_info": [{"family": "inet", "local": "10.0.23.104",
                                                "prefixlen": 24, "scope": "global"}]},
            {"ifname": "podman0", "addr_info": [{"family": "inet", "local": "10.88.0.1",
                                                 "prefixlen": 16, "scope": "global"}]}],
            "firewall": {"tool": "pengyos-firewall", "unit": "nftables.service",
                         "active": "active", "always_open": ["22/tcp", "5353/udp mDNS"],
                         "image_rules": [], "opened": []}}
        for d in ("usr/bin", "usr/lib/systemd/system-generators",
                  "usr/lib/systemd/user-generators"):
            (root / d).mkdir(parents=True, exist_ok=True)
        for t in ("systemctl", "ss", "ip", "pengyos-firewall", "ssh"):
            (root / "usr/bin" / t).symlink_to(FAKE)
        (root / "usr/lib/systemd/system-generators/podman-system-generator").symlink_to(FAKE)
        (root / "usr/lib/systemd/user-generators/podman-user-generator").symlink_to(FAKE)
        self.file("/etc/passwd", PASSWD)
        self.file("/etc/hostname", "behothinkpad\n")
        self.file("/etc/os-release", 'NAME="PengyOS"\nVARIANT_ID=server\nIMAGE_VERSION="26.11.0"\n')
        (root / "run/user/1000").mkdir(parents=True)
        self.pid = 400

    # --- building blocks -----------------------------------------------------
    def file(self, path: str, text: str) -> Path:
        p = self.root / path.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def link(self, path: str, to: str) -> None:
        p = self.root / path.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.symlink_to(to)

    def unit(self, scope: str, name: str, **props) -> dict:
        d = self.fx["systemctl"].setdefault(scope, {}).setdefault(name, {})
        d.update(props)
        return d

    def listen(self, proto: str, addr: str, port: int, proc: str | None,
               cgroup: str | None = None) -> int:
        self.pid += 1
        state = "LISTEN" if proto == "tcp" else "UNCONN"
        users = f'users:(("{proc}",pid={self.pid},fd=3))' if proc else ""
        self.fx["ss"] += f"{proto}   {state} 0      128   {addr}:{port}   0.0.0.0:*    {users}\n"
        if cgroup:
            self.file(f"/proc/{self.pid}/cgroup", f"0::{cgroup}\n")
        return self.pid

    def allow(self, label: str, file: str | None = None) -> None:
        self.fx["firewall"]["opened"].append(
            {"file": file or f"allow-{label.replace('/', '-').replace(' ', '')}.nft",
             "opens": label})

    def linger(self, user: str) -> None:
        self.file(f"/var/lib/systemd/linger/{user}", "")

    # --- running ---------------------------------------------------------------
    def run(self, *args: str, uid: int = 0) -> subprocess.CompletedProcess:
        (self.root / "fixture.json").write_text(json.dumps(self.fx))
        env = {**os.environ, "PENGYOS_SERVICES_UID": str(uid)}
        return subprocess.run([str(SCRIPT), *args, "--root", str(self.root)],
                              capture_output=True, text=True, timeout=60, env=env)

    def json(self, *args: str, uid: int = 0) -> dict:
        cp = self.run("status", "--json", *args, uid=uid)
        assert cp.returncode == 0, cp.stderr
        return json.loads(cp.stdout)

    def calls(self) -> list[list[str]]:
        log = self.root / "calls.log"
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def svc(doc: dict, name: str, scope: str | None = None) -> dict:
    hits = [s for s in doc["services"] if s["name"] == name and (scope is None or s["scope"] == scope)]
    assert len(hits) == 1, [(s["name"], s["scope"]) for s in doc["services"]]
    return hits[0]


def problems(doc: dict, code: str) -> list[dict]:
    return [p for p in doc["problems"] if p["code"] == code]


SYS = "/system.slice"
USER = "/user.slice/user-1000.slice/user@1000.service/app.slice"


def pengy_review(m: Machine) -> Machine:
    """The machine from the acceptance criteria."""
    # sshd: an image unit, listed because it listens.
    m.unit("system", "sshd.service", ActiveState="active", SubState="running",
           FragmentPath="/usr/lib/systemd/system/sshd.service", WantedBy="multi-user.target",
           UnitFileState="enabled", ControlGroup=f"{SYS}/sshd.service",
           ActiveEnterTimestamp="@1762160400")
    m.listen("tcp", "0.0.0.0", 22, "sshd", f"{SYS}/sshd.service")
    m.listen("tcp", "[::]", 22, "sshd", f"{SYS}/sshd.service")
    # A system Quadlet publishing 8282: rootful, netavark DNAT, no socket at all.
    m.file("/etc/containers/systemd/web.container",
           "[Container]\nImage=nginx\nPublishPort=8282:80\nVolume=/data/web:/usr/share/nginx/html:ro\n"
           "[Install]\nWantedBy=multi-user.target\n")
    m.file("/etc/containers/systemd/README", "not a quadlet\n")
    m.link("/run/systemd/generator/multi-user.target.wants/web.service", "../web.service")
    m.unit("system", "web.service", ActiveState="active", SubState="running",
           SourcePath="/etc/containers/systemd/web.container",
           FragmentPath="/run/systemd/generator/web.service", WantedBy="multi-user.target",
           UnitFileState="generated", ControlGroup=f"{SYS}/web.service")
    # A user Quadlet, no linger.
    m.file("/home/pengy/.config/containers/systemd/staging-jellyfin.container",
           "[Container]\nImage=jellyfin\nPublishPort=8096:8096\nVolume=/data/media:/media\n"
           "Volume=jelly-config:/config\n[Install]\nWantedBy=default.target\n")
    m.unit("user:pengy", "staging-jellyfin.service", ActiveState="active", SubState="running",
           SourcePath="/home/pengy/.config/containers/systemd/staging-jellyfin.container",
           WantedBy="default.target", UnitFileState="generated",
           ControlGroup=f"{USER}/staging-jellyfin.service")
    m.listen("tcp", "*", 8096, "rootlessport", f"{USER}/staging-jellyfin.service")
    # A Quadlet with a key quadlet does not know: no unit.
    m.file("/etc/containers/systemd/broken.container",
           "[Container]\nImage=x\nEnv=A=1\n")
    m.fx["generator"] = {"system": 'quadlet-generator[812]: converting "broken.container": '
                         "unsupported key 'Env' in group 'Container' in "
                         "/etc/containers/systemd/broken.container\n"}
    # A VM with a loopback forward, and the firewall opening the same port.
    m.file("/home/pengy/vms/web1/vm.json", json.dumps({
        "name": "web1", "net": "nat", "ssh_port": 2201,
        "forwards": [{"proto": "tcp", "addr": "127.0.0.1", "host": 8080, "guest": 80}]}))
    m.unit("user:pengy", "pengyos-vm@web1.service", ActiveState="active", SubState="running",
           FragmentPath="/usr/lib/systemd/user/pengyos-vm@.service",
           ControlGroup=f"{USER}/pengyos-vm@web1.service", UnitFileState="disabled")
    qemu = f"{USER}/pengyos-vm@web1.service"
    m.listen("tcp", "127.0.0.1", 8080, "qemu-system-x86", qemu)
    m.listen("tcp", "127.0.0.1", 2201, "qemu-system-x86", qemu)
    m.allow("8080/tcp")
    # A firewall rule with nothing behind it.
    m.allow("9999/tcp from 10.0.23.0/24")
    # Something the image ships and that does not listen: hidden by default.
    m.unit("system", "systemd-journald.service", ActiveState="active", SubState="running",
           FragmentPath="/usr/lib/systemd/system/systemd-journald.service")
    return m


@pytest.fixture()
def machine(tmp_path: Path) -> Machine:
    return Machine(tmp_path)


@pytest.fixture()
def review(machine: Machine) -> Machine:
    return pengy_review(machine)


# --- packaging -----------------------------------------------------------------
def test_ships_in_core_overlay_executable_python():
    assert SCRIPT.is_file() and os.access(SCRIPT, os.X_OK)
    assert SCRIPT.read_text().startswith("#!/usr/bin/env python3")
    subprocess.run(["python3", "-m", "py_compile", str(SCRIPT)], check=True)


def test_server_feature_claim():
    assert any(l.split()[:2] == ["exists", "usr/bin/pengyos-services"]
               for l in FEATURES.read_text().splitlines())


def test_help_and_misuse():
    assert subprocess.run([str(SCRIPT), "--help"], capture_output=True).returncode == 0
    assert subprocess.run([str(SCRIPT), "frobnicate"], capture_output=True).returncode == 2
    assert subprocess.run([str(SCRIPT), "status", "--bogus"], capture_output=True).returncode == 2


# --- the acceptance machine ------------------------------------------------------
def test_review_machine_json(review: Machine):
    doc = review.json()
    assert doc["tool"] == "pengyos-services" and doc["version"] == 1
    assert doc["host"] == "behothinkpad" and doc["edition"] == "server"
    assert doc["release"] == "26.11.0" and doc["privileged"] is True
    assert doc["lan"] == ["10.0.23.0/24"]            # not lo, not podman0

    web = svc(doc, "web")
    assert web["kind"] == "quadlet" and web["scope"] == "system"
    assert web["autostart"] == "yes"
    assert web["why"] == "quadlet [Install] WantedBy=multi-user.target"
    assert "linger" not in web
    [p] = web["ports"]
    assert p["port"] == 8282 and p["reachable"] == "yes" and p["basis"] == "by rules"
    assert "DNAT" in p["firewall"]
    assert web["data"] == ["/data/web"]

    jelly = svc(doc, "staging-jellyfin")
    assert jelly["autostart"] == "at-login-only" and jelly["linger"] is False
    assert jelly["data"] == ["/data/media",
                             "/home/pengy/.local/share/containers/storage/volumes/jelly-config/_data"]
    [p] = jelly["ports"]
    assert (p["port"], p["bind"], p["firewall"], p["reachable"]) == \
        (8096, "all", "blocked", "no — blocked by the firewall")

    ssh = svc(doc, "sshd")
    [p] = ssh["ports"]
    assert (p["port"], p["firewall"], p["reachable"]) == (22, "always-open", "yes")
    assert ssh["autostart"] == "yes" and ssh["since"] == "2025-11-03T09:00:00Z"

    vm = svc(doc, "web1")
    assert vm["kind"] == "vm" and vm["source"] == "/home/pengy/vms/web1/vm.json"
    assert vm["autostart"] == "no" and vm["why"] == "pengyos-vm autostart web1 on"
    ports = {p["port"]: p for p in vm["ports"]}
    assert ports[8080]["owner"] == "vm forward" and ports[8080]["bind"] == "loopback"
    assert ports[8080]["reachable"] == "no — bound to loopback"
    assert ports[2201]["owner"] == "vm ssh"

    names = [s["name"] for s in doc["services"]]
    assert "systemd-journald" not in names           # image, not listening
    assert "broken" in names

    codes = sorted(p["code"] for p in doc["problems"])
    assert codes == ["loopback-only-forward", "open-not-served", "quadlet-no-unit",
                     "served-not-reachable", "user-no-linger"]
    [w] = problems(doc, "served-not-reachable")      # jellyfin: listening, port closed
    assert w["service"] == "staging-jellyfin" and w["severity"] == "warning"


def test_quadlet_no_unit_names_the_key(review: Machine):
    [p] = problems(review.json(), "quadlet-no-unit")
    assert p["service"] == "broken" and p["key"] == "Container/Env"
    assert "unsupported key 'Env'" in p["detail"]
    assert "/etc/containers/systemd/broken.container" in p["detail"]
    assert "daemon-reload" in p["fix"]


def test_loopback_forward_fix_is_the_pengyos_vm_command(review: Machine):
    [p] = problems(review.json(), "loopback-only-forward")
    assert p["service"] == "web1"
    assert p["fix"] == "pengyos-vm set web1 --unforward 8080 --forward 0.0.0.0:8080:80"


def test_loopback_container_publish_fix_names_the_container_port(machine: Machine):
    machine.file("/etc/containers/systemd/adm.container",
                 "[Container]\nImage=x\nPublishPort=127.0.0.1:9090:90\n")
    machine.unit("system", "adm.service", ActiveState="inactive",
                 SourcePath="/etc/containers/systemd/adm.container")
    machine.allow("9090/tcp")
    [p] = problems(machine.json(), "loopback-only-forward")
    assert "PublishPort=9090:90" in p["fix"]


def test_open_not_served(review: Machine):
    doc = review.json()
    assert [o["rule"] for o in doc["open_not_served"]] == ["9999/tcp from 10.0.23.0/24"]
    [p] = problems(doc, "open-not-served")
    assert p["fix"].startswith("sudo pengyos-firewall remove 9999 --from 10.0.23.0/24")


def test_linger_fix_and_linger_on(review: Machine):
    [p] = problems(review.json(), "user-no-linger")
    assert p["fix"] == "sudo loginctl enable-linger pengy"
    review.linger("pengy")
    doc = review.json()
    jelly = svc(doc, "staging-jellyfin")
    assert jelly["autostart"] == "yes" and jelly["why"].endswith("linger on")
    assert not problems(doc, "user-no-linger")


def test_root_inspects_other_users_with_machine_flag(review: Machine):
    review.linger("bob")
    (review.root / "run/user/1001").mkdir(parents=True)
    review.json()
    user_calls = [c for c in review.calls() if c[0] == "systemctl" and "--user" in c]
    assert user_calls and all("-M" in c for c in user_calls)
    assert {c[c.index("-M") + 1] for c in user_calls} == {"pengy@", "bob@"}


def test_user_nobody_logged_in_is_read_from_files(machine: Machine):
    """bob has a Quadlet, no linger, and no manager running: the after-a-reboot case."""
    machine.file("/home/bob/.config/containers/systemd/notes.container",
                 "[Container]\nImage=x\nPublishPort=8300:80\n[Install]\nWantedBy=default.target\n")
    doc = machine.json()
    notes = svc(doc, "notes", "user:bob")
    assert notes["state"] == "inactive" and notes["autostart"] == "at-login-only"
    assert notes["sub"] == "no user manager (nobody logged in)"
    assert notes["ports"][0]["reachable"] == "no — not listening"
    [p] = problems(doc, "user-no-linger")
    assert p["scope"] == "user:bob"
    assert not any("bob@" in c for c in machine.calls())    # no manager to ask


# --- not root -----------------------------------------------------------------------
def test_not_root_sees_self_and_says_what_it_missed(machine: Machine):
    machine.linger("bob")
    machine.unit("system", "sshd.service", ActiveState="active",
                 FragmentPath="/usr/lib/systemd/system/sshd.service")
    machine.listen("tcp", "0.0.0.0", 22, None)        # not ours: ss shows no process
    doc = machine.json(uid=1000)
    assert doc["privileged"] is False
    assert doc["not_seen"] == ["user:bob"]
    assert any("not root" in n for n in doc["notes"])
    [u] = doc["unowned_listeners"]
    assert u["port"] == 22 and u["owner"] == "owner unknown: run with sudo"
    user_calls = [c for c in machine.calls() if c[0] == "systemctl" and "--user" in c]
    assert user_calls and not any("-M" in c for c in user_calls)


# --- autostart answers -------------------------------------------------------------
def test_enabled_owner_unit_and_disabled_one(machine: Machine):
    machine.file("/etc/systemd/system/app.service", "[Service]\nExecStart=/x\n")
    machine.link("/etc/systemd/system/multi-user.target.wants/app.service", "../app.service")
    machine.unit("system", "app.service", ActiveState="active", WantedBy="multi-user.target",
                 FragmentPath="/etc/systemd/system/app.service", UnitFileState="enabled")
    machine.file("/etc/systemd/system/idle.service", "[Service]\nExecStart=/x\n")
    machine.unit("system", "idle.service", ActiveState="inactive",
                 FragmentPath="/etc/systemd/system/idle.service", UnitFileState="disabled")
    doc = machine.json()
    app = svc(doc, "app")
    assert (app["autostart"], app["why"]) == ("yes", "enabled: multi-user.target.wants")
    idle = svc(doc, "idle")
    assert idle["autostart"] == "no"
    assert idle["why"] == "not enabled (disabled): sudo systemctl enable idle.service"


def test_pulled_in_through_an_intermediate_target(machine: Machine):
    machine.file("/etc/systemd/system/a.service", "")
    machine.unit("system", "a.service", WantedBy="stack.target", ActiveState="active",
                 FragmentPath="/etc/systemd/system/a.service")
    machine.unit("system", "stack.target", WantedBy="multi-user.target")
    a = svc(machine.json(), "a")
    assert a["autostart"] == "yes" and a["why"] == "pulled in by stack.target → multi-user.target"


def test_quadlet_without_install_says_how(machine: Machine):
    machine.file("/etc/containers/systemd/db.container", "[Container]\nImage=pg\n")
    machine.unit("system", "db.service", SourcePath="/etc/containers/systemd/db.container",
                 ActiveState="inactive", UnitFileState="generated")
    db = svc(machine.json(), "db")
    assert db["autostart"] == "no"
    assert "add [Install] WantedBy=multi-user.target" in db["why"]


def test_via_timer_and_on_demand(machine: Machine):
    machine.file("/etc/systemd/system/backup.service", "")
    machine.unit("system", "backup.service", TriggeredBy="backup.timer",
                 FragmentPath="/etc/systemd/system/backup.service", ActiveState="inactive")
    machine.unit("system", "backup.timer", WantedBy="timers.target", ActiveState="active",
                 NextElapseUSecRealtime="@1762218000")
    machine.file("/etc/systemd/system/echo.service", "")
    machine.unit("system", "echo.service", TriggeredBy="echo.socket",
                 FragmentPath="/etc/systemd/system/echo.service")
    machine.unit("system", "echo.socket", WantedBy="sockets.target", ActiveState="active")
    doc = machine.json()
    assert svc(doc, "backup")["autostart"] == "via-timer (backup.timer, next run 2025-11-04T01:00:00Z)"
    assert svc(doc, "echo")["autostart"] == "on-demand (echo.socket)"


def test_vm_autostart_on_is_at_login_only_without_linger(machine: Machine):
    machine.file("/home/pengy/vms/nas/vm.json", json.dumps({"name": "nas", "net": "bridge",
                                                            "forwards": []}))
    machine.link("/home/pengy/.config/systemd/user/default.target.wants/pengyos-vm@nas.service",
                 "/usr/lib/systemd/user/pengyos-vm@.service")
    machine.unit("user:pengy", "pengyos-vm@nas.service", ActiveState="inactive")
    nas = svc(machine.json(), "nas")
    assert nas["autostart"] == "at-login-only"
    assert nas["why"].startswith("enabled: default.target.wants")
    assert nas["ports"] == []                           # bridged: no forwards


def test_overlay_etc_lists_only_the_owners_units(machine: Machine):
    """PengyOS /etc is an overlay; the image's own units live in its lower layer."""
    machine.file("/proc/self/mountinfo",
                 "25 1 0:30 / / ro - erofs /dev/root ro\n"
                 "36 25 0:32 / /etc rw,relatime - overlay overlay "
                 "rw,lowerdir=/etc,upperdir=/data/etc/upper,workdir=/data/etc/work\n")
    for n in ("pengyos-banner", "mine"):
        machine.file(f"/etc/systemd/system/{n}.service", "")
        machine.unit("system", f"{n}.service", ActiveState="inactive",
                     FragmentPath=f"/etc/systemd/system/{n}.service")
    machine.file("/data/etc/upper/systemd/system/mine.service", "")
    names = [s["name"] for s in machine.json()["services"]]
    assert names == ["mine"]


def test_plain_etc_uses_the_image_unit_list(machine: Machine):
    for n in ("pengyos-banner", "mine"):
        machine.file(f"/etc/systemd/system/{n}.service", "")
        machine.unit("system", f"{n}.service", ActiveState="inactive",
                     FragmentPath=f"/etc/systemd/system/{n}.service")
    machine.file("/usr/lib/pengyos/image-units.txt", "etc/systemd/system/pengyos-banner.service\n")
    assert [s["name"] for s in machine.json()["services"]] == ["mine"]


def test_core_post_build_writes_the_image_unit_list():
    text = (REPO / "buildroot-external/board/core/post-build.sh").read_text()
    assert "usr/lib/pengyos/image-units.txt" in text


def test_alias_links_are_not_units(machine: Machine):
    machine.file("/etc/systemd/system/avahi-daemon.service", "")
    machine.link("/etc/systemd/system/dbus-org.freedesktop.Avahi.service",
                 "/etc/systemd/system/avahi-daemon.service")
    machine.unit("system", "avahi-daemon.service", ActiveState="active",
                 FragmentPath="/etc/systemd/system/avahi-daemon.service")
    assert [s["name"] for s in machine.json()["services"]] == ["avahi-daemon"]


def test_podman_run_inside_a_unit_is_traced_through_conmon(machine: Machine):
    cid = "e412ee84782bbb900bdd3cd74c58ba240d187def73c45fd1a3816094c86ba842"
    machine.file("/home/pengy/.config/systemd/user/board.service", "")
    machine.unit("user:pengy", "board.service", ActiveState="active", WantedBy="default.target",
                 FragmentPath="/home/pengy/.config/systemd/user/board.service")
    machine.listen("tcp", "0.0.0.0", 5050, "python3",
                   f"/user.slice/user-1000.slice/user@1000.service/user.slice/libpod-{cid}.scope")
    machine.file("/proc/77/cgroup", f"0::{USER}/board.service\n")
    (machine.root / "proc/77/cmdline").write_bytes(
        b"/usr/bin/conmon\0--api-version\0001\0-c\0" + cid.encode() + b"\0")
    doc = machine.json()
    assert doc["unowned_listeners"] == []
    [p] = svc(doc, "board")["ports"]
    assert p["port"] == 5050 and p["owner"] == "listener (python3)"


def test_systemctl_that_does_not_answer_is_unknown_not_missing(machine: Machine):
    machine.file("/home/pengy/.config/systemd/user/x.service", "")
    machine.fx["systemctl"]["user:pengy"] = None    # the fake prints nothing
    (machine.root / "usr/bin/systemctl").unlink()
    machine.file("/usr/bin/systemctl", "#!/bin/sh\necho 'Failed to connect to bus' >&2\nexit 1\n")
    os.chmod(machine.root / "usr/bin/systemctl", 0o755)
    doc = machine.json(uid=1000)
    x = svc(doc, "x")
    assert x["state"] == "unknown" and x["autostart"] == "unknown"
    assert any("Failed to connect to bus" in n for n in doc["notes"])


# --- state, memory ---------------------------------------------------------------------
def test_failed_image_unit_is_listed_and_restart_loops_count(machine: Machine):
    machine.unit("system", "smartd.service", ActiveState="failed", Result="exit-code",
                 FragmentPath="/usr/lib/systemd/system/smartd.service")
    machine.file("/etc/systemd/system/flappy.service", "")
    machine.unit("system", "flappy.service", ActiveState="active", NRestarts="7",
                 FragmentPath="/etc/systemd/system/flappy.service")
    doc = machine.json()
    assert svc(doc, "smartd")["state"] == "failed"
    got = {p["service"]: p["detail"] for p in problems(doc, "failed")}
    assert got == {"smartd": "failed (exit-code)", "flappy": "restarted 7 times since boot"}


def test_memory_is_peak_and_anon_never_page_cache(machine: Machine):
    machine.file("/etc/systemd/system/sync.service", "")
    machine.unit("system", "sync.service", ActiveState="active",
                 FragmentPath="/etc/systemd/system/sync.service",
                 ControlGroup=f"{SYS}/sync.service", MemoryCurrent="10500000000")
    machine.file(f"/sys/fs/cgroup{SYS}/sync.service/memory.peak", "412000000\n")
    machine.file(f"/sys/fs/cgroup{SYS}/sync.service/memory.stat",
                 "anon 148000000\nfile 9800000000\nkernel 1000\n")
    s = svc(machine.json(), "sync")
    assert s["memory"] == {"peak": 412000000, "anon": 148000000, "file": 9800000000}
    out = machine.run("status", "sync").stdout
    assert "anon 141.1M" in out and "page cache 9.1G, not counted" in out


# --- ports and the firewall ----------------------------------------------------------------
def test_port_collision_container_against_vm(machine: Machine):
    machine.file("/home/pengy/.config/containers/systemd/app.container",
                 "[Container]\nImage=x\nPublishPort=8080:80\n")
    machine.unit("user:pengy", "app.service", ActiveState="inactive",
                 SourcePath="/home/pengy/.config/containers/systemd/app.container")
    machine.file("/home/pengy/vms/web1/vm.json", json.dumps({
        "name": "web1", "net": "nat", "ssh_port": 2201,
        "forwards": [{"proto": "tcp", "addr": "0.0.0.0", "host": 8080, "guest": 80}]}))
    machine.unit("user:pengy", "pengyos-vm@web1.service", ActiveState="inactive")
    [p] = problems(machine.json(), "port-collision")
    assert "8080/tcp" in p["detail"] and "app" in p["detail"] and "web1" in p["detail"]


def test_distinct_addresses_do_not_collide(machine: Machine):
    for n, addr in (("a", "127.0.0.1"), ("b", "10.0.23.104")):
        machine.file(f"/etc/containers/systemd/{n}.container",
                     f"[Container]\nImage=x\nPublishPort={addr}:9000:80\n")
        machine.unit("system", f"{n}.service", ActiveState="inactive",
                     SourcePath=f"/etc/containers/systemd/{n}.container")
    assert not problems(machine.json(), "port-collision")


def test_served_not_reachable_is_a_warning_and_check_ignores_it(machine: Machine):
    machine.file("/etc/systemd/system/api.service", "")
    machine.unit("system", "api.service", ActiveState="active",
                 FragmentPath="/etc/systemd/system/api.service")
    machine.listen("tcp", "0.0.0.0", 5000, "uvicorn", f"{SYS}/api.service")
    doc = machine.json()
    [p] = problems(doc, "served-not-reachable")
    assert p["severity"] == "warning"
    assert p["fix"] == "sudo pengyos-firewall allow 5000 --from 10.0.23.0/24"
    assert machine.run("status", "--check").returncode == 0
    # An image service on a closed port (LLMNR, say) is not flagged at all.
    machine.unit("system", "systemd-resolved.service", ActiveState="active",
                 FragmentPath="/usr/lib/systemd/system/systemd-resolved.service")
    machine.listen("tcp", "0.0.0.0", 5355, "systemd-resolve", f"{SYS}/systemd-resolved.service")
    assert len(problems(machine.json(), "served-not-reachable")) == 1


def test_check_fails_on_errors(review: Machine):
    assert review.run("status", "--check").returncode == 1


def test_firewall_cidr_outside_the_lan_does_not_count(machine: Machine):
    machine.file("/etc/systemd/system/api.service", "")
    machine.unit("system", "api.service", ActiveState="active",
                 FragmentPath="/etc/systemd/system/api.service")
    machine.listen("tcp", "0.0.0.0", 5000, "uvicorn", f"{SYS}/api.service")
    machine.allow("5000/tcp from 192.168.9.0/24")
    [p] = svc(machine.json(), "api")["ports"]
    assert p["firewall"] == "opened 192.168.9.0/24 (not the LAN)"
    assert p["reachable"] == "no — blocked by the firewall"


def test_desktop_image_rule_and_firewall_off(machine: Machine):
    machine.file("/etc/systemd/system/game.service", "")
    machine.unit("system", "game.service", ActiveState="active",
                 FragmentPath="/etc/systemd/system/game.service")
    machine.listen("udp", "0.0.0.0", 25565, "java", f"{SYS}/game.service")
    machine.fx["firewall"]["image_rules"] = ["input-desktop.nft"]
    [p] = svc(machine.json(), "game")["ports"]
    assert p["proto"] == "udp" and p["reachable"] == "yes" and p["firewall"].startswith("image-rule")
    machine.fx["firewall"]["image_rules"] = []
    machine.fx["firewall"]["active"] = "inactive"
    [p] = svc(machine.json(), "game")["ports"]
    assert p["firewall"].startswith("off") and p["reachable"] == "yes"


def test_unowned_listener_and_pasta_outside_the_unit(machine: Machine):
    machine.listen("tcp", "0.0.0.0", 7000, "python3",
                   "/user.slice/user-1000.slice/session-3.scope")
    machine.file("/home/pengy/.config/containers/systemd/app.container",
                 "[Container]\nImage=x\nPublishPort=8090:80\n")
    machine.unit("user:pengy", "app.service", ActiveState="active",
                 SourcePath="/home/pengy/.config/containers/systemd/app.container")
    machine.listen("tcp", "*", 8090, "pasta", "/user.slice/user-1000.slice/user@1000.service/x.scope")
    doc = machine.json()
    [u] = doc["unowned_listeners"]
    assert u["port"] == 7000 and u["owner"] == "python3 (pid 401, session-3.scope, user pengy)"
    [p] = svc(doc, "app")["ports"]
    assert p["listening"] and p["bind"] == "all"


def test_probe_from_marks_tested(machine: Machine):
    machine.file("/etc/systemd/system/api.service", "")
    machine.unit("system", "api.service", ActiveState="active",
                 FragmentPath="/etc/systemd/system/api.service")
    machine.listen("tcp", "0.0.0.0", 5000, "uvicorn", f"{SYS}/api.service")
    machine.allow("5000/tcp")
    machine.fx["ssh"] = {"10.0.23.104 5000": "no"}
    doc = machine.json("--probe-from", "behobasement")
    [p] = svc(doc, "api")["ports"]
    assert p["basis"] == "tested from behobasement" and p["reachable"].startswith("no")
    assert any(c[:1] == ["ssh"] and "behobasement" in c for c in machine.calls())
    machine.fx["ssh"] = {"10.0.23.104 5000": "ok"}
    [p] = svc(machine.json("--probe-from", "behobasement"), "api")["ports"]
    assert p["reachable"] == "yes"


# --- output, sources missing, read-only, speed --------------------------------------------
def test_one_service_in_full_and_unknown_name(review: Machine):
    cp = review.run("status", "staging-jellyfin")
    assert cp.returncode == 0, cp.stderr
    assert "autostart  at-login-only" in cp.stdout and "user-no-linger" in cp.stdout
    assert "loopback-only-forward" not in cp.stdout
    assert review.run("status", "nosuch").returncode == 2


def test_human_list(review: Machine):
    cp = review.run()
    assert cp.returncode == 0, cp.stderr
    out = cp.stdout
    assert out.splitlines()[0] == "behothinkpad: PengyOS server 26.11.0, LAN 10.0.23.0/24"
    assert "8282/tcp:LAN" in out and "8080/tcp:local" in out and "8096/tcp:blocked" in out
    assert "Problems (5):" in out


def test_all_shows_image_services(review: Machine):
    names = [s["name"] for s in review.json("--all")["services"]]
    assert "systemd-journald" in names


def test_missing_sources_give_empty_sections(machine: Machine):
    for t in ("ss", "pengyos-firewall"):
        (machine.root / "usr/bin" / t).unlink()
    doc = machine.json()
    assert doc["services"] == [] and doc["unowned_listeners"] == []
    assert any("ss did not run" in n for n in doc["notes"])
    assert doc["firewall_active"] is None


def test_never_writes(review: Machine):
    def tree():
        return {str(p): p.stat().st_mtime_ns for p in review.root.rglob("*")
                if p.name not in ("calls.log", "fixture.json") and not p.is_symlink()}
    before = tree()
    start = time.monotonic()
    assert review.run("status", "--json").returncode == 0
    assert time.monotonic() - start < 3
    assert tree() == before
