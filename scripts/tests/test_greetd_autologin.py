"""Tests for the Desktop login screen's live/installed decision.

`buildroot-external/board/desktop/rootfs-overlay/usr/libexec/pengyos/greetd-config`
writes greetd's config every boot and decides whether the greeter is replaced by an
automatic session. It used to decide that from the PENGYOS-PAYLOAD partition label,
which only the *installer* stick has — so the plain live image
(pengyos-desktop-x86_64.img) stopped at the greeter and asked a person trying a trial
image for a password they had no reason to know. Found 2026-10-08 on behotv (26.10.15),
reproduced under QEMU: the plain image fails the session check, the installer stick
passes it. The fix uses the same live-boot test the Server/Core gettys already use
(usr/libexec/pengyos/agetty-autologin): an installed system boots with pengyos.data=
on the kernel line.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = (REPO / "buildroot-external/board/desktop/rootfs-overlay"
          / "usr/libexec/pengyos/greetd-config")

LIVE = "BOOT_IMAGE=/bzImage root=PARTUUID=abcd rw rootwait audit=0 splash\n"
INSTALLED = ("BOOT_IMAGE=/bzImage root=PARTUUID=abcd ro rootwait "
             "init=/usr/lib/pengyos/preinit pengyos.data=4 audit=0 splash\n")


def run(tmp_path: Path, cmdline: str, conf: str | None = None) -> tuple[str, str]:
    """Run greetd-config against a fake cmdline and return (config, stderr)."""
    (tmp_path / "cmdline").write_text(cmdline)
    env = {"PENGYOS_CMDLINE": str(tmp_path / "cmdline"),
           "PENGYOS_GREETD_DIR": str(tmp_path / "run"), "PATH": "/bin:/usr/bin"}
    if conf is not None:
        (tmp_path / "autologin.conf").write_text(conf)
        env["PENGYOS_AUTOLOGIN_CONF"] = str(tmp_path / "autologin.conf")
    p = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return (tmp_path / "run" / "config.toml").read_text(), p.stderr


def autologin_user(config: str) -> str | None:
    """The user greetd would log in automatically, or None if it shows the greeter."""
    in_initial = False
    for line in config.splitlines():
        if line.strip() == "[initial_session]":
            in_initial = True
        elif line.startswith("["):
            in_initial = False
        elif in_initial and line.startswith("user ="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def test_live_boot_logs_root_in(tmp_path):
    """A trial image must reach the desktop, not a password prompt."""
    config, _ = run(tmp_path, LIVE)
    assert autologin_user(config) == "root"


def test_installed_boot_still_asks_for_a_password(tmp_path):
    """The negative control: pengyos.data= on the kernel line means installed, and an
    installed desktop never logs root in."""
    config, _ = run(tmp_path, INSTALLED)
    assert autologin_user(config) is None, config


def test_the_payload_label_is_no_longer_the_test():
    """Regression guard on the *shape* of the decision, not just its output: "the
    installer is available" and "this boot is live" are different facts, and only the
    kernel line can tell them apart."""
    text = SCRIPT.read_text()
    assert "pengyos.data=" in text, "the live/installed test must be the kernel line"
    assert "/dev/disk/by-label" not in text, "a partition label must not gate autologin"


def test_opt_in_autologin_is_honoured_on_an_installed_system(tmp_path):
    conf = "[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=nobody\n"
    config, err = run(tmp_path, INSTALLED, conf)
    assert autologin_user(config) == "nobody", err


def test_opt_in_can_be_turned_off(tmp_path):
    conf = "[daemon]\nAutomaticLoginEnable=False\nAutomaticLogin=nobody\n"
    config, _ = run(tmp_path, INSTALLED, conf)
    assert autologin_user(config) is None


def test_root_is_never_autologged_on_an_installed_system(tmp_path):
    conf = "[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=root\n"
    config, err = run(tmp_path, INSTALLED, conf)
    assert autologin_user(config) is None, config
    assert "ignoring automatic login" in err, err


def test_a_missing_account_is_not_autologged(tmp_path):
    conf = "[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=nosuchuser-xyz\n"
    config, err = run(tmp_path, INSTALLED, conf)
    assert autologin_user(config) is None, config
    assert "no such user" in err, err


def test_the_greeter_is_always_configured(tmp_path):
    """Whatever else happens, the greeter session is present: autologin is an addition,
    not a replacement."""
    for cmdline in (LIVE, INSTALLED):
        config, _ = run(tmp_path, cmdline)
        assert "[default_session]" in config
        assert "cosmic-greeter-start" in config


@pytest.mark.skipif(not SCRIPT.is_file(), reason="overlay script missing")
def test_the_script_is_executable_in_git():
    mode = subprocess.run(["git", "-C", str(REPO), "ls-files", "-s", "--",
                           str(SCRIPT.relative_to(REPO))],
                          capture_output=True, text=True).stdout.split()
    assert mode and mode[0] == "100755", mode
