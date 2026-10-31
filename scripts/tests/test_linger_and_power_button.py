"""Users set their own linger (polkit on the Server), and Core has logind.

logind authorizes a non-root `loginctl enable-linger` through polkit; with no
polkit daemon it allows root only, so a user's VMs and rootless containers
died at logout. And without logind nothing acts on the power button, so Core
ignored it (`pengyos-vm stop` of a Core VM waited out its timeout).
"""
from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
LAYERS = REPO / "buildroot-external" / "configs" / "layers"
RULE = (REPO / "buildroot-external" / "board" / "server" / "rootfs-overlay"
        / "etc" / "polkit-1" / "rules.d" / "60-pengyos-linger.rules")


def symbols(layer: str) -> set[str]:
    return {l.strip() for l in (LAYERS / f"{layer}.config").read_text().splitlines()
            if l.startswith("BR2_")}


def test_logind_is_in_core():
    assert "BR2_PACKAGE_SYSTEMD_LOGIND=y" in symbols("core")


def test_polkit_is_in_the_server_layer():
    assert "BR2_PACKAGE_POLKIT=y" in symbols("server")
    # external.mk switches systemd to -Dpolkit=enabled when both are on.
    mk = (REPO / "buildroot-external" / "external.mk").read_text()
    assert "ifeq ($(BR2_PACKAGE_POLKIT)$(BR2_PACKAGE_SYSTEMD_LOGIND),yy)" in mk


def test_the_rule_allows_only_self_linger():
    text = RULE.read_text()
    assert 'action.id == "org.freedesktop.login1.set-self-linger"' in text
    assert "set-user-linger" not in text.split("polkit.addRule", 1)[1]
    assert "polkit.Result.NOT_HANDLED" in text
