"""Vendored reference-data tests.

These pin the two committed fixtures in ``pengyos_desktop_drivers/data/``:

* ``symbols.2026.08.txt`` -- every ``CONFIG_*`` symbol in the pinned 2026.08
  kernel build (the sibling Buildroot checkout).
* ``firmware.upstream.txt`` -- every upstream ``linux-firmware`` name (the
  subset oracle), derived from the pinned ``linux-firmware`` release tarball.

The tests assert the files are committed, well-formed (unique, non-empty lines,
trailing newline), and contain the symbols / prefixes the later ``emit``/``lint``
units depend on -- without needing network, root, or hardware.
"""

from __future__ import annotations

import importlib.resources as ir
import pathlib
import subprocess

import pytest

PKG_DIR = pathlib.Path(
    __import__("pengyos_desktop_drivers").__file__
).resolve().parent
DATA_DIR = PKG_DIR / "data"

SYMBOLS_FILE = DATA_DIR / "symbols.2026.08.txt"
FIRMWARE_FILE = DATA_DIR / "firmware.upstream.txt"


def _read_lines(path: pathlib.Path) -> list[str]:
    """Read a data file into a list of stripped, non-empty trailing-split lines."""
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n"), f"{path.name} must end with a trailing newline"
    lines = text.split("\n")
    # split on the trailing newline yields a final "" -- drop exactly that one.
    assert lines[-1] == ""
    return lines[:-1]


def _committed(path: pathlib.Path) -> bool:
    """True if ``path`` is tracked by git (a committed fixture, not just on disk)."""
    root = PKG_DIR.parent
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", str(path)],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


# --------------------------------------------------------------------------- #
# Existence / commit
# --------------------------------------------------------------------------- #
def test_data_directory_exists() -> None:
    assert DATA_DIR.is_dir(), "data/ dir must exist inside the package"


def test_symbol_list_committed_fixture() -> None:
    assert SYMBOLS_FILE.is_file(), "symbols.2026.08.txt must be vendored"
    assert SYMBOLS_FILE.stat().st_size > 0, "symbol list must be non-empty"
    assert _committed(SYMBOLS_FILE), "symbols.2026.08.txt must be git-tracked"


def test_firmware_list_committed_fixture() -> None:
    assert FIRMWARE_FILE.is_file(), "firmware.upstream.txt must be vendored"
    assert FIRMWARE_FILE.stat().st_size > 0, "firmware list must be non-empty"
    assert _committed(FIRMWARE_FILE), "firmware.upstream.txt must be git-tracked"


# --------------------------------------------------------------------------- #
# Well-formedness
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", [SYMBOLS_FILE, FIRMWARE_FILE])
def test_list_is_unique_and_nonempty(path: pathlib.Path) -> None:
    lines = _read_lines(path)
    assert lines, f"{path.name} has no entries"
    assert all(line for line in lines), f"{path.name} has an empty line"
    assert len(lines) == len(set(lines)), f"{path.name} has duplicate entries"


def test_symbol_list_has_expected_kernel_symbols() -> None:
    symbols = set(_read_lines(SYMBOLS_FILE))
    # The generic Desktop profile hinges on these being real 2026.08 symbols.
    for symbol in (
        "CONFIG_DRM",
        "CONFIG_DRM_KMS_HELPER",
        "CONFIG_DRM_I915",
        "CONFIG_DRM_AMDGPU",
        "CONFIG_DRM_RADEON",
        "CONFIG_IWLWIFI",
        "CONFIG_INPUT",
        "CONFIG_INPUT_EVDEV",
        "CONFIG_VIRTIO",
        "CONFIG_DRM_TTM",
    ):
        assert symbol in symbols, f"expected {symbol} in the 2026.08 symbol list"


def test_firmware_list_has_expected_prefixes() -> None:
    names = _read_lines(FIRMWARE_FILE)
    prefixes = ("i915/", "amdgpu/", "radeon/", "intel/iwlwifi/", "rtl_bt/", "rtlwifi/")
    for prefix in prefixes:
        assert any(n.startswith(prefix) for n in names), (
            f"expected an upstream name under {prefix}"
        )


def test_firmware_list_excludes_licenses_and_docs() -> None:
    names = _read_lines(FIRMWARE_FILE)
    # The oracle is loadable firmware paths, not license files or repo docs.
    assert not any(n.startswith("LICENSES/") for n in names)
    assert not any(n.split("/", 1)[0] in ("WHENCE", "README", "LICENSE") for n in names)
    assert not any(n.startswith("AGENTS.md") for n in names)


# --------------------------------------------------------------------------- #
# Loadable as package data (so later emit/lint units can read them in-place)
# --------------------------------------------------------------------------- #
def test_firmware_list_loadable_via_importlib_resources() -> None:
    text = ir.files("pengyos_desktop_drivers").joinpath("data", "firmware.upstream.txt").read_text()
    lines = [line for line in text.split("\n") if line]
    assert "i915/adlp_dmc.bin" in lines
