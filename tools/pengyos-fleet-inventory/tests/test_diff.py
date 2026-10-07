"""Tests for the diff subcommand (``pengyos_fleet.diff`` + ``pengyos-fleet diff``).

Covers loading and validating two profiles, the added / removed / changed
comparison over the hardware list sections, the report format, and the
``diff`` CLI subcommand (exit codes, help, error paths). Every run is
deterministic and offline: documents are built in-memory or produced by the
mock runner, and profiles are written to a temp dir.

The diff mirrors the sibling ``pengyos_hwprofile`` diff: same per-section
identity fields, same added (+) / removed (-) / changed (~) semantics, and —
when the sibling is importable — the same validator. The local copy is locked
to be equivalent to the sibling in the normal test run.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import pengyos_fleet
from pengyos_fleet import diff
from pengyos_fleet.cli import build_parser, main
from pengyos_fleet.diff import DiffError
from pengyos_fleet.hosts import Host
from pengyos_fleet.runner import MockRunner

HERE = Path(__file__).parent
FIXTURE_HOSTS = HERE / "fixtures" / "hosts"


def _host(name: str = "n1") -> Host:
    return Host(
        name=name,
        address=f"{name}.home.lab",
        fixtures=str(FIXTURE_HOSTS / name),
    )


# ---------------------------------------------------------------------------
# Valid document builders.
# ---------------------------------------------------------------------------


def _base_document() -> dict:
    """A valid profile with one device in every hardware list section."""

    return {
        "metadata": {"hostname": "host-a", "kernel": "6.8.0-pengyos", "tool_version": "0.1.0"},
        "cpu": {"vendor": "AMD", "model": "Ryzen", "cores": 8, "threads": 16, "mhz": 1800.0},
        "memory": [{"locator": "System", "size_bytes": 16000000000}],
        "gpu": [{"vendor": "AMD", "model": "Original", "bus": "00:02.0", "driver": "amdgpu"}],
        "storage": [
            {"bus": "nvme", "model": "Kept SSD", "size_bytes": 500000000000, "serial": "AAA1"}
        ],
        "network": [
            {"interface": "enp3s0", "mac": "aa:bb:cc:dd:ee:ff", "speed_mbps": 1000}
        ],
    }


def _write_profile(path: Path, document: dict) -> None:
    """Validate ``document`` and write it to ``path`` as TOML (via the emitter)."""

    import pengyos_fleet.emit

    from pengyos_hwprofile.schema import validate  # type: ignore

    validate(document)  # must be valid before we emit it
    path.write_text(pengyos_fleet.emit.render(document), encoding="utf-8")


def _write(tmp_path: Path, name: str, document: dict) -> Path:
    path = tmp_path / f"{name}.toml"
    _write_profile(path, document)
    return path


# ---------------------------------------------------------------------------
# load_profile().
# ---------------------------------------------------------------------------


def test_load_profile_reads_valid_file(tmp_path):
    path = _write(tmp_path, "a", _base_document())
    data = diff.load_profile(path)
    assert data["metadata"]["hostname"] == "host-a"
    assert data["cpu"]["cores"] == 8
    assert isinstance(data["storage"], list)


def test_load_profile_missing_file_raises(tmp_path):
    with pytest.raises(DiffError, match="not found"):
        diff.load_profile(str(tmp_path / "nope.toml"))


def test_load_profile_invalid_toml_raises(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("this is = = not valid toml [[[\n", encoding="utf-8")
    with pytest.raises(DiffError, match="not valid TOML"):
        diff.load_profile(str(path))


def test_load_profile_invalid_schema_raises(tmp_path):
    # Valid TOML but no ``cpu`` section -> schema violation.
    path = tmp_path / "noid.toml"
    path.write_text(
        '[metadata]\n'
        'hostname = "h"\n'
        'kernel = "k"\n'
        'tool_version = "0.1.0"\n',
        encoding="utf-8",
    )
    with pytest.raises(DiffError, match="missing required"):
        diff.load_profile(str(path))


def test_load_profile_reuses_hwprofile_validator_when_importable(tmp_path):
    # conftest.py puts the sibling on sys.path, so the reuse branch is taken in
    # the normal run; the fallback produces the same result anyway.
    assert diff._USES_HWPROFILE is True
    path = _write(tmp_path, "a", _base_document())
    # The document round-trips through the (reused) validator without raising.
    assert diff.load_profile(str(path))["cpu"]["cores"] == 8


def test_load_profile_falls_back_to_local_when_sibling_absent(tmp_path, monkeypatch):
    # Force the "sibling not importable" branch and confirm the local validator
    # still loads a valid file and refuses an invalid one.
    monkeypatch.setattr(diff, "_USES_HWPROFILE", False)
    monkeypatch.setattr(diff, "_hwprofile_load_profile", None)

    good = _write(tmp_path, "a", _base_document())
    assert diff.load_profile(str(good))["metadata"]["hostname"] == "host-a"

    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[metadata]\nhostname = "h"\nkernel = "k"\ntool_version = "0.1.0"\n',
        encoding="utf-8",
    )
    with pytest.raises(DiffError, match="missing required"):
        diff.load_profile(str(bad))


def test_local_validator_rejects_missing_mandatory_field(tmp_path, monkeypatch):
    # The local validator checks the mandatory fields of metadata/cpu, so a
    # profile missing (say) cpu.cores is refused even without the sibling.
    monkeypatch.setattr(diff, "_USES_HWPROFILE", False)
    monkeypatch.setattr(diff, "_hwprofile_load_profile", None)

    path = tmp_path / "noid.toml"
    path.write_text(
        '[metadata]\n'
        'hostname = "h"\n'
        'kernel = "k"\n'
        'tool_version = "0.1.0"\n'
        '[cpu]\n'
        'vendor = "v"\n'
        'model = "m"\n'
        'threads = 1\n'
        'mhz = 1.0\n',
        encoding="utf-8",
    )
    with pytest.raises(DiffError, match="missing required field"):
        diff.load_profile(str(path))


# ---------------------------------------------------------------------------
# diff_documents().
# ---------------------------------------------------------------------------


def test_diff_identical_is_empty():
    doc = _base_document()
    assert diff.diff_documents(doc, doc) == []


def test_diff_added_device():
    before = _base_document()
    after = _base_document()
    after["storage"] = [
        {"bus": "nvme", "model": "Kept SSD", "size_bytes": 500000000000, "serial": "AAA1"},
        {"bus": "sata", "model": "New Disk", "size_bytes": 2000000000000, "serial": "BBB2"},
    ]
    changes = diff.diff_documents(before, after)
    assert changes == [("+", "storage", "BBB2/sata/New Disk")]


def test_diff_removed_device():
    before = _base_document()
    before["storage"] = [
        {"bus": "nvme", "model": "Kept SSD", "size_bytes": 500000000000, "serial": "AAA1"},
        {"bus": "sata", "model": "Old Disk", "size_bytes": 1000000000000, "serial": "ZZZ9"},
    ]
    after = _base_document()
    changes = diff.diff_documents(before, after)
    assert changes == [("-", "storage", "ZZZ9/sata/Old Disk")]


def test_diff_changed_device():
    before = _base_document()
    after = _base_document()
    # The GPU bus (identity) is unchanged; only its driver changed.
    after["gpu"][0]["driver"] = "nouveau"
    changes = diff.diff_documents(before, after)
    assert changes == [("~", "gpu", "00:02.0")]


def test_diff_ignores_metadata_and_cpu():
    # metadata/cpu differ but no hardware section changed -> no diff.
    before = _base_document()
    after = _base_document()
    after["metadata"] = {"hostname": "other", "kernel": "9.9", "tool_version": "0.2.0"}
    after["cpu"] = {"vendor": "Intel", "model": "Xeon", "cores": 32, "threads": 64, "mhz": 3000.0}
    assert diff.diff_documents(before, after) == []


def test_diff_network_identity_is_interface_name():
    # Same interface, different MAC -> same physical device; the MAC is a
    # property, so this is reported as a single ~ changed (not a removal plus a
    # fresh addition).
    before = _base_document()
    after = _base_document()
    after["network"][0]["mac"] = "de:ad:be:ef:00:00"
    assert diff.diff_documents(before, after) == [("~", "network", "enp3s0")]

    # A different interface -> the identity differs, so added + removed.
    after = _base_document()
    after["network"] = [{"interface": "enp9s9", "mac": "de:ad:be:ef:00:01"}]
    assert diff.diff_documents(before, after) == [
        ("+", "network", "enp9s9"),
        ("-", "network", "enp3s0"),
    ]


def test_diff_storage_identity_includes_serial_bus_model():
    # Same serial but different model -> identity differs -> added + removed.
    before = _base_document()
    after = _base_document()
    after["storage"][0]["model"] = "Kept SSD (rev2)"
    changes = diff.diff_documents(before, after)
    assert changes == [
        ("+", "storage", "AAA1/nvme/Kept SSD (rev2)"),
        ("-", "storage", "AAA1/nvme/Kept SSD"),
    ]


def test_diff_stable_ordering():
    # Added items come before removed, which come before changed; sections are
    # emitted in the fixed list order.
    before = _base_document()
    after = _base_document()
    after["gpu"] = [{"vendor": "NVIDIA", "model": "New", "bus": "10:00.0", "driver": "nvidia"}]
    after["network"][0]["speed_mbps"] = 2500  # changed
    after["storage"].append({"bus": "sata", "model": "Extra", "size_bytes": 1, "serial": "CCC3"})
    changes = diff.diff_documents(before, after)
    kinds = [(k, s) for k, s, _ in changes]
    # gpu (added) before storage (added) before network (changed): section order.
    assert kinds.index(("+", "gpu")) < kinds.index(("+", "storage"))
    assert kinds.index(("+", "storage")) < kinds.index(("~", "network"))


def test_diff_empty_sections_never_raise():
    assert diff.diff_documents({}, {}) == []
    assert diff.diff_documents({"metadata": {}, "cpu": {}}, {"metadata": {}, "cpu": {}}) == []


# ---------------------------------------------------------------------------
# render_diff().
# ---------------------------------------------------------------------------


def test_render_diff_identical_line():
    doc = _base_document()
    text = diff.render_diff(doc, doc, label_a="A", label_b="B")
    assert text == "A and B are identical\n"


def test_render_diff_lines_format():
    before = _base_document()
    after = _base_document()
    # Same GPU (bus unchanged); only its driver changed -> one "~ changed" line.
    after["gpu"][0]["driver"] = "nouveau"
    text = diff.render_diff(before, after, label_a="A", label_b="B")
    assert text == "~ gpu: 00:02.0\n"


def test_render_diff_changes_and_trailing_newline():
    before = _base_document()
    after = _base_document()
    after["gpu"][0]["driver"] = "nouveau"  # changed
    after["storage"].append({"bus": "sata", "model": "Extra", "size_bytes": 1, "serial": "CCC3"})
    text = diff.render_diff(before, after)
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert "+ storage: CCC3/sata/Extra\n" in text
    assert "~ gpu: 00:02.0\n" in text


# ---------------------------------------------------------------------------
# Alignment with the sibling hwprofile diff (when importable).
# ---------------------------------------------------------------------------


def test_diff_matches_hwprofile_section_kinds():
    # When the sibling is importable, our (kind, section) detection agrees with
    # its _diff_documents, even though the per-device label differs.
    if not diff._USES_HWPROFILE:
        pytest.skip("pengyos_hwprofile not importable")
    from pengyos_hwprofile.cli import _diff_documents as hw_diff  # type: ignore

    before = _base_document()
    after = _base_document()
    after["gpu"] = [{"vendor": "NVIDIA", "model": "New", "bus": "10:00.0", "driver": "nvidia"}]
    after["network"][0]["speed_mbps"] = 2500
    after["storage"].append({"bus": "sata", "model": "Extra", "size_bytes": 1, "serial": "CCC3"})

    ours = {(k, s) for k, s, _ in diff.diff_documents(before, after)}
    # hwprofile prefixes are "+"/"-"/"~" and its "section" is the token after
    # the prefix (before the ":"); normalise to (kind, section) for comparison.
    theirs = {(line[0], line.split()[1].rstrip(":"))
              for line in hw_diff(before, after) if line.strip()}
    assert ours == theirs


def test_local_diff_matches_hwprofile_diff_on_collected_profiles():
    # The local diff over real collected profiles agrees with the sibling's.
    if not diff._USES_HWPROFILE:
        pytest.skip("pengyos_hwprofile not importable")
    from pengyos_hwprofile.cli import _diff_documents as hw_diff  # type: ignore

    a = pengyos_fleet.collect_facts(MockRunner(), _host("n1"))
    b = pengyos_fleet.collect_facts(MockRunner(), _host("n2"))
    ours = {(k, s) for k, s, _ in diff.diff_documents(a, b)}
    theirs = {(line[0], line.split()[1].rstrip(":"))
              for line in hw_diff(a, b) if line.strip()}
    assert ours == theirs


# ---------------------------------------------------------------------------
# diff CLI subcommand.
# ---------------------------------------------------------------------------


def test_cli_diff_identical_returns_zero(tmp_path, capsys):
    a = _write(tmp_path, "a", _base_document())
    b = _write(tmp_path, "b", _base_document())
    rc = main(["diff", str(a), str(b)])
    assert rc == 0
    assert "identical" in capsys.readouterr().out


def test_cli_diff_differ_returns_one(tmp_path, capsys):
    a = _write(tmp_path, "a", _base_document())
    after = _base_document()
    after["gpu"][0]["driver"] = "nouveau"
    b = _write(tmp_path, "b", after)
    rc = main(["diff", str(a), str(b)])
    assert rc == 1
    assert "~ gpu:" in capsys.readouterr().out


def test_cli_diff_reports_all_change_kinds(tmp_path, capsys):
    a = _write(tmp_path, "a", _base_document())
    after = _base_document()
    # keep the same GPU (bus unchanged) but change its driver -> ~ changed
    after["gpu"][0]["driver"] = "nouveau"
    after["network"][0]["speed_mbps"] = 2500  # ~ changed
    after["storage"].append({"bus": "sata", "model": "Extra", "size_bytes": 1, "serial": "CCC3"})  # + added
    b = _write(tmp_path, "b", after)
    rc = main(["diff", str(a), str(b)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "+ storage: CCC3/sata/Extra" in out
    assert "~ gpu: 00:02.0" in out
    assert "~ network: enp3s0" in out


def test_cli_diff_missing_file_returns_two(tmp_path, capsys):
    a = _write(tmp_path, "a", _base_document())
    rc = main(["diff", str(a), str(tmp_path / "nope.toml")])
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_cli_diff_invalid_toml_returns_two(tmp_path, capsys):
    a = _write(tmp_path, "a", _base_document())
    b = tmp_path / "bad.toml"
    b.write_text("this is = = not toml\n", encoding="utf-8")
    rc = main(["diff", str(a), str(b)])
    assert rc == 2
    assert "not valid TOML" in capsys.readouterr().err


def test_cli_diff_invalid_schema_returns_two(tmp_path, capsys):
    a = _write(tmp_path, "a", _base_document())
    b = tmp_path / "noid.toml"
    b.write_text(
        '[metadata]\nhostname = "h"\nkernel = "k"\ntool_version = "0.1.0"\n',
        encoding="utf-8",
    )
    rc = main(["diff", str(a), str(b)])
    assert rc == 2
    assert "missing required" in capsys.readouterr().err


def test_cli_diff_help_lists_command():
    help_text = build_parser().format_help()
    assert "diff" in help_text
    assert "added" in help_text.lower() or "removed" in help_text.lower()


def test_cli_diff_requires_two_args():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["diff", "only-one"])


def test_cli_diff_round_trips_through_collect(tmp_path, capsys):
    # The real end-to-end flow: collect two hosts, then diff the profiles.
    from pengyos_fleet.emit import emit_profile

    emit_profile(MockRunner(), _host("n1"), tmp_path)
    emit_profile(MockRunner(), _host("n2"), tmp_path)
    a = tmp_path / "n1" / "profile.toml"
    b = tmp_path / "n2" / "profile.toml"
    rc = main(["diff", str(a), str(b)])
    assert rc == 1
    out = capsys.readouterr().out
    # n1 has one NVMe; n2 has two storage devices and a different GPU.
    assert "storage" in out
    assert "gpu" in out


# ---------------------------------------------------------------------------
# Integration: the diff over the mock runner is opt-in.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_cli_diff_integration_over_mock(tmp_path):
    from pengyos_fleet.emit import emit_profile

    emit_profile(MockRunner(), _host("n1"), tmp_path)
    emit_profile(MockRunner(), _host("n2"), tmp_path)
    assert main(["diff", str(tmp_path / "n1" / "profile.toml"),
                 str(tmp_path / "n2" / "profile.toml")]) == 1
