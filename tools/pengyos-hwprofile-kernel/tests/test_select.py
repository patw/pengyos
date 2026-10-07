"""Tests for :mod:`pengyos_hwprofile_kernel.select`.

These lock in the "merge the selected fragments (later-wins with conflict
warnings) and emit a deterministic defconfig fragment" contract
. They drive the real
``pengyos-kernel-fragments`` later-wins grammar through :func:`compose`, plus
the ``select`` CLI end-to-end — no network, no root, no hardware.

The committed ``fragments/`` directory (bundled with this tool) is the default
library; tests that need a conflicting fragment build a small temp directory.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from pengyos_hwprofile_kernel.profiles import load_profile
from pengyos_hwprofile_kernel.select import (
    SelectReport,
    compose,
    load_library,
    render_defconfig,
    render_missing,
    render_warnings,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
FRAGMENTS = os.path.join(os.path.dirname(__file__), "..", "fragments")


def _fixture(name: str) -> dict:
    import tomllib

    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return tomllib.load(fh)


def _fixture_path(name: str) -> str:
    return os.path.join(FIXTURES, name)


# ---------------------------------------------------------------------------
# compose: map + merge the AMD-laptop fixture.
# ---------------------------------------------------------------------------


def test_compose_maps_and_merges_amd_laptop():
    """The AMD-laptop fixture composes to its five build fragments."""
    report = compose(_fixture("profile.toml"), FRAGMENTS)
    assert isinstance(report, SelectReport)
    assert report.fragments == ("amd-cpu", "amd-igpu", "nvme", "qemu", "sata")
    assert report.missing == ()
    assert report.ok
    # The driverless Wi-Fi iface is reported, never dropped.
    assert len(report.unknown) == 1
    assert report.unknown[0].device["interface"] == "wlo1"


def test_compose_merged_is_sorted_by_symbol():
    """The merged CONFIG set is sorted by symbol (deterministic)."""
    report = compose(_fixture("profile.toml"), FRAGMENTS)
    symbols = [entry.symbol for entry in report.merged]
    assert symbols == sorted(symbols)
    # Every merged entry is a well-formed CONFIG_ symbol.
    for entry in report.merged:
        assert entry.symbol.startswith("CONFIG_")


def test_compose_no_conflicts_for_amd_laptop():
    """The AMD-laptop fragments share no conflicting symbols."""
    report = compose(_fixture("profile.toml"), FRAGMENTS)
    assert report.warnings == ()


def test_render_defconfig_has_trailing_newline_and_no_duplicates():
    """The rendered defconfig is one CONFIG line per symbol, newline-terminated."""
    report = compose(_fixture("profile.toml"), FRAGMENTS)
    text = render_defconfig(report)
    assert text.endswith("\n")
    lines = text.rstrip("\n").split("\n")
    # One line per distinct symbol (no duplicates).
    symbols = [line.split("=")[0] for line in lines]
    assert len(symbols) == len(set(symbols))
    # A known symbol from a selected fragment is present.
    assert "CONFIG_VIRTIO_NET=y" in lines
    assert "CONFIG_DRM_AMDGPU=y" in lines


def test_render_defconfig_empty_is_empty_string():
    """An empty merged set renders as an empty string (no trailing newline)."""
    report = compose({}, FRAGMENTS)
    assert report.merged == ()
    assert render_defconfig(report) == ""


def test_compose_is_deterministic_across_runs():
    """Repeated compose of the same document yields byte-identical output."""
    doc = _fixture("profile.toml")
    first = render_defconfig(compose(doc, FRAGMENTS))
    second = render_defconfig(compose(doc, FRAGMENTS))
    assert first == second


def test_load_library_keys_by_fragment_name():
    """The library is keyed by ``Fragment.name`` (the sibling's convention)."""
    library = load_library(FRAGMENTS)
    # The sibling keys its library by ``Fragment.name`` (basename + suffix);
    # ``merge_fragments`` resolves a bare basename to that key tolerantly.
    assert set(library) == {
        "amd-cpu.fragment", "amd-igpu.fragment", "intel-cpu.fragment",
        "intel-igpu.fragment", "nvidia-proprietary.fragment", "nvme.fragment",
        "qemu.fragment", "sata.fragment", "systemd.fragment",
    }


# ---------------------------------------------------------------------------
# compose: later-wins conflict warnings (temp fragments dir).
# ---------------------------------------------------------------------------


def _write(dirpath, name: str, text: str) -> None:
    from pathlib import Path

    Path(dirpath, name).write_text(text, encoding="utf-8")


def test_compose_reports_later_wins_conflict(tmp_path):
    """A symbol set to different values by two fragments yields a warning."""
    # A minimal document that selects two fragments from the temp dir.
    doc = {
        "cpu": {"vendor": "AuthenticAMD", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.0.0"},
        "gpu": [{"vendor": "Advanced Micro Devices, Inc. [AMD]", "model": "X", "bus": "00:02.0", "driver": "amdgpu"}],
        "network": [],
        # An NVMe storage device selects the "nvme" fragment.
        "storage": [{"bus": "nvme", "model": "N", "size_bytes": 1}],
    }
    # Two fragments that both set CONFIG_DRM — to different values — so the
    # later one (z-late, after nvme alphabetically) wins with a warning.
    _write(tmp_path, "nvme.fragment", "CONFIG_NVME=y\nCONFIG_DRM=y\n")
    _write(tmp_path, "z-late.fragment", "CONFIG_DRM=n\n")

    # The mapping selects only "nvme"; to exercise the conflict we compose the
    # two temp fragments directly through the same later-wins path compose uses.
    from pengyos_kernel_fragments.compose import merge_fragments
    from pengyos_hwprofile_kernel.select import load_library as _load

    library = _load(tmp_path)
    report = merge_fragments(["nvme", "z-late"], library, "test")
    assert len(report.warnings) == 1
    warning = report.warnings[0]
    assert warning.symbol == "CONFIG_DRM"
    # Later-wins: z-late's =n wins over nvme's =y.
    assert warning.new_value == "n"
    final = {e.symbol: e.value for e in report.merged}
    assert final["CONFIG_DRM"] == "n"


# ---------------------------------------------------------------------------
# compose: missing fragment is a hard error.
# ---------------------------------------------------------------------------


def test_compose_missing_fragment_is_reported(tmp_path):
    """A selected fragment absent from the dir is surfaced as missing."""
    doc = {
        "cpu": {"vendor": "AuthenticAMD", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.0.0"},
        "gpu": [],
        "network": [],
        # An NVMe storage device selects the "nvme" fragment — absent here.
        "storage": [{"bus": "nvme", "model": "N", "size_bytes": 1}],
    }
    # Empty fragments dir: "nvme" is not present.
    report = compose(doc, tmp_path)
    assert report.fragments == ("amd-cpu", "nvme")
    assert report.missing == ("amd-cpu", "nvme")
    assert not report.ok
    assert report.exit_code == 2
    assert render_missing(report) is not None


# ---------------------------------------------------------------------------
# CLI: select end-to-end.
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run ``python -m pengyos_hwprofile_kernel`` with the siblings on path."""
    env = os.environ.copy()
    repo = os.path.join(os.path.dirname(__file__), "..")
    env["PYTHONPATH"] = os.pathsep.join(
        [
            os.path.join(repo, "..", "pengyos-hwprofile"),
            os.path.join(repo, "..", "pengyos-kernel-fragments"),
            env.get("PYTHONPATH", ""),
        ]
    )
    return subprocess.run(
        [sys.executable, "-m", "pengyos_hwprofile_kernel", *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_cli_select_writes_defconfig_to_stdout():
    result = _run_cli(
        "select",
        "--profile", _fixture_path("profile.toml"),
        "--fragments", os.path.join(FRAGMENTS),
    )
    assert result.returncode == 0
    # The defconfig is on stdout; the unmapped-device warning on stderr.
    assert "CONFIG_VIRTIO_NET=y" in result.stdout
    assert "wlo1" in result.stderr
    # stdout is clean CONFIG lines only (no warning noise).
    for line in result.stdout.splitlines():
        assert line.startswith("CONFIG_")


def test_cli_select_writes_to_out_file(tmp_path):
    out = tmp_path / "fragment.config"
    result = _run_cli(
        "select",
        "--profile", _fixture_path("profile.toml"),
        "--fragments", os.path.join(FRAGMENTS),
        "--out", str(out),
    )
    assert result.returncode == 0
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "CONFIG_DRM_AMDGPU=y" in text


def test_cli_select_missing_profile_exits_1(tmp_path):
    result = _run_cli(
        "select",
        "--profile", str(tmp_path / "nope.toml"),
        "--fragments", os.path.join(FRAGMENTS),
    )
    assert result.returncode == 1
    assert "error" in result.stderr.lower()
    assert result.stdout == ""


def test_cli_select_missing_fragment_exits_2(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _run_cli(
        "select",
        "--profile", _fixture_path("profile.toml"),
        "--fragments", str(empty),
    )
    assert result.returncode == 2
    assert "missing fragment" in result.stderr.lower()


def test_cli_select_usage_error_missing_profile():
    result = _run_cli("select", "--fragments", os.path.join(FRAGMENTS))
    assert result.returncode == 2
    assert "--profile" in result.stderr


def test_cli_select_version():
    result = _run_cli("--version")
    assert result.returncode == 0
    assert "pengyos-hwprofile-kernel" in result.stdout
