"""Tests for the emit step (``pengyos_fleet.emit``) and the ``collect`` CLI.

These lock down the emit contract: ``render`` produces deterministic,
hwprofile-schema-conformant ``profile.toml`` text (byte-identical whether the
sibling ``pengyos_hwprofile`` emitter is reused or the local copy is used),
``emit_profile`` / ``collect_all`` write one ``profile.toml`` per host, and the
``collect`` subcommand wires it all together with the right exit codes.

No network, no root, no real hardware — every run is driven by the deterministic
mock runner against committed fixtures.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

import pengyos_fleet
from pengyos_fleet import __version__, emit
from pengyos_fleet.cli import build_parser, main
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


def _read(*parts: str) -> str:
    return (HERE / "fixtures" / "hosts").joinpath(*parts).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# render() — the TOML writer.
# ---------------------------------------------------------------------------


def test_render_reuses_hwprofile_emitter_when_importable():
    # conftest.py puts the sibling on sys.path, so the reuse branch is taken in
    # the normal test run; the fallback is byte-identical anyway.
    from pengyos_hwprofile.emitter import emit as _hwprofile_emit  # type: ignore

    assert emit._USES_HWPROFILE is True
    doc = {"metadata": {"hostname": "h", "kernel": "k", "tool_version": "0"},
           "cpu": {"vendor": "v", "model": "m", "cores": 1, "threads": 1, "mhz": 1.0}}
    assert emit.render(doc) == _hwprofile_emit(doc)


def test_render_falls_back_to_local_copy_when_sibling_absent(monkeypatch):
    # Force the "sibling not importable" branch and confirm render still works.
    monkeypatch.setattr(emit, "_USES_HWPROFILE", False)
    monkeypatch.setattr(emit, "_hwprofile_emit", None)
    doc = {"metadata": {"hostname": "h", "kernel": "k", "tool_version": "0"},
           "cpu": {"vendor": "v", "model": "m", "cores": 1, "threads": 1, "mhz": 1.0}}
    assert emit.render(doc) == emit._local_emit(doc)


def test_render_local_copy_matches_hwprofile_emitter():
    # The documented local copy must be byte-identical to the sibling writer.
    from pengyos_hwprofile.emitter import emit as _hwprofile_emit  # type: ignore

    for name in ("n1", "n2"):
        doc = pengyos_fleet.collect_facts(MockRunner(), _host(name))
        assert emit._local_emit(doc) == _hwprofile_emit(doc)


def test_render_renders_metadata_and_cpu_as_tables():
    text = emit.render({"metadata": {"hostname": "h", "kernel": "k", "tool_version": "0"},
                        "cpu": {"vendor": "v", "model": "m", "cores": 1, "threads": 1, "mhz": 1.0}})
    assert text.startswith("[metadata]\n")
    assert "[cpu]" in text
    assert text.index("hostname") < text.index("[cpu]")


def test_render_uses_fixed_key_order():
    text = emit.render({"cpu": {"mhz": 1.0, "threads": 1, "cores": 1, "model": "m", "vendor": "v"},
                        "metadata": {"tool_version": "0", "kernel": "k", "hostname": "h"}})
    order = [line.split(" = ")[0] for line in text.splitlines() if " = " in line]
    assert order == ["hostname", "kernel", "tool_version", "vendor", "model", "cores", "threads", "mhz"]


def test_render_ints_bare_and_floats_have_point():
    text = emit.render({"metadata": {"hostname": "h", "kernel": "k", "tool_version": "0"},
                        "cpu": {"vendor": "v", "model": "m", "cores": 4, "threads": 8, "mhz": 2300.0}})
    assert "cores = 4" in text
    assert "mhz = 2300.0" in text


def test_render_escapes_special_characters():
    text = emit.render({"metadata": {"hostname": 'line1\nline2\t"q"', "kernel": "k", "tool_version": "0"},
                        "cpu": {"vendor": "v", "model": "m", "cores": 1, "threads": 1, "mhz": 1.0}})
    assert 'hostname = "line1\\nline2\\t\\"q\\""' in text


def test_render_skips_absent_list_sections():
    text = emit.render({"metadata": {"hostname": "h", "kernel": "k", "tool_version": "0"},
                        "cpu": {"vendor": "v", "model": "m", "cores": 1, "threads": 1, "mhz": 1.0}})
    assert "[[" not in text


def test_render_one_header_per_list_item():
    text = emit.render({"metadata": {"hostname": "h", "kernel": "k", "tool_version": "0"},
                        "cpu": {"vendor": "v", "model": "m", "cores": 1, "threads": 1, "mhz": 1.0},
                        "storage": [
                            {"bus": "sata", "model": "A", "size_bytes": 1},
                            {"bus": "nvme", "model": "B", "size_bytes": 2},
                        ]})
    assert text.count("[[storage]]") == 2
    assert text.count("bus = ") == 2


def test_render_is_deterministic_regardless_of_input_order():
    doc = {"metadata": {"hostname": "h", "kernel": "k", "tool_version": "0"},
           "cpu": {"vendor": "v", "model": "m", "cores": 1, "threads": 1, "mhz": 1.0},
           "storage": [
               {"bus": "sata", "model": "Zeta", "size_bytes": 1},
               {"bus": "nvme", "model": "Alpha", "size_bytes": 2},
           ]}
    shuffled = {
        "storage": doc["storage"][::-1],
        "metadata": dict(reversed(list(doc["metadata"].items()))),
        "cpu": dict(reversed(list(doc["cpu"].items()))),
    }
    assert emit.render(doc) == emit.render(shuffled)


def test_render_sorts_list_items_by_identity():
    text = emit.render({"metadata": {"hostname": "h", "kernel": "k", "tool_version": "0"},
                        "cpu": {"vendor": "v", "model": "m", "cores": 1, "threads": 1, "mhz": 1.0},
                        "storage": [
                            {"bus": "sata", "model": "Zeta", "size_bytes": 1},
                            {"bus": "nvme", "model": "Alpha", "size_bytes": 2},
                        ]})
    assert text.index("Alpha") < text.index("Zeta")


def test_render_ends_with_single_newline():
    doc = {"metadata": {"hostname": "h", "kernel": "k", "tool_version": "0"},
           "cpu": {"vendor": "v", "model": "m", "cores": 1, "threads": 1, "mhz": 1.0},
           "storage": [{"bus": "sata", "model": "A", "size_bytes": 1}]}
    text = emit.render(doc)
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_render_round_trips_through_tomllib():
    for name in ("n1", "n2"):
        doc = pengyos_fleet.collect_facts(MockRunner(), _host(name))
        reloaded = tomllib.loads(emit.render(doc))
        assert reloaded["metadata"]["hostname"] == name
        assert reloaded["cpu"]["cores"] == doc["cpu"]["cores"]


def test_render_rejects_non_mapping():
    with pytest.raises(ValueError):
        emit.render([1, 2, 3])


# ---------------------------------------------------------------------------
# emit_profile() — one profile.toml per host.
# ---------------------------------------------------------------------------


def test_emit_profile_writes_file(tmp_path):
    path = emit.emit_profile(MockRunner(), _host("n1"), tmp_path)
    assert path == tmp_path / "n1" / "profile.toml"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("[metadata]")
    assert "hostname = \"n1\"" in text


def test_emit_profile_creates_nested_output_dir(tmp_path):
    nested = tmp_path / "deep" / "profiles"
    path = emit.emit_profile(MockRunner(), _host("n1"), nested)
    assert path.parent == nested / "n1"
    assert path.is_file()


def test_emit_profile_is_idempotent(tmp_path):
    first = emit.emit_profile(MockRunner(), _host("n1"), tmp_path)
    first_bytes = first.read_bytes()
    # A second write to the same target reproduces byte-identical output.
    emit.emit_profile(MockRunner(), _host("n1"), tmp_path)
    assert first.read_bytes() == first_bytes


def test_emit_profile_all_six_sections_present(tmp_path):
    text = emit.emit_profile(MockRunner(), _host("n1"), tmp_path).read_text(encoding="utf-8")
    for section in ("[metadata]", "[cpu]", "[memory]", "[gpu]", "[storage]", "[network]"):
        assert section in text


def test_emit_profile_round_trips_and_validates():
    from pengyos_hwprofile.schema import validate  # type: ignore

    doc = pengyos_fleet.collect_facts(MockRunner(), _host("n1"))
    reloaded = tomllib.loads(emit.render(doc))
    validate(reloaded)  # raises ProfileError on any violation


def test_emit_profile_tool_version_is_current():
    text = emit.emit_profile(MockRunner(), _host("n1"), Path("/tmp")).read_text(encoding="utf-8")
    assert f"tool_version = \"{__version__}\"" in text


# ---------------------------------------------------------------------------
# collect_all() / profile_written().
# ---------------------------------------------------------------------------


def test_collect_all_writes_one_per_host(tmp_path):
    hosts = [_host("n1"), _host("n2")]
    paths = emit.collect_all(MockRunner(), hosts, tmp_path)
    assert paths == [tmp_path / "n1" / "profile.toml", tmp_path / "n2" / "profile.toml"]
    for path in paths:
        assert path.is_file()


def test_collect_all_preserves_host_order(tmp_path):
    hosts = [_host("n2"), _host("n1")]
    paths = emit.collect_all(MockRunner(), hosts, tmp_path)
    assert [p.parent.name for p in paths] == ["n2", "n1"]


def test_profile_written_reflects_disk_state(tmp_path):
    host = _host("n1")
    assert emit.profile_written(tmp_path, host) is False
    emit.emit_profile(MockRunner(), host, tmp_path)
    assert emit.profile_written(tmp_path, host) is True


# ---------------------------------------------------------------------------
# emit_profile_if_changed() — write only when the profile is stale.
# ---------------------------------------------------------------------------


def test_emit_profile_if_changed_writes_when_missing(tmp_path):
    host = _host("n1")
    path = emit.emit_profile_if_changed(MockRunner(), host, tmp_path)
    assert path == tmp_path / "n1" / "profile.toml"
    assert path.is_file()


def test_emit_profile_if_changed_skips_when_unchanged(tmp_path):
    # A second collect over identical data must not rewrite the file — the mtime
    # is left untouched, proving no write happened.
    host = _host("n1")
    first = emit.emit_profile_if_changed(MockRunner(), host, tmp_path)
    before = first.stat().st_mtime_ns
    result = emit.emit_profile_if_changed(MockRunner(), host, tmp_path)
    after = first.stat().st_mtime_ns
    assert result is None
    assert before == after


def test_emit_profile_if_changed_writes_when_stale(tmp_path):
    # An on-disk profile that does not match a fresh collect is rewritten.
    host = _host("n1")
    path = emit.emit_profile_if_changed(MockRunner(), host, tmp_path)
    # Corrupt the on-disk profile so it no longer matches a fresh collect.
    path.write_text("[metadata]\nhostname = \"stale\"\n", encoding="utf-8")
    stale_mtime = path.stat().st_mtime_ns
    result = emit.emit_profile_if_changed(MockRunner(), host, tmp_path)
    assert result == path
    assert path.stat().st_mtime_ns != stale_mtime
    assert "hostname = \"n1\"" in path.read_text(encoding="utf-8")


def test_emit_profile_if_changed_skips_written_by_unconditional_writer(tmp_path):
    # A file written by the unconditional writer is byte-identical to a fresh
    # collect, so the changed-only variant skips it (no rewrite).
    host = _host("n1")
    emit.emit_profile(MockRunner(), host, tmp_path)
    before = (tmp_path / "n1" / "profile.toml").stat().st_mtime_ns
    result = emit.emit_profile_if_changed(MockRunner(), host, tmp_path)
    after = (tmp_path / "n1" / "profile.toml").stat().st_mtime_ns
    assert result is None
    assert before == after


# ---------------------------------------------------------------------------
# collect_all() — changed only.
# ---------------------------------------------------------------------------


def test_collect_all_writes_all_on_first_run(tmp_path):
    hosts = [_host("n1"), _host("n2")]
    paths = emit.collect_all(MockRunner(), hosts, tmp_path)
    assert paths == [tmp_path / "n1" / "profile.toml", tmp_path / "n2" / "profile.toml"]


def test_collect_all_skips_everything_on_unchanged_recolllect(tmp_path):
    # Idempotent re-collect makes no changes: the second run writes nothing.
    hosts = [_host("n1"), _host("n2")]
    emit.collect_all(MockRunner(), hosts, tmp_path)
    mtimes = {
        name: (tmp_path / name / "profile.toml").stat().st_mtime_ns
        for name in ("n1", "n2")
    }
    written = emit.collect_all(MockRunner(), hosts, tmp_path)
    assert written == []
    for name, before in mtimes.items():
        assert (tmp_path / name / "profile.toml").stat().st_mtime_ns == before


def test_collect_all_writes_only_changed_host(tmp_path):
    # Corrupt one host's profile; the re-collect rewrites just that one.
    hosts = [_host("n1"), _host("n2")]
    emit.collect_all(MockRunner(), hosts, tmp_path)
    (tmp_path / "n2" / "profile.toml").write_text("[metadata]\nhostname = \"stale\"\n", encoding="utf-8")
    written = emit.collect_all(MockRunner(), hosts, tmp_path)
    assert written == [tmp_path / "n2" / "profile.toml"]


def test_collect_all_preserves_host_order_for_written(tmp_path):
    hosts = [_host("n2"), _host("n1")]
    paths = emit.collect_all(MockRunner(), hosts, tmp_path)
    assert [p.parent.name for p in paths] == ["n2", "n1"]


# ---------------------------------------------------------------------------
# collect CLI subcommand.
# ---------------------------------------------------------------------------


def _write_hosts(tmp_path, text: str) -> Path:
    path = tmp_path / "hosts.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _hosts_toml(tmp_path: Path, names: list[str]) -> Path:
    # Absolute fixture paths so the list is self-contained in tmp_path (relative
    # fixtures would resolve against the file's own directory).
    entries = "\n".join(
        f'[[hosts]]\nname = "{n}"\naddress = "{n}.home.lab"\nfixtures = "{FIXTURE_HOSTS / n}"\n'
        for n in names
    )
    return _write_hosts(tmp_path, entries + "\n")


def test_cli_collect_writes_profiles(tmp_path, capsys):
    hosts = _hosts_toml(tmp_path, ["n1"])
    out = tmp_path / "profiles"
    rc = main(["collect", "--hosts", str(hosts), "--out", str(out), "--runner", "mock"])
    assert rc == 0
    written = (out / "n1" / "profile.toml")
    assert written.is_file()
    out_err = capsys.readouterr().out
    assert str(written) in out_err


def test_cli_collect_multiple_hosts(tmp_path):
    hosts = _hosts_toml(tmp_path, ["n1", "n2"])
    out = tmp_path / "profiles"
    rc = main(["collect", "--hosts", str(hosts), "--out", str(out), "--runner", "mock"])
    assert rc == 0
    assert (out / "n1" / "profile.toml").is_file()
    assert (out / "n2" / "profile.toml").is_file()


def test_cli_collect_missing_hosts_file_returns_2(tmp_path, capsys):
    rc = main(["collect", "--hosts", str(tmp_path / "nope.toml"), "--out", str(tmp_path / "o")])
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_cli_collect_reports_written_then_unchanged(tmp_path, capsys):
    # First run writes; a second run over identical data reports "unchanged"
    # and writes nothing.
    hosts = _hosts_toml(tmp_path, ["n1", "n2"])
    out = tmp_path / "profiles"
    assert main(["collect", "--hosts", str(hosts), "--out", str(out), "--runner", "mock"]) == 0
    out_first = capsys.readouterr().out
    assert f"wrote {out / 'n1' / 'profile.toml'}" in out_first
    assert f"wrote {out / 'n2' / 'profile.toml'}" in out_first

    # Capture mtimes, then re-collect.
    mtimes = {
        name: (out / name / "profile.toml").stat().st_mtime_ns
        for name in ("n1", "n2")
    }
    assert main(["collect", "--hosts", str(hosts), "--out", str(out), "--runner", "mock"]) == 0
    out_second = capsys.readouterr().out
    assert f"unchanged {out / 'n1' / 'profile.toml'}" in out_second
    assert f"unchanged {out / 'n2' / 'profile.toml'}" in out_second
    for name, before in mtimes.items():
        assert (out / name / "profile.toml").stat().st_mtime_ns == before


def test_cli_collect_rewrites_stale_host(tmp_path, capsys):
    # A host whose on-disk profile went stale is rewritten; the other stays
    # "unchanged".
    hosts = _hosts_toml(tmp_path, ["n1", "n2"])
    out = tmp_path / "profiles"
    main(["collect", "--hosts", str(hosts), "--out", str(out), "--runner", "mock"])
    (out / "n2" / "profile.toml").write_text("[metadata]\nhostname = \"stale\"\n", encoding="utf-8")
    main(["collect", "--hosts", str(hosts), "--out", str(out), "--runner", "mock"])
    out_err = capsys.readouterr().out
    assert f"wrote {out / 'n2' / 'profile.toml'}" in out_err
    assert f"unchanged {out / 'n1' / 'profile.toml'}" in out_err


def test_cli_collect_missing_fixture_returns_1(tmp_path, capsys):
    # A host with no fixtures -> the mock runner fails loudly.
    hosts = _write_hosts(
        tmp_path,
        """
[[hosts]]
name = "ghost"
address = "ghost.home.lab"
""",
    )
    rc = main(["collect", "--hosts", str(hosts), "--out", str(tmp_path / "o"), "--runner", "mock"])
    assert rc == 1
    assert "fixtures" in capsys.readouterr().err


def test_cli_collect_invalid_runner_is_rejected():
    # argparse rejects an unknown --runner choice before any handler runs.
    with pytest.raises(SystemExit):
        main(["collect", "--hosts", "x", "--out", "y", "--runner", "telnet"])


def test_cli_collect_requires_hosts_and_out():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["collect", "--out", "y"])


def test_cli_collect_help_lists_command():
    help_text = build_parser().format_help()
    assert "collect" in help_text


# ---------------------------------------------------------------------------
# Integration: the whole flow over the mock runner is opt-in.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_cli_collect_integration_over_mock(tmp_path):
    hosts = _hosts_toml(tmp_path, ["n1"])
    out = tmp_path / "profiles"
    assert main(["collect", "--hosts", str(hosts), "--out", str(out), "--runner", "mock"]) == 0
    assert (out / "n1" / "profile.toml").is_file()
