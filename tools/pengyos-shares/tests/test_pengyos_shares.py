"""Tests for the `pengyos-shares` shipped tool.

The shipped script is run as a program against a sandbox root (`--root DIR`):
a fake `systemctl` that keeps unit state in files, and the real
`pengyos-firewall` (Core overlay) with its own fake `nft`, so the firewall rule
files a share opens are the real ones. Asserted: the ksmbd.conf and
ksmbdpwd.db written, byte for byte where it matters; the service enabled with
the first share and disabled with the last; port 445 opened to LAN ranges only
and closed again; and every misuse refused with nothing written.

That ksmbd really serves the shares is proven by booting the Server image
(spec.md, "Verification").

No build, no network, no root, no ksmbd.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_DIR.parents[1]
BOARD = REPO_ROOT / "buildroot-external" / "board"
SCRIPT = BOARD / "server" / "rootfs-overlay" / "usr" / "bin" / "pengyos-shares"
FIREWALL = BOARD / "core" / "rootfs-overlay" / "usr" / "bin" / "pengyos-firewall"
RULESET = BOARD / "core" / "rootfs-overlay" / "etc" / "nftables.conf"
FAKE_NFT = REPO_ROOT / "tools" / "pengyos-firewall" / "tests" / "fake-nft"
HERE = Path(__file__).resolve().parent
LAN = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fd00::/8", "fe80::/10")


def rule_file(cidr: str) -> str:
    """pengyos-firewall's file name for `allow 445/tcp --from CIDR`."""
    return "allow-tcp-445-from-" + cidr.replace("/", "_").replace(":", "_") + ".nft"


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """A directory standing in for `/`: two accounts, a data folder, the firewall up."""
    bindir = tmp_path / "usr" / "bin"
    bindir.mkdir(parents=True)
    for src, name in ((HERE / "fake-systemctl", "systemctl"), (FAKE_NFT, "nft"),
                      (FIREWALL, "pengyos-firewall")):
        shutil.copy(src, bindir / name)
        (bindir / name).chmod(0o755)
    etc = tmp_path / "etc"
    etc.mkdir()
    shutil.copy(RULESET, etc / "nftables.conf")
    (etc / "hostname").write_text("lidserver\n")
    (etc / "passwd").write_text(
        "root:x:0:0:root:/root:/bin/sh\n"
        "sshd:x:101:101::/var/empty:/bin/false\n"
        "pat:x:1000:1000:Pat:/home/pat:/bin/zsh\n"
        "sam:x:1001:1001::/home/sam:/bin/zsh\n")
    (tmp_path / "data" / "media").mkdir(parents=True)
    (tmp_path / "home" / "pat" / "Music").mkdir(parents=True)
    active = tmp_path / "var" / "lib" / "fake-systemd" / "active"
    active.mkdir(parents=True)
    (active / "nftables.service").touch()
    return tmp_path


def sh(root: Path, *args: str, stdin: str | None = None,
       env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k != "SUDO_USER"}
    e.update(env or {})
    return subprocess.run([str(SCRIPT), *args, "--root", str(root)], input=stdin,
                          capture_output=True, text=True, timeout=60, env=e)


def conf(root: Path) -> str:
    return (root / "etc" / "ksmbd" / "ksmbd.conf").read_text()


def rules(root: Path) -> list[str]:
    d = root / "etc" / "pengyos" / "nftables.d"
    return sorted(p.name for p in d.glob("*.nft")) if d.is_dir() else []


def unit(root: Path, state: str) -> bool:
    return (root / "var" / "lib" / "fake-systemd" / state / "ksmbd.service").exists()


def syslog(root: Path) -> str:
    p = root / "var" / "log" / "fake-systemctl.log"
    return p.read_text() if p.exists() else ""


# --- what ships --------------------------------------------------------------


def test_script_ships_executable_in_the_server_overlay():
    assert SCRIPT.read_text().startswith("#!/usr/bin/env python3\n")
    mode = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-s", str(SCRIPT.relative_to(REPO_ROOT))],
        capture_output=True, text=True, check=True).stdout
    assert mode.startswith("100755"), mode


def test_ksmbd_is_preset_off():
    preset = (BOARD / "server" / "rootfs-overlay" / "usr" / "lib" / "systemd" / "system-preset"
              / "10-pengyos-server.preset").read_text().splitlines()
    for u in ("ksmbd.service", "smb.service", "nmb.service", "winbind.service", "samba.service"):
        assert f"disable {u}" in preset, u


def test_nt_hash_matches_the_known_vector():
    """The classic NT hash of "password", as ksmbd.adduser would store it."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("pengyos_shares", str(SCRIPT))
    spec = importlib.util.spec_from_loader("pengyos_shares", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    assert base64.b64decode(mod.nt_hash("password")).hex() == "8846f7eaee8fb117ad06bdd830b7586c"
    assert mod._md4(b"a" * 100).hex() == "a2a3c7c3ee6088bad252bfbbae229bb6"  # two blocks


# --- the first share turns sharing on; the last turns it off -----------------


def test_add_writes_the_share_opens_lan_and_starts_ksmbd(root):
    r = sh(root, "add", "media", "/data/media", "--user", "pat")
    assert r.returncode == 0, r.stderr
    assert conf(root) == (
        "# Managed by pengyos-shares.\n"
        "# `pengyos-shares add/remove` rewrite this file. A share's extra keys added\n"
        "# by hand are kept; [global] is the tool's. See ksmbd.conf(5).\n"
        "[global]\n"
        "\tnetbios name = LIDSERVER\n"
        "\tserver string = PengyOS (lidserver)\n"
        "\tworkgroup = WORKGROUP\n"
        "\tmap to guest = never\n"
        "\trestrict anonymous = 2\n"
        "\tserver signing = auto\n"
        "\n"
        "[media]\n"
        "\tpath = /data/media\n"
        "\tread only = no\n"
        "\tvalid users = pat\n"
        "\tguest ok = no\n"
        "\tbrowseable = yes\n")
    assert unit(root, "active") and unit(root, "enabled")
    assert "systemctl enable --now ksmbd.service" in syslog(root)
    assert rules(root) == sorted(rule_file(c) for c in LAN)
    for c in LAN:
        text = (root / "etc/pengyos/nftables.d" / rule_file(c)).read_text()
        assert "tcp dport 445" in text and c in text
    assert "\\\\lidserver\\media" in r.stdout and "smb://lidserver/media" in r.stdout
    assert "pat has no share password yet" in r.stdout


def test_a_second_share_reloads_instead_of_restarting(root):
    sh(root, "add", "media", "/data/media", "--user", "pat")
    r = sh(root, "add", "music", "/home/pat/Music", "--read-only", "--user", "pat",
           "--user", "sam", "--comment", "Pat's records")
    assert r.returncode == 0, r.stderr
    assert syslog(root).count("enable --now") == 1
    assert "systemctl reload ksmbd.service" in syslog(root)
    text = conf(root)
    assert ("[music]\n\tcomment = Pat's records\n\tpath = /home/pat/Music\n"
            "\tread only = yes\n\tvalid users = pat sam\n") in text
    assert len(rules(root)) == len(LAN)  # allow twice is one rule


def test_remove_last_share_stops_ksmbd_and_closes_the_port(root):
    sh(root, "add", "media", "/data/media", "--user", "pat")
    sh(root, "add", "music", "/home/pat/Music", "--user", "pat")
    assert sh(root, "remove", "media").returncode == 0
    assert unit(root, "active") and len(rules(root)) == len(LAN)
    r = sh(root, "remove", "music")
    assert r.returncode == 0, r.stderr
    assert not unit(root, "active") and not unit(root, "enabled")
    assert rules(root) == []
    assert "[music]" not in conf(root) and "[global]" in conf(root)
    assert (root / "home" / "pat" / "Music").is_dir()  # the folder is untouched
    assert sh(root, "remove", "music").returncode == 0  # no-op


def test_hand_added_keys_survive_a_rewrite(root):
    sh(root, "add", "media", "/data/media", "--user", "pat")
    p = root / "etc" / "ksmbd" / "ksmbd.conf"
    p.write_text(p.read_text().replace("\tbrowseable = yes\n", "\tbrowseable = yes\n\tveto files = /.DS_Store/\n"))
    sh(root, "add", "media", "/data/media", "--read-only", "--user", "sam")
    text = conf(root)
    assert "\tveto files = /.DS_Store/\n" in text
    assert "\tread only = yes\n\tvalid users = sam\n" in text


def test_sudo_user_is_the_default_user(root):
    r = sh(root, "add", "media", "/data/media", env={"SUDO_USER": "sam"})
    assert r.returncode == 0, r.stderr
    assert "\tvalid users = sam\n" in conf(root)


# --- share passwords -----------------------------------------------------------


def test_passwd_writes_the_nt_hash_0600_and_reloads(root):
    sh(root, "add", "media", "/data/media", "--user", "pat")
    r = sh(root, "passwd", "pat", "--stdin", stdin="password\n")
    assert r.returncode == 0, r.stderr
    db = root / "etc" / "ksmbd" / "ksmbdpwd.db"
    assert db.read_text() == "pat:iEb36u6PsRetBr3YMLdYbA==\n"
    assert db.stat().st_mode & 0o777 == 0o600
    assert syslog(root).rstrip().endswith("systemctl reload ksmbd.service")
    sh(root, "passwd", "sam", "--stdin", stdin="another one\n")
    sh(root, "passwd", "pat", "--stdin", stdin="changed it\n")
    lines = db.read_text().splitlines()
    assert [ln.split(":")[0] for ln in lines] == ["pat", "sam"]
    assert lines[0] != "pat:iEb36u6PsRetBr3YMLdYbA=="
    assert sh(root, "deluser", "pat").returncode == 0
    assert db.read_text().startswith("sam:")


def test_passwd_without_ksmbd_running_does_not_start_it(root):
    assert sh(root, "passwd", "pat", "--stdin", stdin="password\n").returncode == 0
    assert not unit(root, "active")
    assert "reload" not in syslog(root)


@pytest.mark.parametrize("who,pw,msg", [
    ("pat", "short\n", "at least 8"),
    ("root", "password\n", "not an account"),
    ("sshd", "password\n", "not an account"),
    ("nobody", "password\n", "not an account"),
])
def test_passwd_refusals(root, who, pw, msg):
    r = sh(root, "passwd", who, "--stdin", stdin=pw)
    assert r.returncode == 2 and msg in r.stderr
    assert not (root / "etc" / "ksmbd" / "ksmbdpwd.db").exists()


# --- refusals: exit 2, nothing written -----------------------------------------


@pytest.mark.parametrize("args,msg", [
    (["add", "global", "/data/media", "--user", "pat"], "bad share name"),
    (["add", "IPC$", "/data/media", "--user", "pat"], "bad share name"),
    (["add", "-x", "/data/media", "--user", "pat"], ""),
    (["add", "a b", "/data/media", "--user", "pat"], "bad share name"),
    (["add", "media", "data/media", "--user", "pat"], "full path"),
    (["add", "media", "/", "--user", "pat"], "part of the system"),
    (["add", "media", "/etc", "--user", "pat"], "part of the system"),
    (["add", "media", "/usr/share", "--user", "pat"], "part of the system"),
    (["add", "media", "/data/nope", "--user", "pat"], "not a folder"),
    (["add", "media", "/data/media"], "--user"),
    (["add", "media", "/data/media", "--user", "root"], "not an account"),
])
def test_add_refusals(root, args, msg):
    r = sh(root, *args)
    assert r.returncode == 2, r.stdout
    assert msg in r.stderr
    assert not (root / "etc" / "ksmbd").exists()
    assert rules(root) == [] and not unit(root, "active")


def test_names_differing_only_in_case_clash(root):
    sh(root, "add", "Media", "/data/media", "--user", "pat")
    r = sh(root, "add", "media", "/data/media", "--user", "pat")
    assert r.returncode == 2 and "already a share called Media" in r.stderr


def test_a_hand_written_conf_is_not_overwritten(root):
    p = root / "etc" / "ksmbd" / "ksmbd.conf"
    p.parent.mkdir(parents=True)
    p.write_text("[global]\n\tworkgroup = HOME\n[old]\n\tpath = /data\n")
    r = sh(root, "add", "media", "/data/media", "--user", "pat")
    assert r.returncode == 2 and "not written by pengyos-shares" in r.stderr
    assert "[old]" in p.read_text()


def test_changes_need_root_status_does_not(root):
    if os.geteuid() == 0:
        pytest.skip("running as root")
    r = subprocess.run([str(SCRIPT), "add", "media", "/data/media"], capture_output=True, text=True)
    assert r.returncode == 2 and "sudo" in r.stderr
    r = subprocess.run([str(SCRIPT), "status", "--json"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --- status --------------------------------------------------------------------


def test_status_json_and_text(root):
    sh(root, "add", "media", "/data/media", "--user", "pat", "--user", "sam")
    sh(root, "passwd", "pat", "--stdin", stdin="password\n")
    s = json.loads(sh(root, "status", "--json").stdout)
    assert s["active"] and s["enabled"] and s["host"] == "lidserver"
    assert s["shares"] == [{"name": "media", "path": "/data/media", "read_only": False,
                            "users": ["pat", "sam"], "comment": ""}]
    assert s["users"] == ["pat"]
    text = sh(root, "status").stdout
    assert "SMB sharing: on" in text
    assert "sam has no share password: sudo pengyos-shares passwd sam" in text
    sh(root, "remove", "media")
    assert "No shares." in sh(root, "status").stdout
