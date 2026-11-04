"""Tests for board/core/rootfs-overlay/usr/bin/adduser (and addgroup/deluser/delgroup).

Up to 26.10.57 these names were BusyBox applets; from 26.11.0 they are a front end to
shadow's tools. These drive the script with fake useradd/groupadd/gpasswd/... that
record their argv, so they check the translation, not shadow.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BIN = REPO / "buildroot-external/board/core/rootfs-overlay/usr/bin"

USERS = {"alice"}
GROUPS = {"alice", "docker", "wheel"}


def run(tmp_path: Path, name: str, *args: str, uid: str = "0", tty: bool = False):
    fake = tmp_path / "fake"
    fake.mkdir(exist_ok=True)
    log = tmp_path / "log"
    for tool in ("useradd", "groupadd", "userdel", "groupdel", "gpasswd", "passwd"):
        (fake / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n')
    (fake / "id").write_text(f"#!/bin/sh\necho {uid}\n")
    users = " ".join(USERS)
    groups = " ".join(GROUPS)
    (fake / "getent").write_text(
        "#!/bin/sh\n"
        f'case "$1" in passwd) set -- "$2" {users} ;; group) set -- "$2" {groups} ;; esac\n'
        'n=$1; shift; for x in "$@"; do [ "$x" = "$n" ] && exit 0; done; exit 2\n')
    for f in fake.iterdir():
        os.chmod(f, 0o755)
    env = {**os.environ, "PATH": f"{fake}:/usr/bin:/bin"}
    p = subprocess.run([str(BIN / name), *args], env=env, capture_output=True, text=True,
                       stdin=None if tty else subprocess.DEVNULL)
    calls = log.read_text().splitlines() if log.exists() else []
    return p, calls


def test_the_four_names_are_one_script():
    for n in ("addgroup", "deluser", "delgroup"):
        assert (BIN / n).is_symlink() and os.readlink(BIN / n) == "adduser"


def test_adduser_creates_home_own_group_and_bash(tmp_path):
    p, calls = run(tmp_path, "adduser", "bob")
    assert p.returncode == 0, p.stderr
    assert calls == ["useradd -s /bin/bash -m -U bob"]
    assert "sudo passwd bob" in p.stderr  # no terminal: says how to set one


def test_adduser_debian_long_options(tmp_path):
    p, calls = run(tmp_path, "adduser", "--home", "/srv/bob", "--shell", "/bin/zsh",
                   "--uid", "1500", "--gecos", "Bob,,,", "--ingroup", "wheel",
                   "--disabled-password", "bob")
    assert p.returncode == 0, p.stderr
    assert calls == ["useradd -s /bin/zsh -d /srv/bob -m -u 1500 -c Bob,,, -g wheel -N bob"]


def test_adduser_busybox_short_options(tmp_path):
    p, calls = run(tmp_path, "adduser", "-D", "-H", "-h", "/x", "-s", "/bin/sh", "-g", "G", "bob")
    assert p.returncode == 0, p.stderr
    assert calls == ["useradd -s /bin/sh -d /x -M -c G -U bob"]


def test_adduser_system_user_has_no_login_and_no_password(tmp_path):
    p, calls = run(tmp_path, "adduser", "--system", "svc")
    assert p.returncode == 0, p.stderr
    assert calls == ["useradd -r -s /usr/sbin/nologin -M -U svc"]


def test_two_names_mean_membership(tmp_path):
    p, calls = run(tmp_path, "adduser", "alice", "docker")
    assert p.returncode == 0 and calls == ["gpasswd -a alice docker"]
    p, calls = run(tmp_path, "addgroup", "alice", "wheel")
    assert calls[-1] == "gpasswd -a alice wheel"
    p, calls = run(tmp_path, "deluser", "alice", "docker")
    assert calls[-1] == "gpasswd -d alice docker"


def test_membership_needs_both_to_exist(tmp_path):
    p, calls = run(tmp_path, "adduser", "alice", "nosuch")
    assert p.returncode == 1 and calls == []
    assert "group nosuch does not exist" in p.stderr


def test_existing_user_is_refused(tmp_path):
    p, calls = run(tmp_path, "adduser", "alice")
    assert p.returncode == 1 and calls == []


def test_addgroup_deluser_delgroup(tmp_path):
    _, calls = run(tmp_path, "addgroup", "-S", "-g", "900", "svc")
    assert calls[-1] == "groupadd -r -g 900 svc"
    _, calls = run(tmp_path, "deluser", "--remove-home", "alice")
    assert calls[-1] == "userdel -r alice"
    _, calls = run(tmp_path, "delgroup", "docker")
    assert calls[-1] == "groupdel docker"


def test_not_root_is_refused_with_the_sudo_hint(tmp_path):
    p, calls = run(tmp_path, "adduser", "bob", uid="1000")
    assert p.returncode == 1 and calls == []
    assert "sudo adduser" in p.stderr


def test_unknown_option_points_at_useradd(tmp_path):
    p, calls = run(tmp_path, "adduser", "--frobnicate", "bob")
    assert p.returncode == 1 and calls == []
    assert "useradd" in p.stderr
