"""Core's first-boot provisioning is wired, and only on Core.

A Core image has no installer: pengyos-provision.service gives it SSH keys and a
hostname from the EFI partition (or credentials) and grows its root, before sshd.
Server and Desktop inherit the Core overlay, so they mask it: their installer
sticks and installed systems have their own layout, and a stick must never grow
its own root. Core's sshd runs without PAM, so it sets LANG itself (SetEnv).
"""
from __future__ import annotations

import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
CORE = ROOT / "buildroot-external" / "board" / "core" / "rootfs-overlay"
SERVER = ROOT / "buildroot-external" / "board" / "server" / "rootfs-overlay"
SCRIPT = CORE / "usr/libexec/pengyos/pengyos-provision"
UNIT = CORE / "etc/systemd/system/pengyos-provision.service"


def test_script_is_executable_sh():
    assert SCRIPT.read_text().startswith("#!/bin/sh\n")
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["sh", "-n", str(SCRIPT)], check=True)


def test_unit_runs_it_before_sshd_and_is_enabled():
    unit = UNIT.read_text()
    assert "ExecStart=/usr/libexec/pengyos/pengyos-provision" in unit
    assert "Before=sshd.service" in unit
    assert "ImportCredential=system.hostname" in unit
    wants = CORE / "etc/systemd/system/multi-user.target.wants/pengyos-provision.service"
    assert os.readlink(wants) == "../pengyos-provision.service"


def test_server_and_desktop_mask_it():
    assert os.readlink(SERVER / "etc/systemd/system/pengyos-provision.service") == "/dev/null"


def test_only_the_default_hostname_is_replaced():
    text = SCRIPT.read_text()
    assert "DEFAULT_HOST=pengyos-core" in text
    assert '[ "$have" = "$DEFAULT_HOST" ]' in text


def test_ssh_sessions_get_the_locale():
    # Core's sshd has UsePAM no, so a pam.d file there would be dead.
    core = (CORE / "etc/ssh/sshd_config").read_text()
    assert "UsePAM no" in core and "SetEnv LANG=C.UTF-8" in core
    assert not (CORE / "etc/pam.d/sshd").exists()
    assert "pam_env.so envfile=/etc/locale.conf" in (SERVER / "etc/pam.d/sshd").read_text()
