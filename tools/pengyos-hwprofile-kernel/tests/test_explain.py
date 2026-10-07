"""Tests for :mod:`pengyos_hwprofile_kernel.explain`.

These lock in the "explain which device pulled in a CONFIG_ symbol" contract
. Given a validated hwprofile document,
a fragments directory and a symbol, :func:`explain` traces the symbol backwards
along the profile -> mapper -> fragments -> merge flow: the selected fragments
that set the symbol, and the device(s) that pulled each carrying fragment into
the machine's image, plus the symbol's later-wins final value.

Table-driven over the committed AMD-laptop fixture, plus a temp-fragments
"two-fragments-share-a-symbol" fixture (later-wins final value) and a
monkeypatched mapping (a device selecting a custom fragment). No network, no
root, no hardware.
"""

from __future__ import annotations

import os
import subprocess
import sys

import tomllib

from pengyos_hwprofile_kernel import explain as explain_mod
from pengyos_hwprofile_kernel import mapper

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
FRAGMENTS = os.path.join(os.path.dirname(__file__), "..", "fragments")


def _fixture(name: str) -> dict:
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return tomllib.load(fh)


def _fixture_path(name: str) -> str:
    return os.path.join(FIXTURES, name)


def _write(dirpath, name: str, text: str) -> None:
    from pathlib import Path

    Path(dirpath, name).write_text(text, encoding="utf-8")


def _amd_laptop() -> dict:
    """The committed AMD-laptop fixture (AMD CPU + amdgpu GPU + e1000e NIC)."""
    return _fixture("profile.toml")


# ---------------------------------------------------------------------------
# explain: the committed AMD-laptop fixture.
# ---------------------------------------------------------------------------


def test_explain_traces_symbol_to_device():
    """CONFIG_VIRTIO_NET is set by qemu, pulled in by the e1000e NIC."""
    report = explain_mod.explain(_amd_laptop(), FRAGMENTS, "CONFIG_VIRTIO_NET")
    assert report.ok  # a clean answer, always ok
    assert report.found
    assert report.final_value == "y"
    assert report.final_source == "qemu"
    # Exactly one selected fragment sets it.
    assert len(report.fragments) == 1
    sym = report.fragments[0]
    assert sym.fragment == "qemu"
    assert sym.value == "y"
    assert sym.line_no == 5  # CONFIG_VIRTIO_NET is the 5th line of qemu.fragment
    # The e1000e NIC (enp3s0) is the device that pulled qemu in.
    assert len(sym.devices) == 1
    device = sym.devices[0]
    assert device.section == "network"
    assert device.fragment == "qemu"
    assert device.label == "enp3s0"


def test_explain_cpu_symbol_pulled_in_by_cpu_device():
    """CONFIG_CPU_FREQ_AMDPM is set by amd-cpu, pulled in by the CPU device."""
    report = explain_mod.explain(_amd_laptop(), FRAGMENTS, "CONFIG_CPU_FREQ_AMDPM")
    assert report.found
    sym = report.fragments[0]
    assert sym.fragment == "amd-cpu"
    assert len(sym.devices) == 1
    device = sym.devices[0]
    assert device.section == "cpu"
    # The CPU's model is the identifying label.
    assert "Ryzen 7 5700G" in device.label


def test_explain_gpu_symbol_pulled_in_by_gpu_device():
    """CONFIG_DRM_AMDGPU is set by amd-igpu, pulled in by the amdgpu GPU."""
    report = explain_mod.explain(_amd_laptop(), FRAGMENTS, "CONFIG_DRM_AMDGPU")
    assert report.found
    sym = report.fragments[0]
    assert sym.fragment == "amd-igpu"
    assert len(sym.devices) == 1
    device = sym.devices[0]
    assert device.section == "gpu"
    # The GPU's model is the identifying label.
    assert device.label == "Vega [Radeon RX Vega]"


def test_explain_not_selected_is_clean_not_error():
    """A symbol no selected fragment sets is a clean (not-found) answer."""
    report = explain_mod.explain(_amd_laptop(), FRAGMENTS, "CONFIG_NVIDIA")
    assert report.ok
    assert not report.found
    assert report.fragments == ()
    assert report.final_value is None
    assert report.final_source is None


def test_explain_reports_unknown_devices():
    """The driverless Wi-Fi iface is still reported as unknown."""
    report = explain_mod.explain(_amd_laptop(), FRAGMENTS, "CONFIG_VIRTIO_NET")
    assert len(report.unknown) == 1
    assert report.unknown[0].device["interface"] == "wlo1"


def test_explain_multiple_devices_pull_same_fragment():
    """Two devices selecting the same fragment both appear as pullers."""
    doc = {
        "cpu": {"vendor": "AuthenticAMD", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.1.0"},
        "gpu": [],
        "network": [
            {"interface": "enp0s3", "mac": "aa:..", "driver": "e1000e"},
            {"interface": "enp0s8", "mac": "bb:..", "driver": "e1000e"},
        ],
        "storage": [],
    }
    report = explain_mod.explain(doc, FRAGMENTS, "CONFIG_E1000E")
    assert report.found
    sym = report.fragments[0]
    assert sym.fragment == "qemu"
    # Both e1000e NICs pulled qemu in.
    assert {d.label for d in sym.devices} == {"enp0s3", "enp0s8"}
    assert {d.section for d in sym.devices} == {"network"}


def test_explain_empty_profile_symbol_not_found():
    """With no selected fragments, no symbol is pulled in."""
    report = explain_mod.explain({}, FRAGMENTS, "CONFIG_VIRTIO_NET")
    assert not report.found
    assert report.fragments == ()


# ---------------------------------------------------------------------------
# explain: later-wins final value (two fragments share a symbol).
# ---------------------------------------------------------------------------


def test_explain_final_value_uses_later_wins(tmp_path, monkeypatch):
    """The later fragment (sorted-by-name) supplies the final value."""
    # Two custom fragments both set CONFIG_DUP, to different values.
    _write(tmp_path, "dup-a.fragment", "CONFIG_DUP=y\n")
    _write(tmp_path, "dup-b.fragment", "CONFIG_DUP=n\n")
    # Add mapping rules so two devices select both fragments.
    rules = (
        *mapper.MAPPING,
        mapper.Rule("network", "driver", "dupa", "dup-a"),
        mapper.Rule("network", "driver", "dupb", "dup-b"),
    )
    monkeypatch.setattr(mapper, "MAPPING", rules)
    doc = {
        "cpu": {"vendor": "AuthenticAMD", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.1.0"},
        "gpu": [],
        "network": [
            {"interface": "eth0", "mac": "aa:..", "driver": "dupb"},
            {"interface": "eth1", "mac": "bb:..", "driver": "dupa"},
        ],
        "storage": [],
    }
    report = explain_mod.explain(doc, tmp_path, "CONFIG_DUP")
    # Both fragments set it; sorted-by-name order (dup-a, dup-b) -> dup-b wins.
    assert [f.fragment for f in report.fragments] == ["dup-a", "dup-b"]
    assert report.final_value == "n"
    assert report.final_source == "dup-b"


# ---------------------------------------------------------------------------
# ExplainReport / SymbolFragment helpers.
# ---------------------------------------------------------------------------


def test_explain_report_helpers():
    """ok is always True; found and exit_code reflect the query."""
    found = explain_mod.ExplainReport(
        symbol="CONFIG_A",
        fragments=(
            explain_mod.SymbolFragment(
                fragment="qemu", value="y", line_no=5, devices=()
            ),
        ),
        final_value="y",
        final_source="qemu",
    )
    assert found.ok
    assert found.found
    assert found.exit_code == 0
    not_found = explain_mod.ExplainReport(symbol="CONFIG_ZED")
    assert not_found.ok
    assert not not_found.found
    assert not_found.final_value is None


# ---------------------------------------------------------------------------
# render_explain.
# ---------------------------------------------------------------------------


def test_render_explain_found():
    """The found report names the symbol, value, fragment and pulling device."""
    report = explain_mod.explain(_amd_laptop(), FRAGMENTS, "CONFIG_VIRTIO_NET")
    text = explain_mod.render_explain(report)
    assert "CONFIG_VIRTIO_NET=y" in text
    assert "(from qemu)" in text
    assert "qemu (line 5): =y" in text
    assert "enp3s0 [network]" in text


def test_render_explain_not_found():
    """The not-found report says no selected fragment set the symbol."""
    report = explain_mod.explain(_amd_laptop(), FRAGMENTS, "CONFIG_NVIDIA")
    text = explain_mod.render_explain(report)
    assert "CONFIG_NVIDIA:" in text
    assert "not pulled in by this machine" in text


def test_render_explain_multiple_devices_suffix():
    """Multiple pulling devices get an (xN) suffix."""
    doc = {
        "cpu": {"vendor": "AuthenticAMD", "model": "X", "cores": 1, "threads": 1, "mhz": 1000.0},
        "metadata": {"hostname": "h", "kernel": "k", "tool_version": "0.1.0"},
        "gpu": [],
        "network": [
            {"interface": "enp0s3", "mac": "aa:..", "driver": "e1000e"},
            {"interface": "enp0s8", "mac": "bb:..", "driver": "e1000e"},
        ],
        "storage": [],
    }
    report = explain_mod.explain(doc, FRAGMENTS, "CONFIG_E1000E")
    text = explain_mod.render_explain(report)
    assert "(x2)" in text


# ---------------------------------------------------------------------------
# CLI: explain end-to-end.
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


def test_cli_explain_success_stdout():
    result = _run_cli(
        "explain",
        "--profile", _fixture_path("profile.toml"),
        "--symbol", "CONFIG_VIRTIO_NET",
        "--fragments", os.path.join(FRAGMENTS),
    )
    assert result.returncode == 0
    assert "CONFIG_VIRTIO_NET=y" in result.stdout
    assert "enp3s0" in result.stdout
    # The unmapped-device warning goes to stderr, not the report.
    assert "wlo1" in result.stderr


def test_cli_explain_not_selected_is_clean():
    result = _run_cli(
        "explain",
        "--profile", _fixture_path("profile.toml"),
        "--symbol", "CONFIG_NVIDIA",
        "--fragments", os.path.join(FRAGMENTS),
    )
    assert result.returncode == 0
    assert "not pulled in by this machine" in result.stdout


def test_cli_explain_writes_to_out_file(tmp_path):
    out = tmp_path / "explain.txt"
    result = _run_cli(
        "explain",
        "--profile", _fixture_path("profile.toml"),
        "--symbol", "CONFIG_DRM_AMDGPU",
        "--fragments", os.path.join(FRAGMENTS),
        "--out", str(out),
    )
    assert result.returncode == 0
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "CONFIG_DRM_AMDGPU=y" in text
    assert "Vega [Radeon RX Vega]" in text


def test_cli_explain_missing_profile_exits_1(tmp_path):
    result = _run_cli(
        "explain",
        "--profile", str(tmp_path / "nope.toml"),
        "--symbol", "CONFIG_VIRTIO_NET",
        "--fragments", os.path.join(FRAGMENTS),
    )
    assert result.returncode == 1
    assert "error" in result.stderr.lower()
    assert result.stdout == ""


def test_cli_explain_missing_fragment_exits_2(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _run_cli(
        "explain",
        "--profile", _fixture_path("profile.toml"),
        "--symbol", "CONFIG_VIRTIO_NET",
        "--fragments", str(empty),
    )
    assert result.returncode == 2
    assert "missing fragment" in result.stderr.lower()
    assert result.stdout == ""


def test_cli_explain_usage_error_missing_symbol():
    result = _run_cli(
        "explain",
        "--profile", _fixture_path("profile.toml"),
        "--fragments", os.path.join(FRAGMENTS),
    )
    assert result.returncode == 2
    assert "--symbol" in result.stderr


def test_cli_explain_version():
    result = _run_cli("--version")
    assert result.returncode == 0
    assert "pengyos-hwprofile-kernel" in result.stdout


def test_parser_has_explain_subcommand():
    from pengyos_hwprofile_kernel.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(
        ["explain", "--profile", "p.toml", "--symbol", "CONFIG_A", "--fragments", "d"]
    )
    assert args.command == "explain"
    assert args.symbol == "CONFIG_A"
    assert args.profile == "p.toml"
    assert args.fragments == "d"
