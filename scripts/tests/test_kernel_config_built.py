"""Tests for check-kernel-fragments.py --built (run by the core post-build).

The check exists because the kernel quietly lacked options nobody had listed:
on 2026-10-17 a hybrid laptop's dGPU never powered off (~23 W idle), because
CONFIG_VGA_SWITCHEROO and CONFIG_HOTPLUG_PCI_PCIE had never been on, and
VGA_SWITCHEROO only survives Kconfig with FB=y. Now every fragment line and
every board/<layer>/kernel-required.txt entry must hold in the built .config.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check-kernel-fragments.py"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def ckf(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ckf", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "EXTERNAL", tmp_path)
    board = tmp_path / "board" / "core"
    board.mkdir(parents=True)
    (board / "linux.fragment").write_text(
        "# a comment\nCONFIG_VGA_SWITCHEROO=y\n# CONFIG_FOO is not set\nCONFIG_TYPEC=m\n")
    (board / "kernel-required.txt").write_text(
        "# header\nCONFIG_USB_STORAGE=y   # the live stick\nCONFIG_ACPI_BATTERY  # laptops\n")
    (tmp_path / "br.config").write_text(
        'BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES='
        '"$(BR2_EXTERNAL_PENGYOS_PATH)/board/core/linux.fragment"\n')
    return mod


def kconfig(tmp_path, lines):
    p = tmp_path / "kernel.config"
    p.write_text("\n".join(lines) + "\n")
    return p


GOOD = ["CONFIG_VGA_SWITCHEROO=y", "CONFIG_TYPEC=m", "CONFIG_USB_STORAGE=y",
        "CONFIG_ACPI_BATTERY=m"]


def test_all_present_passes(ckf, tmp_path):
    assert ckf.check_built(kconfig(tmp_path, GOOD), tmp_path / "br.config") == []


def test_fragment_line_dropped_by_kconfig_fails(ckf, tmp_path):
    cfg = kconfig(tmp_path, [l for l in GOOD if "SWITCHEROO" not in l])
    problems = ckf.check_built(cfg, tmp_path / "br.config")
    assert len(problems) == 1 and "CONFIG_VGA_SWITCHEROO=y -> unset" in problems[0]


def test_unset_fragment_line_that_got_set_fails(ckf, tmp_path):
    problems = ckf.check_built(kconfig(tmp_path, GOOD + ["CONFIG_FOO=y"]),
                               tmp_path / "br.config")
    assert len(problems) == 1 and "CONFIG_FOO=n -> y" in problems[0]


def test_required_exact_value(ckf, tmp_path):
    cfg = kconfig(tmp_path, [l.replace("USB_STORAGE=y", "USB_STORAGE=m") for l in GOOD])
    problems = ckf.check_built(cfg, tmp_path / "br.config")
    assert len(problems) == 1
    assert "CONFIG_USB_STORAGE is m (the live stick)" in problems[0]


def test_required_y_or_m(ckf, tmp_path):
    cfg = kconfig(tmp_path, [l for l in GOOD if "BATTERY" not in l])
    problems = ckf.check_built(cfg, tmp_path / "br.config")
    assert len(problems) == 1 and "CONFIG_ACPI_BATTERY is unset (laptops)" in problems[0]


def test_bad_required_line_is_an_error(ckf, tmp_path):
    (tmp_path / "board/core/kernel-required.txt").write_text("CONFIG_X=maybe\n")
    with pytest.raises(SystemExit):
        ckf.check_built(kconfig(tmp_path, GOOD), tmp_path / "br.config")


def test_repo_required_lists_parse(ckf):
    """Every real kernel-required.txt in the tree is well formed."""
    lists = sorted((REPO / "buildroot-external/board").glob("*/kernel-required.txt"))
    assert lists
    for path in lists:
        assert ckf.required_values(path)
