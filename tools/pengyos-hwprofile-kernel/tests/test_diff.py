"""Tests for :mod:`pengyos_hwprofile_kernel.diff`.

These lock in the "fragment delta between two profiles = hardware-change
detection" contract. Given two
validated hwprofile documents, :func:`diff` reports the fragments the second
profile carries that the first does not (**added**) and the fragments the first
carried that the second does not (**removed**); each changed fragment carries
the symbols it sets.

Table-driven over the committed AMD-laptop / Intel-desktop fixture pair (which
differ only by CPU vendor and GPU driver, so the delta is exactly
amd-cpu/amd-igpu -> intel-cpu/intel-igpu), plus a missing-fragment and a
determinism case. No network, no root, no hardware.
"""

from __future__ import annotations

import os
import subprocess
import sys

import tomllib

from pengyos_hwprofile_kernel import diff as diff_mod

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
FRAGMENTS = os.path.join(os.path.dirname(__file__), "..", "fragments")


def _fixture(name: str) -> dict:
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return tomllib.load(fh)


def _fixture_path(name: str) -> str:
    return os.path.join(FIXTURES, name)


def _amd() -> dict:
    """The AMD-laptop fixture (amd-cpu/amd-igpu/nvme/qemu/sata)."""
    return _fixture("profile.toml")


def _intel() -> dict:
    """The Intel-desktop fixture (intel-cpu/intel-igpu/nvme/qemu/sata)."""
    return _fixture("profile_intel.toml")


# ---------------------------------------------------------------------------
# diff: the AMD -> Intel pair.
# ---------------------------------------------------------------------------


def test_diff_identical_profiles_has_no_changes():
    """A profile diffed against itself selects the same fragment set."""
    report = diff_mod.diff(_amd(), _amd(), FRAGMENTS)
    assert report.identical
    assert report.changed == 0
    assert report.added == ()
    assert report.removed == ()
    assert report.a_fragments == report.b_fragments


def test_diff_added_is_b_minus_a():
    """Going AMD -> Intel, the added fragments are the Intel builds."""
    report = diff_mod.diff(_amd(), _intel(), FRAGMENTS)
    assert not report.identical
    # 2 added + 2 removed = 4 changed fragments.
    assert report.changed == 4
    assert [d.fragment for d in report.added] == ["intel-cpu", "intel-igpu"]
    assert all(d.kind == "added" for d in report.added)


def test_diff_removed_is_a_minus_b():
    """Going AMD -> Intel, the removed fragments are the AMD builds."""
    report = diff_mod.diff(_amd(), _intel(), FRAGMENTS)
    assert [d.fragment for d in report.removed] == ["amd-cpu", "amd-igpu"]
    assert all(d.kind == "removed" for d in report.removed)


def test_diff_shared_fragments_are_not_reported():
    """Fragments both profiles carry (nvme/qemu/sata) are unchanged, not delta."""
    report = diff_mod.diff(_amd(), _intel(), FRAGMENTS)
    changed = {d.fragment for d in report.added} | {
        d.fragment for d in report.removed
    }
    assert changed == {"intel-cpu", "intel-igpu", "amd-cpu", "amd-igpu"}
    for shared in ("nvme", "qemu", "sata"):
        assert shared not in changed


def test_diff_each_delta_carries_symbols():
    """Each changed fragment lists the symbols it sets (sorted)."""
    report = diff_mod.diff(_amd(), _intel(), FRAGMENTS)
    by_fragment = {d.fragment: d for d in report.added + report.removed}
    # intel-cpu sets its three CPU-family symbols.
    assert by_fragment["intel-cpu"].symbols == (
        "CONFIG_CPU_FREQ",
        "CONFIG_CPU_FREQ_GOV_INTEL_PERF",
        "CONFIG_X86_INTEL_PSTATE",
    )
    # amd-igpu sets the AMDGPU DRM symbols.
    assert by_fragment["amd-igpu"].symbols == (
        "CONFIG_DRM",
        "CONFIG_DRM_AMD",
        "CONFIG_DRM_AMDGPU",
        "CONFIG_FB",
        "CONFIG_FB_SYS_FOPS",
    )
    # Every symbol listed really belongs to that fragment.
    from pengyos_hwprofile_kernel import select

    library = select.load_library(FRAGMENTS)
    for delta in report.added + report.removed:
        key = f"{delta.fragment}.fragment"
        expected = tuple(sorted(library[key].values))
        assert delta.symbols == expected


def test_diff_direction_matters():
    """Going Intel -> AMD flips added/removed."""
    forward = diff_mod.diff(_amd(), _intel(), FRAGMENTS)
    backward = diff_mod.diff(_intel(), _amd(), FRAGMENTS)
    assert [d.fragment for d in forward.added] == [
        d.fragment for d in backward.removed
    ]
    assert [d.fragment for d in forward.removed] == [
        d.fragment for d in backward.added
    ]


def test_diff_is_deterministic():
    """Repeated diff yields byte-identical reports."""
    first = diff_mod.diff(_amd(), _intel(), FRAGMENTS)
    second = diff_mod.diff(_amd(), _intel(), FRAGMENTS)
    assert first.added == second.added
    assert first.removed == second.removed
    assert first.a_fragments == second.a_fragments
    assert first.b_fragments == second.b_fragments


def test_diff_report_helpers():
    """changed = added + removed; identical when zero."""
    report = diff_mod.diff(_amd(), _intel(), FRAGMENTS)
    assert report.changed == len(report.added) + len(report.removed)
    assert not report.identical
    same = diff_mod.diff(_amd(), _amd(), FRAGMENTS)
    assert same.identical
    assert same.changed == 0


def test_diff_labels_surfaces_a_and_b():
    """The profile labels are surfaced on the report."""
    report = diff_mod.diff(
        _amd(), _intel(), FRAGMENTS, profile_a="laptop", profile_b="desktop"
    )
    assert report.a == "laptop"
    assert report.b == "desktop"


def test_diff_reports_unknown_across_both():
    """Unmapped devices from either profile are reported (never dropped)."""
    report = diff_mod.diff(_amd(), _intel(), FRAGMENTS)
    # AMD's driverless Wi-Fi iface is unknown; Intel's is clean.
    assert [u.device.get("interface") for u in report.unknown] == ["wlo1"]


# ---------------------------------------------------------------------------
# diff: edge cases.
# ---------------------------------------------------------------------------


def test_diff_missing_fragment_is_reported(tmp_path):
    """A selected fragment absent from the dir is surfaced as missing."""
    empty = tmp_path / "empty"
    empty.mkdir()
    report = diff_mod.diff(_amd(), _intel(), empty)
    # Every selected fragment is absent from the empty dir.
    assert report.missing
    assert set(report.missing) == set(report.a_fragments) | set(report.b_fragments)


def test_diff_empty_profiles_have_empty_delta():
    """Two empty profiles select no fragments -> no delta."""
    report = diff_mod.diff({}, {}, FRAGMENTS)
    assert report.identical
    assert report.added == ()
    assert report.removed == ()


# ---------------------------------------------------------------------------
# render_diff.
# ---------------------------------------------------------------------------


def test_render_diff_added_and_removed_lines():
    """The report names the change counts and one line per changed fragment."""
    report = diff_mod.diff(_amd(), _intel(), FRAGMENTS)
    text = diff_mod.render_diff(report)
    assert "hardware change" in text
    assert "2 added, 2 removed" in text
    assert "+ intel-cpu" in text
    assert "- amd-cpu" in text
    # Each changed fragment shows its symbol count.
    assert "(added: 3 new)" in text
    assert "(removed: 3 old)" in text
    # The symbols of a changed fragment are listed.
    assert "CONFIG_X86_INTEL_PSTATE" in text
    assert "CONFIG_CPU_FREQ_AMDPM" in text


def test_render_diff_identical_is_just_header():
    """An identical diff prints just the (zero-change) header."""
    report = diff_mod.diff(_amd(), _amd(), FRAGMENTS)
    text = diff_mod.render_diff(report)
    assert text.count("\n") == 1  # header + trailing newline only
    assert "0 added, 0 removed" in text


# ---------------------------------------------------------------------------
# CLI: diff end-to-end.
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


def test_cli_diff_success_stdout():
    result = _run_cli(
        "diff",
        "--a", _fixture_path("profile.toml"),
        "--b", _fixture_path("profile_intel.toml"),
        "--fragments", os.path.join(FRAGMENTS),
    )
    assert result.returncode == 0
    assert "2 added, 2 removed" in result.stdout
    assert "+ intel-cpu" in result.stdout
    assert "- amd-cpu" in result.stderr or "+ intel-cpu" in result.stdout
    # The unmapped-device warning goes to stderr.
    assert "wlo1" in result.stderr


def test_cli_diff_json_output():
    result = _run_cli(
        "diff",
        "--a", _fixture_path("profile.toml"),
        "--b", _fixture_path("profile_intel.toml"),
        "--fragments", os.path.join(FRAGMENTS),
        "--json",
    )
    assert result.returncode == 0
    import json

    data = json.loads(result.stdout)
    assert data["a_fragments"] == ["amd-cpu", "amd-igpu", "nvme", "qemu", "sata"]
    assert data["b_fragments"] == ["intel-cpu", "intel-igpu", "nvme", "qemu", "sata"]
    assert [d["fragment"] for d in data["added"]] == ["intel-cpu", "intel-igpu"]
    assert [d["fragment"] for d in data["removed"]] == ["amd-cpu", "amd-igpu"]
    assert all(d["kind"] == "added" for d in data["added"])
    assert all(d["kind"] == "removed" for d in data["removed"])
    # Each added fragment lists its symbols.
    intel_cpu = next(d for d in data["added"] if d["fragment"] == "intel-cpu")
    assert "CONFIG_X86_INTEL_PSTATE" in intel_cpu["symbols"]


def test_cli_diff_writes_to_out_file(tmp_path):
    out = tmp_path / "diff.txt"
    result = _run_cli(
        "diff",
        "--a", _fixture_path("profile.toml"),
        "--b", _fixture_path("profile_intel.toml"),
        "--fragments", os.path.join(FRAGMENTS),
        "--out", str(out),
    )
    assert result.returncode == 0
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "2 added, 2 removed" in text
    assert "+ intel-cpu" in text


def test_cli_diff_identical_profiles():
    result = _run_cli(
        "diff",
        "--a", _fixture_path("profile.toml"),
        "--b", _fixture_path("profile.toml"),
        "--fragments", os.path.join(FRAGMENTS),
    )
    assert result.returncode == 0
    assert "0 added, 0 removed" in result.stdout


def test_cli_diff_missing_profile_exits_1(tmp_path):
    result = _run_cli(
        "diff",
        "--a", _fixture_path("profile.toml"),
        "--b", str(tmp_path / "nope.toml"),
        "--fragments", os.path.join(FRAGMENTS),
    )
    assert result.returncode == 1
    assert "error" in result.stderr.lower()
    assert result.stdout == ""


def test_cli_diff_missing_fragment_exits_2(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _run_cli(
        "diff",
        "--a", _fixture_path("profile.toml"),
        "--b", _fixture_path("profile_intel.toml"),
        "--fragments", str(empty),
    )
    assert result.returncode == 2
    assert "missing fragment" in result.stderr.lower()
    assert result.stdout == ""


def test_cli_diff_usage_error_missing_b():
    result = _run_cli(
        "diff",
        "--a", _fixture_path("profile.toml"),
        "--fragments", os.path.join(FRAGMENTS),
    )
    assert result.returncode == 2
    assert "--b" in result.stderr


def test_cli_diff_version():
    result = _run_cli("--version")
    assert result.returncode == 0
    assert "pengyos-hwprofile-kernel" in result.stdout


def test_parser_has_diff_subcommand():
    from pengyos_hwprofile_kernel.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(
        ["diff", "--a", "a.toml", "--b", "b.toml", "--fragments", "d"]
    )
    assert args.command == "diff"
    assert args.a == "a.toml"
    assert args.b == "b.toml"
    assert args.fragments == "d"
    assert args.json is False
