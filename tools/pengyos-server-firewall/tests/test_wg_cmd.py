"""Tests for the ``wg`` CLI command (:mod:`pengyos_netctl.cli`).

The ``wg`` command renders a ``wg0`` server config plus one config per peer
from a WireGuard spec, injecting keys from the spec's ``key_dir`` at mode
``0600`` and never echoing private-key material. These tests cover the CLI
surface (exit codes, stdout, flags, module invocation) on top of the
per-test coverage in :mod:`tests.test_wireguard`.
"""

import os
import stat
import subprocess
import sys

import pytest

from pengyos_netctl.cli import main

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_wg_spec(tmp_path, *, with_peer_key=True):
    lines = [
        "key_dir: keys",
        "server:",
        "  private_key: server.private",
        "  address: 10.8.0.1/24",
        "  listen_port: 51820",
        "  dns: 10.8.0.1",
        "peers:",
        "  - name: alice",
        "    address: 10.8.0.2/24",
    ]
    if with_peer_key:
        lines.append("    private_key: alice.private")
    lines += [
        "  - name: bob",
        "    address: 10.8.0.3/24",
    ]
    if with_peer_key:
        lines.append("    private_key: bob.private")
    spec = tmp_path / "wg.yaml"
    spec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(spec)


def test_wg_writes_server_and_peer_configs(capsys, tmp_path):
    spec = _write_wg_spec(tmp_path)
    out = str(tmp_path / "wg")
    assert main(["wg", "--spec", spec, "--out", out]) == 0
    for name in ("wg0.conf", "alice.conf", "bob.conf"):
        assert os.path.isfile(os.path.join(out, name)), name


def test_wg_reports_peers_and_key_dir(capsys, tmp_path):
    spec = _write_wg_spec(tmp_path)
    out = str(tmp_path / "wg")
    assert main(["wg", "--spec", spec, "--out", out]) == 0
    printed = capsys.readouterr().out
    assert "OK: wrote" in printed
    assert "wg0.conf" in printed
    assert "alice" in printed and "bob" in printed
    assert "0600" in printed


def test_wg_keys_are_chmod_0600(tmp_path):
    spec = _write_wg_spec(tmp_path)
    out = str(tmp_path / "wg")
    main(["wg", "--spec", spec, "--out", out])
    for key in (tmp_path / "keys").iterdir():
        mode = stat.S_IMODE(os.stat(str(key)).st_mode)
        assert mode == 0o600, f"{key.name} is {oct(mode)}"


def test_wg_no_private_key_leak(tmp_path):
    spec = _write_wg_spec(tmp_path)
    out = str(tmp_path / "wg")
    main(["wg", "--spec", spec, "--out", out])

    wg0 = (tmp_path / "wg" / "wg0.conf").read_text(encoding="utf-8")
    alice = (tmp_path / "wg" / "alice.conf").read_text(encoding="utf-8")
    bob = (tmp_path / "wg" / "bob.conf").read_text(encoding="utf-8")
    server_priv = (tmp_path / "keys" / "server.private").read_text(encoding="utf-8").strip()
    alice_priv = (tmp_path / "keys" / "alice.private").read_text(encoding="utf-8").strip()
    bob_priv = (tmp_path / "keys" / "bob.private").read_text(encoding="utf-8").strip()

    assert server_priv in wg0 and server_priv not in alice and server_priv not in bob
    assert alice_priv in alice and alice_priv not in wg0 and alice_priv not in bob
    assert bob_priv in bob and bob_priv not in wg0 and bob_priv not in alice


def test_wg_output_is_deterministic(tmp_path):
    spec = _write_wg_spec(tmp_path)
    out1 = str(tmp_path / "wg1")
    out2 = str(tmp_path / "wg2")
    main(["wg", "--spec", spec, "--out", out1])
    main(["wg", "--spec", spec, "--out", out2])
    assert (tmp_path / "wg1" / "wg0.conf").read_text() == (
        tmp_path / "wg2" / "wg0.conf"
    ).read_text()
    assert (tmp_path / "wg1" / "alice.conf").read_text() == (
        tmp_path / "wg2" / "alice.conf"
    ).read_text()


def test_wg_reuses_existing_keys_without_rewriting(tmp_path):
    spec = _write_wg_spec(tmp_path)
    out1 = str(tmp_path / "wg1")
    main(["wg", "--spec", spec, "--out", out1])
    first = {(k.name, k.stat().st_mtime_ns) for k in (tmp_path / "keys").iterdir()}
    main(["wg", "--spec", spec, "--out", str(tmp_path / "wg2")])
    second = {(k.name, k.stat().st_mtime_ns) for k in (tmp_path / "keys").iterdir()}
    assert first == second  # keys were not regenerated


def test_wg_bad_key_fails_cleanly(capsys, tmp_path):
    spec = _write_wg_spec(tmp_path)
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys" / "alice.private").write_text("NOT-A-KEY\n", encoding="utf-8")
    out = str(tmp_path / "wg")
    assert main(["wg", "--spec", spec, "--out", out]) == 2
    err = capsys.readouterr().err
    assert "error" in err.lower()
    assert not os.path.isfile(os.path.join(out, "wg0.conf"))


def test_wg_missing_spec_exits_two(capsys, tmp_path):
    out = str(tmp_path / "wg")
    assert main(["wg", "--spec", "/nonexistent/wg.yaml", "--out", out]) == 2
    err = capsys.readouterr().err
    assert "spec not found" in err
    assert not os.path.exists(out)


def test_wg_requires_spec_flag():
    with pytest.raises(SystemExit) as exc:
        main(["wg", "--out", "wg/"])
    assert exc.value.code == 2


def test_wg_requires_out_flag():
    with pytest.raises(SystemExit) as exc:
        main(["wg", "--spec", "wg.example.yaml"])
    assert exc.value.code == 2


def test_wg_help_lists_spec_and_out(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["wg", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--spec" in out
    assert "--out" in out


def test_wg_missing_out_dir_is_created(tmp_path):
    spec = _write_wg_spec(tmp_path)
    out = str(tmp_path / "does" / "not" / "exist" / "wg")
    assert main(["wg", "--spec", spec, "--out", out]) == 0
    assert os.path.isfile(os.path.join(out, "wg0.conf"))


def test_wg_invoked_as_module(tmp_path):
    spec = _write_wg_spec(tmp_path)
    out = str(tmp_path / "wg")
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_netctl", "wg",
         "--spec", spec, "--out", out],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK: wrote" in proc.stdout
    assert os.path.isfile(out + "/wg0.conf")
