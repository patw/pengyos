"""Tests for the ``--auto`` front-end.

These cover the spec's core contract — *auto(config) equals the interactive
answer set for the same inputs* — plus the "a missing/invalid field is a
reported error, not a crash" guarantee, end-to-end file reading, and the CLI
surface. Everything is offline: files live under ``tmp_path`` and block-device
facts are injected through a fixture ``probe``.
"""

import contextlib
import io
import json
import tomllib

import pytest

from pengyos_install_tui import model
from pengyos_install_tui.auto import (
    ConfigError,
    build_plan,
    parse_config,
    plan_to_json,
    read_config,
    STEPS,
)
from pengyos_install_tui.cli import ExitCode, main
from pengyos_install_tui.model import ValidationError, Wizard

# The canonical happy-path answer set (see tests/test_model.py). ``auto`` must
# reproduce this exact shape.
HAPPY = {
    "target": "/dev/sdb",
    "edition": "core",
    "layout": "single-root",
    "user": "alice",
    "network": "dhcp",
}

# A full static-network, A/B layout variant.
FULL = {
    "target": "/dev/nvme0n1",
    "edition": "server",
    "layout": "ab-split",
    "user": {"username": "root-admin", "uid": 1000},
    "network": {"mode": "static", "address": "10.0.0.5/24",
                "gateway": "10.0.0.1", "nameservers": ["1.1.1.1", "8.8.8.8"]},
}


# ---------------------------------------------------------------------------
# build_plan: the pure parser — the spec's auto(config) == interactive contract
# ---------------------------------------------------------------------------


def test_build_plan_produces_canonical_plan():
    plan = build_plan(HAPPY)
    assert plan.to_dict() == {
        "target": {"path": "/dev/sdb"},
        "edition": "core",
        "layout": "single-root",
        "user": {"username": "alice", "uid": 1000},
        "network": {"mode": "dhcp"},
    }


def test_auto_equals_interactive_happy_path():
    """The headline spec requirement: auto(config) == interactive answer set."""
    w = Wizard()
    w.answer("target", HAPPY["target"])
    w.answer("edition", HAPPY["edition"])
    w.answer("layout", HAPPY["layout"])
    w.answer("user", HAPPY["user"])
    w.answer("network", HAPPY["network"])
    interactive = w.confirm()
    assert build_plan(HAPPY).to_dict() == interactive.to_dict()


def test_auto_equals_interactive_static_full_variant():
    w = Wizard()
    w.answer("target", FULL["target"])
    w.answer("edition", FULL["edition"])
    w.answer("layout", FULL["layout"])
    w.answer("user", FULL["user"])
    w.answer("network", FULL["network"])
    interactive = w.confirm()
    assert build_plan(FULL).to_dict() == interactive.to_dict()


def test_auto_accepts_bare_scalar_and_mapping_forms():
    # Bare scalar user + bare scalar network (as a person types them).
    plan = build_plan(HAPPY)
    assert plan.to_dict()["user"]["username"] == "alice"
    # Mapping user + mapping network (as a fleet tool emits them).
    plan = build_plan(FULL)
    assert plan.to_dict()["user"]["username"] == "root-admin"
    net = plan.to_dict()["network"]
    assert net["mode"] == "static"
    assert net["address"] == "10.0.0.5/24"
    assert net["gateway"] == "10.0.0.1"
    assert net["nameservers"] == ["1.1.1.1", "8.8.8.8"]


def test_auto_normalises_case_and_whitespace_like_interactive():
    cfg = {**HAPPY, "edition": "  CORE  ", "layout": "AB-SPLIT"}
    plan = build_plan(cfg)
    assert plan.edition == "core"
    assert plan.layout == "ab-split"


def test_auto_ignores_unknown_keys():
    # A config file may carry comments/metadata keys we don't model; ignore them.
    cfg = {**HAPPY, "note": "fleet node 7", "provisioned": True}
    assert build_plan(cfg).to_dict() == build_plan(HAPPY).to_dict()


def test_auto_requires_every_field():
    for missing in STEPS:
        cfg = {k: v for k, v in HAPPY.items() if k != missing}
        with pytest.raises(ConfigError) as e:
            build_plan(cfg)
        assert missing in e.value.message  # the error names the missing field


def test_auto_invalid_field_raises_validation_error():
    with pytest.raises(ValidationError) as e:
        build_plan({**HAPPY, "edition": "desktop"})
    assert "edition" in e.value.step or "edition" in e.value.message


def test_auto_invalid_target_path_raises_validation_error():
    with pytest.raises(ValidationError):
        build_plan({**HAPPY, "target": "/tmp/root"})


def test_auto_probe_can_reject_target():
    def probe(path):
        return "not a block device"  # reject whatever auto feeds it

    with pytest.raises(ValidationError):
        build_plan(HAPPY, probe=probe)


def test_auto_probe_can_accept_target():
    def probe(path):
        return None

    assert build_plan(HAPPY, probe=probe).to_dict() == build_plan(HAPPY).to_dict()


# ---------------------------------------------------------------------------
# read_config / parse_config: file reading and error reporting
# ---------------------------------------------------------------------------


def _write(tmp_path, text, name="install.toml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_read_config_parses_toml(tmp_path):
    path = _write(
        tmp_path,
        "target = \"/dev/sdb\"\n"
        'edition = "core"\n'
        'layout = "single-root"\n'
        'user = "alice"\n'
        'network = "dhcp"\n',
    )
    data = read_config(path)
    assert data["target"] == "/dev/sdb"
    assert data["network"] == "dhcp"


def test_parse_config_end_to_end(tmp_path):
    path = _write(
        tmp_path,
        "target = \"/dev/sdb\"\n"
        'edition = "core"\n'
        'layout = "single-root"\n'
        'user = "alice"\n'
        'network = "dhcp"\n',
    )
    plan = parse_config(path)
    assert plan.to_dict() == build_plan(HAPPY).to_dict()


def test_parse_config_static_variant(tmp_path):
    path = _write(
        tmp_path,
        'target = "/dev/nvme0n1"\n'
        'edition = "server"\n'
        'layout = "ab-split"\n'
        '[user]\n'
        'username = "root-admin"\n'
        'uid = 1000\n'
        '[network]\n'
        'mode = "static"\n'
        'address = "10.0.0.5/24"\n'
        'gateway = "10.0.0.1"\n'
        'nameservers = ["1.1.1.1", "8.8.8.8"]\n',
    )
    plan = parse_config(path)
    assert plan.to_dict() == build_plan(FULL).to_dict()


def test_parse_config_missing_file_reports_error_not_crash():
    with pytest.raises(ConfigError) as e:
        parse_config("/no/such/install.toml")
    assert "not found" in e.value.message
    assert e.value.path == "/no/such/install.toml"


def test_parse_config_invalid_toml_reports_error_not_crash(tmp_path):
    path = _write(tmp_path, "target = /dev/sdb\nthis is = = not valid toml\n")
    with pytest.raises(ConfigError) as e:
        parse_config(path)
    assert "invalid TOML" in e.value.message
    assert e.value.path == path


def test_build_plan_rejects_non_table():
    # A non-mapping config is a reported ConfigError, never a crash. (Valid
    # TOML always parses to a table, so this guards the build_plan contract.)
    with pytest.raises(ConfigError) as e:
        build_plan([1, 2, 3])  # type: ignore[arg-type]
    assert "table" in e.value.message


def test_parse_config_invalid_field_reports_error_not_crash(tmp_path):
    path = _write(
        tmp_path,
        'target = "/dev/sdb"\n'
        'edition = "desktop"\n'  # invalid edition
        'layout = "single-root"\n'
        'user = "alice"\n'
        'network = "dhcp"\n',
    )
    with pytest.raises(ValidationError) as e:
        parse_config(path)
    assert "edition" in e.value.message


def test_read_config_reports_path_on_missing_file():
    with pytest.raises(ConfigError) as e:
        read_config("/no/such/file.toml")
    assert e.value.path == "/no/such/file.toml"


# ---------------------------------------------------------------------------
# plan_to_json — the machine-readable form
# ---------------------------------------------------------------------------


def test_plan_to_json_matches_to_dict():
    plan = build_plan(HAPPY)
    parsed = json.loads(plan_to_json(plan))
    assert parsed == plan.to_dict()


def test_plan_to_json_is_stable():
    assert plan_to_json(build_plan(HAPPY)) == plan_to_json(build_plan(HAPPY))


# ---------------------------------------------------------------------------
# CLI: ``auto --config install.toml [--json]``
# ---------------------------------------------------------------------------


def _run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as exc:  # argparse surface errors
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def test_cli_auto_prints_plan_lines(tmp_path):
    path = _write(
        tmp_path,
        'target = "/dev/sdb"\n'
        'edition = "core"\n'
        'layout = "single-root"\n'
        'user = "alice"\n'
        'network = "dhcp"\n',
    )
    code, out, err = _run_cli("auto", "--config", path)
    assert code == ExitCode.OK
    assert "target:   /dev/sdb" in out
    assert "edition:  core" in out
    assert "user:     alice (uid 1000)" in out
    assert "network:  dhcp" in out


def test_cli_auto_json(tmp_path):
    path = _write(
        tmp_path,
        'target = "/dev/sdb"\n'
        'edition = "core"\n'
        'layout = "single-root"\n'
        'user = "alice"\n'
        'network = "dhcp"\n',
    )
    code, out, err = _run_cli("auto", "--config", path, "--json")
    assert code == ExitCode.OK
    parsed = json.loads(out)
    assert parsed == build_plan(HAPPY).to_dict()


def test_cli_auto_missing_file_is_error_not_crash():
    code, out, err = _run_cli("auto", "--config", "/no/such/install.toml")
    assert code == ExitCode.ERROR
    assert "not found" in err


def test_cli_auto_invalid_toml_is_error_not_crash(tmp_path):
    path = _write(tmp_path, "this = = broken")
    code, out, err = _run_cli("auto", "--config", path)
    assert code == ExitCode.ERROR
    assert "invalid TOML" in err


def test_cli_auto_invalid_field_is_error_not_crash(tmp_path):
    path = _write(
        tmp_path,
        'target = "/dev/sdb"\n'
        'edition = "desktop"\n'
        'layout = "single-root"\n'
        'user = "alice"\n'
        'network = "dhcp"\n',
    )
    code, out, err = _run_cli("auto", "--config", path)
    assert code == ExitCode.ERROR
    assert "edition" in err


def test_cli_auto_missing_field_is_error_not_crash(tmp_path):
    path = _write(
        tmp_path,
        'target = "/dev/sdb"\n'
        'edition = "core"\n'
        'layout = "single-root"\n'
        'user = "alice"\n',  # network missing
    )
    code, out, err = _run_cli("auto", "--config", path)
    assert code == ExitCode.ERROR
    assert "network" in err


def test_cli_auto_default_config_path(tmp_path, monkeypatch):
    # With no --config, auto reads ./install.toml.
    (tmp_path / "install.toml").write_text(
        'target = "/dev/sdb"\n'
        'edition = "core"\n'
        'layout = "single-root"\n'
        'user = "alice"\n'
        'network = "dhcp"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    code, out, err = _run_cli("auto")
    assert code == ExitCode.OK
    assert "target:   /dev/sdb" in out


# ---------------------------------------------------------------------------
# Purity: build_plan performs no I/O beyond the injected probe
# ---------------------------------------------------------------------------


def test_build_plan_reads_no_environment():
    import os

    before = dict(os.environ)
    build_plan(HAPPY)
    assert os.environ == before
