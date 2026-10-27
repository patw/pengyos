"""Tests for the sudo wiring (SPEC: Privilege — sudo-rs in place of the C sudo).

Why these checks exist. Buildroot's C sudo did four things for us that nothing
else in the tree states: it made /usr/bin/sudo setuid root, created the `sudo`
group the installer puts the owner in, installed /etc/pam.d/sudo, and shipped an
/etc/sudoers that grants `%sudo` and includes /etc/sudoers.d (where our `wheel`
rule lives). Our sudo-rs recipe has to do all four itself, or sudo ships and
nobody can use it — a missing setuid bit or a missing @includedir is a
"sudo: not allowed" on every installed machine, found only after an update.

1. the Server layer selects sudo-rs and not the C sudo, so every edition that has
   sudo has exactly one (both install /usr/bin/sudo);
2. the recipe is wired (Config.in sourced, .mk, .hash);
3. it keeps the four things above;
4. its CPE matches how NVD files sudo-rs CVEs, so pkg-stats can see them.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "buildroot-external"
LAYER_SERVER = EXT / "configs/layers/server.config"
CONFIG_IN = EXT / "Config.in"
RECIPE = EXT / "package/sudo-rs"
MK = RECIPE / "sudo-rs.mk"
SUDOERS = RECIPE / "sudoers"
WHEEL = EXT / "board/server/rootfs-overlay/etc/sudoers.d/wheel"

WITH_SUDO = ("pengyos_server_defconfig", "pengyos_desktop_defconfig",
             "pengyos_server_nvidia_defconfig", "pengyos_desktop_nvidia_defconfig")


def _symbols(text: str) -> set[str]:
    return set(re.findall(r"^(BR2_PACKAGE_[A-Z0-9_]+)=y", text, re.M))


def test_server_layer_selects_sudo_rs_and_not_c_sudo():
    syms = _symbols(LAYER_SERVER.read_text())
    assert "BR2_PACKAGE_SUDO_RS" in syms
    assert "BR2_PACKAGE_SUDO" not in syms


def test_every_edition_with_sudo_has_exactly_sudo_rs():
    for name in WITH_SUDO:
        syms = _symbols((EXT / "configs" / name).read_text())
        assert "BR2_PACKAGE_SUDO_RS" in syms, name
        assert "BR2_PACKAGE_SUDO" not in syms, name
    core = _symbols((EXT / "configs/pengyos_core_defconfig").read_text())
    assert not {"BR2_PACKAGE_SUDO", "BR2_PACKAGE_SUDO_RS"} & core


def test_recipe_is_wired_into_the_external_tree():
    assert 'source "$BR2_EXTERNAL_PENGYOS_PATH/package/sudo-rs/Config.in"' in CONFIG_IN.read_text()
    assert "config BR2_PACKAGE_SUDO_RS\n" in (RECIPE / "Config.in").read_text()
    hashes = (RECIPE / "sudo-rs.hash").read_text()
    version = re.search(r"^SUDO_RS_VERSION = (\S+)$", MK.read_text(), re.M).group(1)
    assert re.search(rf"^sha256  [0-9a-f]{{64}}  sudo-rs-{re.escape(version)}-cargo\d+\.tar\.gz$", hashes, re.M)
    assert "0" * 64 not in hashes


def test_sudo_is_setuid_root():
    perms = re.search(r"define SUDO_RS_PERMISSIONS\n(.*?)\nendef", MK.read_text(), re.S).group(1)
    assert re.search(r"^\s*/usr/bin/sudo f 4755 0 0 ", perms, re.M)
    assert re.search(r"^\s*/etc/sudoers f 0440 0 0 ", perms, re.M)


def test_the_sudo_group_still_exists():
    users = re.search(r"define SUDO_RS_USERS\n(.*?)\nendef", MK.read_text(), re.S).group(1)
    assert re.search(r"^\s*- - sudo -1 ", users, re.M)


def test_install_ships_sudoedit_visudo_sudoers_and_pam():
    install = re.search(r"define SUDO_RS_INSTALL_TARGET_CMDS\n(.*?)\nendef", MK.read_text(), re.S).group(1)
    for path in ("/usr/bin/sudo", "/usr/bin/visudo", "/etc/sudoers", "/etc/pam.d/sudo"):
        assert path in install, path
    assert "ln -sf sudo $(TARGET_DIR)/usr/bin/sudoedit" in install


def test_sudoers_grants_the_same_rules_the_c_sudo_did():
    lines = [l for l in SUDOERS.read_text().splitlines() if l and not l.startswith("#")]
    assert "root ALL=(ALL:ALL) ALL" in lines
    assert "%sudo ALL=(ALL:ALL) ALL" in lines
    assert "@includedir /etc/sudoers.d" in lines
    assert "%wheel ALL=(ALL:ALL) ALL" in WHEEL.read_text()


def test_cpe_matches_nvd():
    mk = MK.read_text()
    assert re.search(r"^SUDO_RS_CPE_ID_VENDOR = trifectatech$", mk, re.M)
    assert re.search(r"^SUDO_RS_CPE_ID_PRODUCT = sudo$", mk, re.M)
