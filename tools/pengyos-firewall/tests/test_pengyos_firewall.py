"""Tests for the `pengyos-firewall` shipped tool and the ruleset it extends.

What is checked, and why it can be checked without root or an image:

* the tool is POSIX sh, executable in git, and passes `sh -n`;
* the default-deny ruleset and its unit ship in the **Core** overlay (every
  edition), not the Server one, and the ruleset includes both the image's
  edition rules and this machine's `/etc/pengyos/nftables.d/*.nft`;
* the Desktop's image rule still opens every port above 1024, and nothing
  else in the repo ships an image rule, so Core and Server stay closed;
* `allow` / `remove` / `status` are exercised against a sandbox root
  (`--root DIR`) with a fake `nft` and a fake `systemctl`: the rule file each
  command writes is asserted byte for byte, and so is the check-then-reload
  order, including the path where nft rejects the rule and the file goes;
* the root guard is asserted on the real path (no `--root`): `allow` refuses
  unprivileged, `status` does not.

That the rules really load is proven by booting images (docs/qemu-testing.md):
the Core and Server smoke matrices check that nftables.service finished.

No build, no network, no root, no nftables.
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
CORE = BOARD / "core" / "rootfs-overlay"
SCRIPT = CORE / "usr" / "bin" / "pengyos-firewall"
RULESET = CORE / "etc" / "nftables.conf"
UNIT = CORE / "etc" / "systemd" / "system" / "nftables.service"
WANTS = CORE / "etc" / "systemd" / "system" / "multi-user.target.wants" / "nftables.service"
DESKTOP_RULE = BOARD / "desktop" / "rootfs-overlay" / "usr" / "lib" / "pengyos" / "nftables.d" / "input-desktop.nft"
HERE = Path(__file__).resolve().parent

RULE_DIR_REL = "etc/pengyos/nftables.d"


def run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, timeout=60, env=full_env
    )


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    """A directory that stands in for `/`, with the firewall running."""
    bindir = tmp_path / "usr" / "bin"
    bindir.mkdir(parents=True)
    for fake, name in (("fake-systemctl", "systemctl"), ("fake-nft", "nft")):
        shutil.copy(HERE / fake, bindir / name)
        (bindir / name).chmod(0o755)
    (tmp_path / "etc").mkdir()
    shutil.copy(RULESET, tmp_path / "etc" / "nftables.conf")
    active = tmp_path / "var" / "lib" / "fake-systemd" / "active"
    active.mkdir(parents=True)
    (active / "nftables.service").touch()
    return tmp_path


def sb(root: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return run([*args, "--root", str(root)], env=env)


def log(root: Path, name: str) -> str:
    p = root / "var" / "log" / name
    return p.read_text() if p.exists() else ""


def rules(root: Path) -> dict[str, str]:
    d = root / RULE_DIR_REL
    if not d.is_dir():
        return {}
    return {p.name: p.read_text() for p in sorted(d.glob("*.nft"))}


# --- what ships --------------------------------------------------------------


def test_script_is_executable_posix_sh():
    assert SCRIPT.read_text().startswith("#!/bin/sh\n")
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["sh", "-n", str(SCRIPT)], check=True)
    mode = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-s", str(SCRIPT.relative_to(REPO_ROOT))],
        capture_output=True, text=True, check=True,
    ).stdout
    assert mode.startswith("100755"), mode


def test_ruleset_and_unit_ship_in_core_not_server():
    assert RULESET.is_file() and UNIT.is_file()
    assert WANTS.is_symlink() and os.readlink(WANTS) == "../nftables.service"
    server = BOARD / "server" / "rootfs-overlay"
    assert not (server / "etc" / "nftables.conf").exists()
    assert not (server / "etc" / "systemd" / "system" / "nftables.service").exists()
    assert "ExecStart=/usr/sbin/nft -f /etc/nftables.conf" in UNIT.read_text()


def test_ruleset_is_default_deny_and_includes_both_rule_dirs():
    text = RULESET.read_text()
    assert "type filter hook input priority 0; policy drop;" in text
    assert "tcp dport 22 ct state new accept" in text
    assert 'include "/usr/lib/pengyos/nftables.d/input-*.nft"' in text
    assert 'include "/etc/pengyos/nftables.d/*.nft"' in text
    # Both includes sit inside the input chain, before it closes.
    chain = text.split("chain input {", 1)[1].split("\n    }\n", 1)[0]
    assert 'include "/etc/pengyos/nftables.d/*.nft"' in chain
    assert 'include "/usr/lib/pengyos/nftables.d/input-*.nft"' in chain


def test_only_the_desktop_opens_ports_above_1024():
    statements = [
        ln.strip() for ln in DESKTOP_RULE.read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert statements == [
        "tcp dport 1025-65535 ct state new accept",
        "udp dport 1025-65535 accept",
    ]
    shipped = sorted(p.relative_to(BOARD) for p in BOARD.glob("*/rootfs-overlay/usr/lib/pengyos/nftables.d/*.nft"))
    assert shipped == [DESKTOP_RULE.relative_to(BOARD)]


def test_rule_dir_ships_with_a_readme_the_include_ignores():
    d = CORE / RULE_DIR_REL
    assert (d / "README").is_file()
    assert not list(d.glob("*.nft"))


# --- allow -------------------------------------------------------------------


def test_allow_tcp_port_writes_one_rule_and_reloads(sandbox):
    r = sb(sandbox, "allow", "8086")
    assert r.returncode == 0, r.stderr
    assert rules(sandbox) == {
        "allow-tcp-8086.nft": "# pengyos-firewall: 8086/tcp\n"
        "# Written by pengyos-firewall allow; 'remove' with the same arguments deletes it.\n"
        "tcp dport 8086 ct state new accept\n"
    }
    assert "8086/tcp open (allow-tcp-8086.nft)" in r.stdout
    assert "open to every address" in r.stdout
    # The ruleset was checked with the new file in place, then reloaded.
    assert f"nft -c -f {sandbox}/etc/nftables.conf" in log(sandbox, "fake-nft.log")
    assert "included: allow-tcp-8086.nft" in log(sandbox, "fake-nft.log")
    assert "systemctl reload nftables.service" in log(sandbox, "fake-systemctl.log")


def test_allow_from_ipv4_network(sandbox):
    r = sb(sandbox, "allow", "8086", "--from", "10.0.23.0/24")
    assert r.returncode == 0, r.stderr
    assert rules(sandbox)["allow-tcp-8086-from-10.0.23.0_24.nft"].splitlines()[-1] == (
        "tcp dport 8086 ip saddr 10.0.23.0/24 ct state new accept"
    )
    assert "open to every address" not in r.stdout


def test_allow_from_ipv6_uses_ip6(sandbox):
    r = sb(sandbox, "allow", "22000/udp", "--from=fd00::/8")
    assert r.returncode == 0, r.stderr
    assert rules(sandbox)["allow-udp-22000-from-fd00___8.nft"].splitlines()[-1] == (
        "udp dport 22000 ip6 saddr fd00::/8 accept"
    )


def test_allow_udp_range(sandbox):
    r = sb(sandbox, "allow", "6000-6010/udp")
    assert r.returncode == 0, r.stderr
    assert rules(sandbox)["allow-udp-6000-6010.nft"].splitlines()[-1] == "udp dport 6000-6010 accept"


def test_allow_twice_is_one_rule(sandbox):
    assert sb(sandbox, "allow", "8086").returncode == 0
    assert sb(sandbox, "allow", "8086").returncode == 0
    assert list(rules(sandbox)) == ["allow-tcp-8086.nft"]


def test_allow_while_firewall_stopped_writes_but_does_not_reload(sandbox):
    (sandbox / "var" / "lib" / "fake-systemd" / "active" / "nftables.service").unlink()
    r = sb(sandbox, "allow", "8086")
    assert r.returncode == 0, r.stderr
    assert "not running" in r.stdout
    assert "allow-tcp-8086.nft" in rules(sandbox)
    assert "reload" not in log(sandbox, "fake-systemctl.log")


def test_rule_nft_rejects_is_removed_and_nothing_reloads(sandbox):
    r = sb(sandbox, "allow", "8086", env={"FAKE_NFT_REJECT": "1"})
    assert r.returncode == 2
    assert "nft rejected the rule; nothing was changed" in r.stderr
    assert "Error: syntax error" in r.stderr
    assert rules(sandbox) == {}
    assert "reload" not in log(sandbox, "fake-systemctl.log")


def test_reload_failure_is_an_error(sandbox):
    r = sb(sandbox, "allow", "8086", env={"FAKE_SYSTEMCTL_RELOAD_FAIL": "1"})
    assert r.returncode == 2
    assert "reloading nftables.service failed" in r.stderr


@pytest.mark.parametrize(
    "args, message",
    [
        (["allow"], "allow what?"),
        (["allow", "0"], "not a port: '0'"),
        (["allow", "70000"], "not a port: '70000'"),
        (["allow", "http"], "not a port: 'http'"),
        (["allow", "10-5"], "not a port range: '10-5'"),
        (["allow", "8086/sctp"], "not a protocol: 'sctp'"),
        (["allow", "8086", "--from", "10.0.23.0/24; accept"], "not an address or network"),
        (["allow", "8086", "--from", "evil.example"], "not an address or network"),
        (["allow", "8086", "--from", "/24"], "not an address or network"),
        (["allow", "1", "2"], "too many arguments"),
        (["open", "8086"], "no such command: 'open'"),
        (["status", "8086"], "status takes no port"),
        (["allow", "8086", "--bogus"], "no such option: --bogus"),
    ],
)
def test_misuse_exits_2_and_writes_nothing(sandbox, args, message):
    r = sb(sandbox, *args)
    assert r.returncode == 2
    assert message in r.stderr
    assert rules(sandbox) == {}


# --- remove ------------------------------------------------------------------


def test_remove_deletes_the_matching_rule_and_reloads(sandbox):
    assert sb(sandbox, "allow", "8086", "--from", "10.0.23.0/24").returncode == 0
    assert sb(sandbox, "allow", "9000").returncode == 0
    r = sb(sandbox, "remove", "8086", "--from", "10.0.23.0/24")
    assert r.returncode == 0, r.stderr
    assert "8086/tcp from 10.0.23.0/24 closed" in r.stdout
    assert list(rules(sandbox)) == ["allow-tcp-9000.nft"]
    assert log(sandbox, "fake-systemctl.log").count("systemctl reload nftables.service") == 3


def test_remove_something_not_opened_is_a_no_op(sandbox):
    r = sb(sandbox, "remove", "8086")
    assert r.returncode == 0
    assert "nothing to do" in r.stdout
    assert "reload" not in log(sandbox, "fake-systemctl.log")


# --- status ------------------------------------------------------------------


def test_status_when_nothing_is_opened(sandbox):
    r = sb(sandbox, "status")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == "firewall: on (default-deny inbound; nftables.service active)"
    assert "opened on this machine: nothing" in r.stdout
    assert "image:" not in r.stdout


def test_status_lists_image_rules_and_opened_ports(sandbox):
    image = sandbox / "usr" / "lib" / "pengyos" / "nftables.d"
    image.mkdir(parents=True)
    shutil.copy(DESKTOP_RULE, image / "input-desktop.nft")
    assert sb(sandbox, "allow", "8086", "--from", "10.0.23.0/24").returncode == 0
    (sandbox / RULE_DIR_REL / "caddy.nft").write_text("tcp dport { 80, 443 } ct state new accept\n")

    r = sb(sandbox)
    assert r.returncode == 0, r.stderr
    assert "image: every port above 1024 (Desktop, input-desktop.nft)" in r.stdout
    assert "    8086/tcp from 10.0.23.0/24" in r.stdout
    assert "    (by hand) caddy.nft" in r.stdout

    j = json.loads(sb(sandbox, "status", "--json").stdout)
    assert j["active"] == "active"
    assert j["image_rules"] == ["input-desktop.nft"]
    assert j["opened"] == [
        {"file": "allow-tcp-8086-from-10.0.23.0_24.nft", "opens": "8086/tcp from 10.0.23.0/24"},
        {"file": "caddy.nft", "opens": "(by hand) caddy.nft"},
    ]


def test_status_says_so_when_the_firewall_is_not_running(sandbox):
    (sandbox / "var" / "lib" / "fake-systemd" / "active" / "nftables.service").unlink()
    r = sb(sandbox, "status")
    assert r.returncode == 0
    assert "NOT filtering" in r.stdout


# --- the real path -----------------------------------------------------------


@pytest.mark.skipif(os.getuid() == 0, reason="asserts the unprivileged refusal")
def test_allow_refuses_without_root():
    r = run(["allow", "8086"])
    assert r.returncode == 2
    assert "run it with sudo" in r.stderr


def test_help():
    r = run(["--help"])
    assert r.returncode == 0
    assert "sudo pengyos-firewall allow PORT" in r.stdout
