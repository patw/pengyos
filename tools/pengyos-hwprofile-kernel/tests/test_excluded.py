"""Tests for :mod:`pengyos_hwprofile_kernel.exclude`.

These lock in the "emit an excluded-drivers report — the symbols the generic/full
kernel has that *this* machine does not" contract.
The report is the set difference ``baseline − selected``: the baseline is the
union of every ``CONFIG_`` symbol set by *every* fragment in the fragments
directory (the "full" kernel), and a symbol is excluded when the machine does
not *enable* it (absent, or explicitly ``=n``).

Table-driven over the committed AMD-laptop fixture, plus a temp-fragments
"single-fragment kernel" fixture (one fragment sets every symbol the profile
selects → nothing excluded) and a temp fragments dir with an ``=n`` symbol (a
disabled symbol is excluded). No network, no root, no hardware.
"""

from __future__ import annotations

import os
import subprocess
import sys

from pengyos_hwprofile_kernel import exclude as exclude_mod
from pengyos_hwprofile_kernel import select as select_mod

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
FRAGMENTS = os.path.join(os.path.dirname(__file__), "..", "fragments")

# The bundled qemu fragment's CONFIG set — used to build a "single-fragment
# kernel" temp library where the baseline equals a single selectable fragment.
QEMU_FRAGMENT = """\
CONFIG_VIRTIO=y
CONFIG_VIRTIO_PCI=y
CONFIG_VIRTIO_NET=y
CONFIG_VIRTIO_BLK=y
CONFIG_VIRTIO_CONSOLE=y
CONFIG_VIRTIO_RNG=y
CONFIG_SERIAL_8250=y
CONFIG_SERIAL_8250_CONSOLE=y
CONFIG_DEVTMPFS=y
CONFIG_DEVTMPFS_MOUNT=y
CONFIG_E1000E=y
"""


def _fixture(name: str) -> dict:
    import tomllib

    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return tomllib.load(fh)


def _fixture_path(name: str) -> str:
    return os.path.join(FIXTURES, name)


def _write(dirpath, name: str, text: str) -> None:
    from pathlib import Path

    Path(dirpath, name).write_text(text, encoding="utf-8")


def _library_symbols(library) -> set:
    symbols = set()
    for fragment in library.values():
        symbols.update(fragment.values)
    return symbols


# ---------------------------------------------------------------------------
# baseline_symbols: the union of every fragment's symbols.
# ---------------------------------------------------------------------------


def test_baseline_symbols_covers_every_fragment_symbol():
    """The baseline is every symbol set by every fragment in the library."""
    library = select_mod.load_library(FRAGMENTS)
    base = exclude_mod.baseline_symbols(library)
    base_symbols = {symbol for symbol, _, _ in base}
    assert base_symbols == _library_symbols(library)


def test_baseline_symbols_is_sorted():
    """The baseline rows are sorted by symbol (deterministic)."""
    library = select_mod.load_library(FRAGMENTS)
    base = exclude_mod.baseline_symbols(library)
    symbols = [symbol for symbol, _, _ in base]
    assert symbols == sorted(symbols)


def test_baseline_symbols_lists_all_sources():
    """A symbol set by several fragments lists every provider (sorted)."""
    library = select_mod.load_library(FRAGMENTS)
    base = dict(
        (symbol, (value, sources))
        for symbol, value, sources in exclude_mod.baseline_symbols(library)
    )
    # CONFIG_DRM is set by amd-igpu, intel-igpu and nvidia-proprietary.
    assert base["CONFIG_DRM"][1] == (
        "amd-igpu.fragment",
        "intel-igpu.fragment",
        "nvidia-proprietary.fragment",
    )


def test_baseline_symbol_value_is_the_full_kernel_value():
    """Each baseline symbol's value is the "full" kernel's value (y here)."""
    library = select_mod.load_library(FRAGMENTS)
    base = dict(
        (symbol, (value, sources))
        for symbol, value, sources in exclude_mod.baseline_symbols(library)
    )
    assert base["CONFIG_VIRTIO_NET"] == ("y", ("qemu.fragment",))


# ---------------------------------------------------------------------------
# compute_excluded: the AMD-laptop fixture.
# ---------------------------------------------------------------------------


def test_compute_excluded_amd_laptop_counts():
    """The AMD-laptop profile: 23 carried, 17 omitted, 40 in the baseline."""
    report = exclude_mod.compute_excluded(_fixture("profile.toml"), FRAGMENTS)
    assert len(report.included) == 23
    assert len(report.excluded) == 17
    assert len(report.baseline) == 40
    assert report.ok
    assert report.exit_code == 0
    assert report.missing == ()


def test_excluded_are_the_non_amd_fragments():
    """The omitted symbols are exactly the intel/systemd/nvidia drivers."""
    report = exclude_mod.compute_excluded(_fixture("profile.toml"), FRAGMENTS)
    excluded_symbols = {e.symbol for e in report.excluded}
    # The AMD-laptop carries amd-cpu/amd-igpu/nvme/sata/qemu; it omits the
    # Intel CPU+IGPU, the NVIDIA stack and every systemd service symbol.
    assert excluded_symbols == {
        "CONFIG_CGROUPS",
        "CONFIG_CGROUPS_V2",
        "CONFIG_CPU_FREQ_GOV_INTEL_PERF",
        "CONFIG_DRM_INTEL",
        "CONFIG_DRM_INTEL_DISPLAY",
        "CONFIG_EPHEMERAL_DIRS",
        "CONFIG_EPOLL",
        "CONFIG_INET",
        "CONFIG_INOTIFY",
        "CONFIG_INOTIFY_USER",
        "CONFIG_NET",
        "CONFIG_NVIDIA",
        "CONFIG_NVIDIA_GPU_SUPPORT",
        "CONFIG_OVERLAY_FS",
        "CONFIG_SYSTEMD",
        "CONFIG_TMPFS",
        "CONFIG_X86_INTEL_PSTATE",
    }


def test_excluded_are_sorted():
    """The excluded symbols are sorted (deterministic)."""
    report = exclude_mod.compute_excluded(_fixture("profile.toml"), FRAGMENTS)
    symbols = [e.symbol for e in report.excluded]
    assert symbols == sorted(symbols)


def test_excluded_entry_carries_value_and_sources():
    """Each excluded entry records its baseline value and providing fragments."""
    report = exclude_mod.compute_excluded(_fixture("profile.toml"), FRAGMENTS)
    by_symbol = {e.symbol: e for e in report.excluded}
    assert by_symbol["CONFIG_NVIDIA"].value == "y"
    assert by_symbol["CONFIG_NVIDIA"].sources == ("nvidia-proprietary.fragment",)
    # A symbol present in the carried set is NOT excluded.
    assert "CONFIG_VIRTIO_NET" not in by_symbol
    assert "CONFIG_DRM_AMDGPU" not in by_symbol


def test_included_matches_carried_symbols():
    """The included set equals the symbols the machine enables (=y/=m)."""
    report = exclude_mod.compute_excluded(_fixture("profile.toml"), FRAGMENTS)
    assert set(report.included) == {
        "CONFIG_CPU_FREQ",
        "CONFIG_CPU_FREQ_AMDPM",
        "CONFIG_AMD_NB",
        "CONFIG_DRM",
        "CONFIG_DRM_AMD",
        "CONFIG_DRM_AMDGPU",
        "CONFIG_FB",
        "CONFIG_FB_SYS_FOPS",
        "CONFIG_NVME",
        "CONFIG_NVME_MULTIPATH",
        "CONFIG_ATA",
        "CONFIG_SATA_AHCI",
        "CONFIG_VIRTIO",
        "CONFIG_VIRTIO_PCI",
        "CONFIG_VIRTIO_NET",
        "CONFIG_VIRTIO_BLK",
        "CONFIG_VIRTIO_CONSOLE",
        "CONFIG_VIRTIO_RNG",
        "CONFIG_SERIAL_8250",
        "CONFIG_SERIAL_8250_CONSOLE",
        "CONFIG_DEVTMPFS",
        "CONFIG_DEVTMPFS_MOUNT",
        "CONFIG_E1000E",
    }


# ---------------------------------------------------------------------------
# compute_excluded: edge cases (temp fragments dirs).
# ---------------------------------------------------------------------------


def _virtio_doc() -> dict:
    """A QEMU-style profile: AMD CPU + a virtio_net NIC (selects qemu)."""
    return {
        "cpu": {"vendor": "AuthenticAMD", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.0.0"},
        "gpu": [],
        "network": [{"interface": "eth0", "mac": "aa:bb:cc:dd:ee:ff", "driver": "virtio_net"}],
        "storage": [],
    }


def _amd_cpu_doc() -> dict:
    """A bare AMD-CPU profile: selects only the amd-cpu fragment."""
    return {
        "cpu": {"vendor": "AuthenticAMD", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.0.0"},
        "gpu": [],
        "network": [],
        "storage": [],
    }


def test_single_fragment_kernel_has_no_excluded(tmp_path):
    """A profile whose selected fragments are the whole baseline omits nothing."""
    # The machine (AMD CPU + virtio NIC) selects amd-cpu + qemu; ship both so
    # the baseline is exactly what the machine carries → nothing excluded.
    _write(tmp_path, "amd-cpu.fragment", "CONFIG_CPU=y\n")
    _write(tmp_path, "qemu.fragment", QEMU_FRAGMENT)
    report = exclude_mod.compute_excluded(_virtio_doc(), tmp_path)
    # The machine selects both; both are the entire baseline → nothing excluded.
    assert report.excluded == ()
    assert set(report.included) == _library_symbols(select_mod.load_library(tmp_path))
    assert report.ok


def test_disabled_symbol_is_excluded(tmp_path):
    """A symbol the machine sets to ``=n`` is excluded (not "enabled")."""
    # The machine (AMD CPU + an NVMe storage device) selects amd-cpu + nvme.
    # Ship both: amd-cpu sets a normal "carried" symbol; nvme sets one =y
    # (carried) and one =n (disabled -> excluded).
    _write(tmp_path, "amd-cpu.fragment", "CONFIG_CPU=y\n")
    _write(tmp_path, "nvme.fragment", "CONFIG_ON=y\nCONFIG_OFF=n\n")
    doc = {
        "cpu": {"vendor": "AuthenticAMD", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.0.0"},
        "gpu": [],
        "network": [],
        "storage": [{"bus": "nvme", "model": "N", "size_bytes": 1}],
    }
    report = exclude_mod.compute_excluded(doc, tmp_path)
    # CONFIG_ON is =y (carried); CONFIG_OFF is =n (disabled -> excluded).
    assert "CONFIG_OFF" in {e.symbol for e in report.excluded}
    assert "CONFIG_CPU" not in {e.symbol for e in report.excluded}
    assert "CONFIG_ON" not in {e.symbol for e in report.excluded}
    off = {e.symbol: e for e in report.excluded}["CONFIG_OFF"]
    assert off.value == "n"
    assert off.sources == ("nvme.fragment",)


def test_compute_excluded_is_deterministic():
    """Repeated compute yields byte-identical reports."""
    first = exclude_mod.compute_excluded(_fixture("profile.toml"), FRAGMENTS)
    second = exclude_mod.compute_excluded(_fixture("profile.toml"), FRAGMENTS)
    assert first.excluded == second.excluded
    assert first.included == second.included
    assert first.baseline == second.baseline


def test_compute_excluded_missing_fragment_is_reported(tmp_path):
    """A selected fragment absent from the dir is surfaced as missing."""
    empty = tmp_path / "empty"
    empty.mkdir()
    report = exclude_mod.compute_excluded(_fixture("profile.toml"), empty)
    assert not report.ok
    assert report.exit_code == 2
    assert report.missing  # every selected fragment is missing from the empty dir


def test_compute_excluded_unknown_devices_reported():
    """Unmapped devices are reported on the report (never dropped)."""
    report = exclude_mod.compute_excluded(_fixture("profile.toml"), FRAGMENTS)
    assert len(report.unknown) == 1
    assert report.unknown[0].device["interface"] == "wlo1"


# ---------------------------------------------------------------------------
# render_excluded.
# ---------------------------------------------------------------------------


def test_render_excluded_header_and_lines():
    """The report is a header line plus one line per excluded symbol."""
    report = exclude_mod.compute_excluded(_fixture("profile.toml"), FRAGMENTS)
    text = exclude_mod.render_excluded(report)
    assert text.endswith("\n")
    lines = text.splitlines()
    # Header names the counts.
    assert lines[0].startswith("excluded drivers")
    assert "17 excluded" in lines[0]
    # One line per excluded symbol, each naming its source fragment.
    body = lines[1:]
    assert len(body) == 17
    assert all("=" in line and ".fragment)" in line for line in body)
    # The line for a known symbol carries its baseline value.
    assert any(line.startswith("CONFIG_NVIDIA=y") for line in body)


def test_render_excluded_empty_is_just_header():
    """An empty excluded set renders just the (newline-terminated) header."""
    library = select_mod.load_library(FRAGMENTS)
    symbols = sorted(_library_symbols(library))
    synthetic = exclude_mod.ExcludedReport(
        included=tuple(symbols),
        excluded=(),
        baseline=tuple(symbols),
    )
    text = exclude_mod.render_excluded(synthetic)
    assert text.count("\n") == 1  # header + trailing newline only.
    assert "0 excluded" in text


# ---------------------------------------------------------------------------
# CLI: exclude end-to-end.
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess:
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


def test_cli_exclude_writes_report_to_stdout():
    result = _run_cli(
        "exclude",
        "--profile", _fixture_path("profile.toml"),
        "--fragments", os.path.join(FRAGMENTS),
    )
    assert result.returncode == 0
    assert "17 excluded" in result.stdout
    assert "CONFIG_NVIDIA=y" in result.stdout
    # The unmapped-device warning goes to stderr, not the report.
    assert "wlo1" in result.stderr


def test_cli_exclude_writes_to_out_file(tmp_path):
    out = tmp_path / "excluded_drivers.txt"
    result = _run_cli(
        "exclude",
        "--profile", _fixture_path("profile.toml"),
        "--fragments", os.path.join(FRAGMENTS),
        "--out", str(out),
    )
    assert result.returncode == 0
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "17 excluded" in text
    assert "CONFIG_NVIDIA=y" in text


def test_cli_exclude_missing_profile_exits_1(tmp_path):
    result = _run_cli(
        "exclude",
        "--profile", str(tmp_path / "nope.toml"),
        "--fragments", os.path.join(FRAGMENTS),
    )
    assert result.returncode == 1
    assert "error" in result.stderr.lower()
    assert result.stdout == ""


def test_cli_exclude_missing_fragment_exits_2(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _run_cli(
        "exclude",
        "--profile", _fixture_path("profile.toml"),
        "--fragments", str(empty),
    )
    assert result.returncode == 2
    assert "missing fragment" in result.stderr.lower()
    assert result.stdout == ""


def test_cli_exclude_usage_error_missing_profile():
    result = _run_cli("exclude", "--fragments", os.path.join(FRAGMENTS))
    assert result.returncode == 2
    assert "--profile" in result.stderr


def test_cli_exclude_version():
    result = _run_cli("--version")
    assert result.returncode == 0
    assert "pengyos-hwprofile-kernel" in result.stdout
