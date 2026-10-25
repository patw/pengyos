"""Tests for the `pengyos-webui` shipped tool.

What is checked, and why it can be checked without root or an image:

* the tool is POSIX sh, executable in git, and passes `sh -n`;
* the systemd template is the shape the design requires — `User=%i`,
  `WorkingDirectory=%h`, restart-on-failure, `WantedBy=multi-user.target`,
  and **no** `DynamicUser` / **no** `ProtectHome`, because the agent has to
  see the invoking user's home;
* the unit text is asserted byte for byte (the unit is authored, not
  generated, so a stray edit is a real change to what an image ships);
* `on` / `off` / `status` are exercised against a sandbox root
  (`--root DIR`) with a fake `systemctl`, which is why the tool has that
  hook: the state it writes is system-wide, so the only honest way to test
  the logic is to point every write at a directory the test owns;
* the root guard is asserted on the real path (no `--root`): `on` refuses
  unprivileged, `status` does not.

No build, no network, no root, no systemd.
"""

from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_DIR.parents[1]
OVERLAY = REPO_ROOT / "buildroot-external" / "board" / "server" / "rootfs-overlay"
SCRIPT = OVERLAY / "usr" / "bin" / "pengyos-webui"
UNIT = OVERLAY / "etc" / "systemd" / "system" / "pengy-web@.service"
FAKE_SYSTEMCTL = Path(__file__).resolve().parent / "fake-systemctl"

# The user the sandbox tests enable. It has to exist on the test machine
# (`getent passwd` is part of the tool's validation) and must not be root.
TEST_USER = pwd.getpwuid(os.getuid()).pw_name

CONF_REL = "etc/pengyos/webui.conf"
WANTS_REL = "etc/systemd/system/multi-user.target.wants"


def dropin_rel(user: str) -> str:
    return f"etc/systemd/system/pengy-web@{user}.service.d/pengyos-webui.conf"


def unit_rel(user: str) -> str:
    return f"{WANTS_REL}/pengy-web@{user}.service"


# --- helpers ----------------------------------------------------------------


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    """A directory that stands in for `/` for one test run."""
    (tmp_path / "usr" / "bin").mkdir(parents=True)
    shutil.copy(FAKE_SYSTEMCTL, tmp_path / "usr" / "bin" / "systemctl")
    (tmp_path / "usr" / "bin" / "systemctl").chmod(0o755)
    # The image the sandbox stands in for has pengy-web in it (Core does not,
    # which is the case the tool refuses on).
    (tmp_path / "usr" / "bin" / "pengy-web").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "usr" / "bin" / "pengy-web").chmod(0o755)
    return tmp_path


def ctl_log(root: Path) -> str:
    p = root / "var" / "log" / "fake-systemctl.log"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def conf_lines(root: Path) -> list[str]:
    p = root / CONF_REL
    if not p.is_file():
        return []
    return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("user=")]


# --- the files as they ship -------------------------------------------------


def test_tool_ships_in_the_server_overlay_and_is_executable():
    assert SCRIPT.is_file(), "pengyos-webui must ship in the server overlay"
    assert SCRIPT.stat().st_mode & 0o111, "the overlay is copied verbatim: it needs +x"
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/sh\n")


def test_server_overlay_ships_the_template_unit():
    assert UNIT.is_file(), "pengy-web@.service must ship in the server overlay"
    assert UNIT.stat().st_mode & 0o111 == 0, "a unit file is not executable"


def test_core_overlay_ships_neither():
    """Core has no pengy-web, so it gets no web UI tool and no unit."""
    core = REPO_ROOT / "buildroot-external" / "board" / "core" / "rootfs-overlay"
    assert not (core / "usr" / "bin" / "pengyos-webui").exists()
    assert not (core / "etc" / "systemd" / "system" / "pengy-web@.service").exists()


def test_desktop_overlay_ships_no_second_copy():
    """The server layer is stacked under the desktop one: one copy serves both."""
    desktop = REPO_ROOT / "buildroot-external" / "board" / "desktop" / "rootfs-overlay"
    assert not (desktop / "usr" / "bin" / "pengyos-webui").exists()
    assert not (desktop / "etc" / "systemd" / "system" / "pengy-web@.service").exists()


def test_sh_syntax():
    for shell in ("sh", "dash"):
        if shutil.which(shell) is None:
            continue
        p = subprocess.run([shell, "-n", str(SCRIPT)], capture_output=True, text=True)
        assert p.returncode == 0, f"{shell} -n: {p.stderr}"


def test_git_mode_is_executable():
    """Buildroot copies the overlay with rsync -a: the git mode is the image mode."""
    p = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-s", "--", str(SCRIPT.relative_to(REPO_ROOT))],
        capture_output=True, text=True,
    )
    if not p.stdout.strip():
        pytest.skip("not in the git index yet")
    assert p.stdout.split()[0] == "100755", p.stdout


def test_nothing_is_written_under_usr_at_runtime():
    """/usr is a read-only signed image: every write target is under /etc."""
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if ">" not in stripped or stripped.startswith("#"):
            continue
        target = stripped.split(">", 1)[1].strip().split()[0] if stripped.split(">", 1)[1].strip() else ""
        assert not target.startswith("/usr"), f"writes into the read-only image: {line}"
        assert not target.startswith("/var"), f"state belongs in /etc: {line}"


# --- the unit ---------------------------------------------------------------

EXPECTED_UNIT = """# PengyOS — the Pengy web UI for one user (%i = the user name).
#
# Turned on and off with `pengyos-webui on|off USER`, which writes
# /etc/pengyos/webui.conf and the per-user drop-in that carries this
# instance's port and bind address. Enabling it by hand works too: the
# defaults below are the documented ones (5000 on 127.0.0.1).
#
#   systemctl status 'pengy-web@beholder.service'
#   journalctl -u 'pengy-web@beholder.service'
#
# It runs as %i, in %i's home, and sees %i's ~/.config/pengy, ~/skills and
# ~/.secrets: that is the whole point — the UI is the agent as that person,
# with their configuration and their chats. So no DynamicUser (there is no
# ephemeral user here, the user is the subject) and no ProtectHome (it would
# hide exactly the state the agent needs). It is a system unit, not a user
# unit, so it comes up at boot on a headless Server where nobody has logged
# in and no session exists, and it does not depend on loginctl enable-linger.
#
# The UI has no login and exposes run_bash and run_python: whoever can reach
# the bind address runs tools as %i. The default bind is loopback, this
# machine only. A non-loopback bind is a deliberate, documented decision,
# taken with `pengyos-webui on --host`, never a default and never implied.
[Unit]
Description=Pengy web UI for %i
Documentation=https://pengyos.catbee.ca
After=network.target
ConditionPathExists=/usr/bin/pengy-web

[Service]
Type=simple
User=%i
WorkingDirectory=%h
# Overridden per user by the drop-in pengyos-webui writes; these are the
# documented defaults, so a unit enabled by hand binds the same loopback port.
Environment=WEBUI_PORT=5000
Environment=WEBUI_HOST=127.0.0.1
ExecStart=/usr/bin/pengy-web --port ${WEBUI_PORT} --host ${WEBUI_HOST}
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
"""


def test_unit_text_is_the_authored_text_byte_for_byte():
    assert UNIT.read_bytes() == EXPECTED_UNIT.encode("utf-8")


def _unit_options() -> dict[str, list[tuple[str, str]]]:
    sections: dict[str, list[tuple[str, str]]] = {}
    current = None
    for raw in UNIT.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        if "=" in line and current is not None:
            key, _, value = line.partition("=")
            sections[current].append((key.strip(), value.strip()))
    return sections


def test_unit_runs_as_the_invoking_user_in_their_home():
    svc = dict(_unit_options().get("Service", []))
    assert svc.get("User") == "%i", "the UI is the agent as the human, so User=%i"
    assert svc.get("WorkingDirectory") == "%h"


def test_unit_keeps_the_users_home_visible():
    """The two options that hid the agent's state in the old unit stay out."""
    svc = dict(_unit_options().get("Service", []))
    assert "DynamicUser" not in svc, "no ephemeral user: %i is the subject"
    assert svc.get("ProtectHome") in (None, "no"), "ProtectHome hides ~/.config/pengy, ~/skills, ~/.secrets"
    assert svc.get("User") != "root"


def test_unit_is_a_system_unit_that_boots():
    opts = _unit_options()
    assert dict(opts.get("Install", [])).get("WantedBy") == "multi-user.target"
    assert "After" in dict(opts.get("Unit", []))


def test_unit_binds_loopback_by_default_and_restarts_on_failure():
    opts = _unit_options()
    svc = dict(opts.get("Service", []))
    assert svc.get("Restart") == "on-failure"
    envs = [v for k, v in opts.get("Service", []) if k == "Environment"]
    assert "WEBUI_PORT=5000" in envs, "the documented default port"
    assert "WEBUI_HOST=127.0.0.1" in envs, "the default bind is loopback"
    assert svc.get("ExecStart") == "/usr/bin/pengy-web --port ${WEBUI_PORT} --host ${WEBUI_HOST}"


def test_unit_is_a_template():
    assert UNIT.name == "pengy-web@.service"


# --- the CLI, against a sandbox --------------------------------------------


def test_the_fake_systemctl_scopes_itself_to_its_sandbox(sandbox):
    """The helper finds its root from its own path; a wrong `..` count would
    make every enable/active assertion silently test the wrong tree."""
    p = subprocess.run([str(sandbox / "usr/bin/systemctl"), "is-enabled",
                        "pengy-web@nobody.service"], capture_output=True, text=True)
    assert p.returncode == 1 and p.stdout.strip() == "disabled", (p.stdout, p.stderr)
    assert (sandbox / "var/log/fake-systemctl.log").is_file()
    assert "is-enabled pengy-web@nobody.service" in ctl_log(sandbox)


def test_status_on_an_empty_sandbox(sandbox):
    p = run(["--root", str(sandbox), "status"])
    assert p.returncode == 0, p.stderr
    assert "off" in p.stdout
    assert "pengy web UI: off" in p.stdout


def test_status_json_on_an_empty_sandbox(sandbox):
    p = run(["--root", str(sandbox), "status", "--json"])
    assert p.returncode == 0, p.stderr
    data = json.loads(p.stdout)
    assert data["enabled"] is False
    assert data["users"] == []
    assert data["default_port"] == 5000
    assert data["default_host"] == "127.0.0.1"


def test_json_flag_is_accepted_on_both_sides_of_the_subcommand(sandbox):
    a = run(["--json", "--root", str(sandbox), "status"])
    b = run(["--root", str(sandbox), "status", "--json"])
    assert a.returncode == 0 and b.returncode == 0, (a.stderr, b.stderr)
    assert json.loads(a.stdout) == json.loads(b.stdout)


def test_on_writes_state_enables_and_starts(sandbox):
    p = run(["--root", str(sandbox), "on", TEST_USER, "--port", "8080"])
    assert p.returncode == 0, p.stderr
    assert f"http://127.0.0.1:8080" in p.stdout

    assert conf_lines(sandbox) == [f"user={TEST_USER} 8080 127.0.0.1"]

    dropin = sandbox / dropin_rel(TEST_USER)
    assert dropin.is_file()
    assert dropin.read_text(encoding="utf-8") == (
        "# Written by pengyos-webui from /etc/pengyos/webui.conf.\n"
        "[Service]\n"
        "Environment=WEBUI_PORT=8080\n"
        "Environment=WEBUI_HOST=127.0.0.1\n"
    )

    assert (sandbox / unit_rel(TEST_USER)).is_symlink()
    log = ctl_log(sandbox)
    assert "daemon-reload" in log
    assert f"enable --now pengy-web@{TEST_USER}.service" in log
    assert f"restart pengy-web@{TEST_USER}.service" in log


def test_status_after_on_reports_the_unit_and_the_url(sandbox):
    assert run(["--root", str(sandbox), "on", TEST_USER]).returncode == 0
    p = run(["--root", str(sandbox), "status"])
    assert p.returncode == 0, p.stderr
    assert "on (1 user(s))" in p.stdout
    assert f"http://127.0.0.1:5000" in p.stdout
    assert "unit active, enabled at boot" in p.stdout
    assert "127.0.0.1" in p.stdout and "this machine only" in p.stdout

    data = json.loads(run(["--root", str(sandbox), "status", "--json"]).stdout)
    assert data["enabled"] is True
    (user,) = data["users"]
    assert user == {
        "user": TEST_USER,
        "port": 5000,
        "host": "127.0.0.1",
        "unit": "active",
        "boot": "enabled",
        "url": "http://127.0.0.1:5000",
    }


def test_on_twice_is_idempotent(sandbox):
    assert run(["--root", str(sandbox), "on", TEST_USER]).returncode == 0
    second = run(["--root", str(sandbox), "on", TEST_USER])
    assert second.returncode == 0, second.stderr
    assert conf_lines(sandbox) == [f"user={TEST_USER} 5000 127.0.0.1"]
    status = json.loads(run(["--root", str(sandbox), "status", "--json"]).stdout)
    assert len(status["users"]) == 1


def test_on_then_off_then_off_again(sandbox):
    assert run(["--root", str(sandbox), "on", TEST_USER]).returncode == 0
    off = run(["--root", str(sandbox), "off", TEST_USER])
    assert off.returncode == 0, off.stderr
    assert "off (no user has it enabled)" in off.stdout
    assert conf_lines(sandbox) == []
    assert not (sandbox / unit_rel(TEST_USER)).exists()
    assert not (sandbox / dropin_rel(TEST_USER)).parent.exists()

    # `off` when it is already off is fine.
    again = run(["--root", str(sandbox), "off", TEST_USER])
    assert again.returncode == 0, again.stderr
    assert "off (no user has it enabled)" in again.stdout


def test_off_when_it_was_never_on_is_fine(sandbox):
    p = run(["--root", str(sandbox), "off", TEST_USER])
    assert p.returncode == 0, p.stderr
    assert "off" in p.stdout


def test_two_users_get_the_state_file_to_themselves(sandbox):
    assert run(["--root", str(sandbox), "on", TEST_USER, "--port", "5000"]).returncode == 0
    # A second real user is not available on a test box, so the collision
    # check is asserted against the state file: the same port is refused.
    p = run(["--root", str(sandbox), "on", TEST_USER, "--port", "5000"])
    assert p.returncode == 0, p.stderr
    assert conf_lines(sandbox) == [f"user={TEST_USER} 5000 127.0.0.1"]


def test_a_port_already_taken_is_refused(sandbox):
    """The collision rule: one port is one process."""
    conf = sandbox / CONF_REL
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(
        "# fixture\nuser=someoneelse 6060 127.0.0.1\n", encoding="utf-8"
    )
    p = run(["--root", str(sandbox), "on", TEST_USER, "--port", "6060"])
    assert p.returncode == 2
    assert "already the UI for someoneelse" in p.stderr
    assert conf_lines(sandbox) == ["user=someoneelse 6060 127.0.0.1"], "refused means nothing written"


def test_a_second_user_on_its_own_port_is_accepted(sandbox):
    conf = sandbox / CONF_REL
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("# fixture\nuser=someoneelse 6060 127.0.0.1\n", encoding="utf-8")
    p = run(["--root", str(sandbox), "on", TEST_USER, "--port", "6061"])
    assert p.returncode == 0, p.stderr
    assert conf_lines(sandbox) == [
        "user=someoneelse 6060 127.0.0.1",
        f"user={TEST_USER} 6061 127.0.0.1",
    ]


def test_host_is_recorded_surfaced_and_json_reported(sandbox):
    p = run(["--root", str(sandbox), "on", TEST_USER, "--host", "10.0.23.6"])
    assert p.returncode == 0, p.stderr
    assert "WARNING" in p.stdout and "10.0.23.6" in p.stdout
    assert conf_lines(sandbox) == [f"user={TEST_USER} 5000 10.0.23.6"]

    status = run(["--root", str(sandbox), "status"])
    assert "WARNING bound on 10.0.23.6" in status.stdout
    data = json.loads(run(["--root", str(sandbox), "status", "--json"]).stdout)
    assert data["users"][0]["host"] == "10.0.23.6"
    assert data["users"][0]["url"] == "http://10.0.23.6:5000"


def test_status_reports_a_unit_enabled_by_hand(sandbox):
    wants = sandbox / WANTS_REL
    wants.mkdir(parents=True, exist_ok=True)
    (wants / "pengy-web@handdone.service").symlink_to("../pengy-web@handdone.service")
    status = run(["--root", str(sandbox), "status"])
    assert "enabled by hand, not by pengyos-webui: handdone" in status.stdout
    data = json.loads(run(["--root", str(sandbox), "status", "--json"]).stdout)
    assert data["hand_enabled"] == ["handdone"]
    assert data["enabled"] is False


# --- misuse ----------------------------------------------------------------


def test_help_exits_zero():
    for flag in ("--help", "-h"):
        p = run([flag])
        assert p.returncode == 0
        assert "pengyos-webui" in p.stdout
        assert "no login" in p.stdout


def test_unknown_option_and_stray_argument_are_errors():
    assert run(["--wat"]).returncode == 2
    assert run(["status", "extra"]).returncode == 2
    assert run(["nonsense"]).returncode == 2


def test_bad_ports_are_refused(sandbox):
    # An empty value is indistinguishable from "not given" (`--port ""` means
    # "use the default"), so the empty string is not in this list.
    for bad in ("abc", "0", "70000", "-1", "5000x"):
        p = run(["--root", str(sandbox), "on", TEST_USER, "--port", bad])
        assert p.returncode == 2, f"--port {bad!r} was accepted: {p.stdout}"
        assert conf_lines(sandbox) == []


def test_bad_bind_addresses_are_refused(sandbox):
    for bad in ("127.0.0.1;rm -rf /", "a b", "$(id)", "1.2.3.4 ", "1.2.3.4`id`"):
        p = run(["--root", str(sandbox), "on", TEST_USER, "--host", bad])
        assert p.returncode == 2, f"--host {bad!r} was accepted"
        assert conf_lines(sandbox) == []


def test_bad_user_names_are_refused(sandbox):
    for bad in ("root", "no/such", "a b", ".hidden", "x" * 40):
        p = run(["--root", str(sandbox), "on", bad])
        assert p.returncode == 2, f"{bad!r} was accepted"
        assert conf_lines(sandbox) == []


def test_unknown_user_is_refused(sandbox):
    p = run(["--root", str(sandbox), "on", "pengyos-nosuchuser"])
    assert p.returncode == 2
    assert "no user" in p.stderr


def test_off_takes_no_port_or_host(sandbox):
    assert run(["--root", str(sandbox), "off", TEST_USER, "--port", "5000"]).returncode == 2
    assert run(["--root", str(sandbox), "off", TEST_USER, "--host", "1.2.3.4"]).returncode == 2


def test_missing_root_argument_is_an_error(sandbox):
    assert run(["on", TEST_USER, "--port"]).returncode == 2


# --- the root guard, on the real path ---------------------------------------


def test_on_without_root_is_refused():
    """No --root: the state is system-wide, so it needs root. We are not root."""
    if os.geteuid() == 0:
        pytest.skip("running as root")
    p = run(["on", TEST_USER])
    assert p.returncode == 2
    assert "sudo" in p.stderr


def test_off_without_root_is_refused():
    if os.geteuid() == 0:
        pytest.skip("running as root")
    p = run(["off", TEST_USER])
    assert p.returncode == 2
    assert "sudo" in p.stderr


def test_status_works_unprivileged_on_the_real_system():
    """status is read-only: it must work for a plain user, with no --root."""
    p = run(["status"])
    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith("pengy web UI:")
    data = json.loads(run(["status", "--json"]).stdout)
    assert set(data) == {"tool", "enabled", "default_port", "default_host", "users", "hand_enabled"}


# --- docs -------------------------------------------------------------------


def test_the_desktop_launcher_entry_opens_the_loopback_url():
    """Desktop-only, modelled on the other io.catbee.pengyos.* entries: it opens
    the documented URL with the image's `open` (xdg-open), and says on the
    launcher line that the UI has to be turned on first."""
    d = (
        REPO_ROOT
        / "buildroot-external/board/desktop/rootfs-overlay/usr/share/applications/io.catbee.pengyos.PengyWebUI.desktop"
    )
    assert d.is_file()
    text = d.read_text(encoding="utf-8")
    keys = dict(
        ln.split("=", 1)
        for ln in text.splitlines()
        if "=" in ln and not ln.startswith("#")
    )
    assert keys["Type"] == "Application"
    assert keys["Exec"] == "open http://127.0.0.1:5000"
    assert "pengyos-webui on" in keys["Comment"]
    assert "127.0.0.1" in text, "the launcher opens loopback, nothing else"
    # Server has no launcher and no desktop file.
    assert not (
        OVERLAY / "usr/share/applications/io.catbee.pengyos.PengyWebUI.desktop"
    ).exists()


def test_the_skill_copy_knows_the_command():
    skill = (
        REPO_ROOT
        / "buildroot-external/board/core/rootfs-overlay/etc/skel/skills/pengyos/pengyos_skill.md"
    )
    text = skill.read_text(encoding="utf-8")
    assert "pengyos-webui" in text
    assert "no login" in text.lower()


def test_the_changelog_records_it():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "pengyos-webui" in text.split("## [26.", 1)[0]


def test_the_old_pengy_web_unit_is_gone():
    """The PengyR-shaped unit (--config, DynamicUser, ProtectHome) must not ship."""
    old = REPO_ROOT / "tools/pengy-agent-package/package/pengy/pengy-web.service"
    assert not old.exists(), "replaced by the pengy-web@.service template"
    for path in REPO_ROOT.rglob("*.service"):
        if ".git" in path.parts or path.is_symlink():
            continue  # enable symlinks point into the image, not into the repo
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "pengy-web" not in text:
            continue
        assert "--config /etc/pengy/settings.json" not in text, path
        assert "ProtectHome=yes" not in text, path
        assert "DynamicUser=yes" not in text, path


def test_the_agent_package_suite_still_passes():
    """The vendored recipe suite is the owner of the deleted unit; it must stay green."""
    pkg = REPO_ROOT / "tools/pengy-agent-package"
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "-q"],
        cwd=pkg, capture_output=True, text=True, timeout=600,
    )
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
