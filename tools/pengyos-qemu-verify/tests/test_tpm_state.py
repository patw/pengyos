"""Tests for TPM state provisioning.

Covers, without requiring ``swtpm`` or ``swtpm_setup`` installed (fakes and
the committed state fixture stand in for the real tools):

* the pure ``swtpm_setup`` argv builder (golden strings + validation);
* binary discovery and the committed-fixture locator;
* the provisioning policy — existing state kept, ``swtpm_setup`` run when
  available (fake that records its argv), failure fall-through to the
  fixture, the committed-fixture seed, and the no-source error;
* the real committed fixture itself (present, non-trivial, has state files).
"""

import os
import stat
from pathlib import Path

import pytest

from pengyos_qemu_verify import tpm_state as ts

FIXTURES = Path(__file__).resolve().parent / "fixtures"
COMMITTED_FIXTURE = FIXTURES / "tpmstate"


# A fake swtpm_setup: records its argv (one token per line) to
# $FAKE_SETUP_ARGV and, when FAKE_SETUP_WRITE_STATE=1, writes a state file
# into the --tpmstate dir=. Exit code from FAKE_SETUP_EXIT (default 0).
FAKE_SETUP = r'''#!/usr/bin/env python3
import os, sys
rec = os.environ.get("FAKE_SETUP_ARGV")
if rec:
    with open(rec, "w") as fh:
        fh.write("\n".join(sys.argv) + "\n")
args = sys.argv[1:]
if os.environ.get("FAKE_SETUP_WRITE_STATE", "1") == "1":
    for i, a in enumerate(args):
        if a == "--tpmstate" and i + 1 < len(args):
            d = args[i + 1].split("dir=", 1)[1]
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "tpm2-00.permall"), "wb") as fh:
                fh.write(b"FAKESTATE" + b"\x00" * 16)
sys.exit(int(os.environ.get("FAKE_SETUP_EXIT", "0")))
'''


def _write_exec(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


def _with_env(tmp_path, mapping):
    """Set env vars, remembering prior values (process-global — clean up)."""
    saved = {}
    for k, v in mapping.items():
        saved[k] = os.environ.get(k)
        os.environ[k] = v
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ==========================================================================
# build_swtpm_setup_argv — pure golden argv
# ==========================================================================


def test_swtpm_setup_argv_golden():
    argv = ts.build_swtpm_setup_argv("/var/lib/tpm/state")
    assert argv == ["swtpm_setup", "--tpm2", "--tpmstate", "dir=/var/lib/tpm/state"]


def test_swtpm_setup_argv_binary_and_extra():
    argv = ts.build_swtpm_setup_argv(
        "/s", swtpm_setup_bin="/usr/local/bin/swtpm_setup",
        extra=("--key", "file=/ek.key"),
    )
    assert argv == [
        "/usr/local/bin/swtpm_setup", "--tpm2", "--tpmstate", "dir=/s",
        "--key", "file=/ek.key",
    ]


def test_swtpm_setup_argv_validation():
    with pytest.raises(ValueError):
        ts.build_swtpm_setup_argv("")
    with pytest.raises(ValueError):
        ts.build_swtpm_setup_argv("/s", swtpm_setup_bin="")


# ==========================================================================
# binary discovery + committed fixture locator
# ==========================================================================


def test_find_swtpm_setup_bin_returns_path_or_none(tmp_path, monkeypatch):
    # Absent: a PATH with only a temp dir yields None (never raises).
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    assert ts.find_swtpm_setup_bin() is None

    # Present: a real file on PATH is found.
    fake = _write_exec(tmp_path, "swtpm_setup", FAKE_SETUP)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{empty_bin}")
    assert ts.find_swtpm_setup_bin() == fake


def test_default_fixture_dir_is_the_committed_one():
    # The repo must always carry the committed fixture (fresh-clone safety).
    fixture = ts.default_fixture_dir()
    assert Path(fixture).is_dir()
    assert Path(fixture) == COMMITTED_FIXTURE
    assert ts.has_tpm_state(fixture)


def test_committed_fixture_is_real_state():
    # The fixture holds a swtpm-generated state file — non-trivial bytes,
    # no runtime .lock file (it would not be loadable elsewhere).
    names = sorted(p.name for p in COMMITTED_FIXTURE.iterdir())
    assert any(n.startswith("tpm2-") for n in names)
    assert ".lock" not in names
    state = next(p for p in COMMITTED_FIXTURE.iterdir() if p.name.startswith("tpm2-"))
    data = state.read_bytes()
    assert len(data) > 64  # a real permall blob, not a placeholder
    # The generator script ships with the fixture (the regeneration path).
    assert (COMMITTED_FIXTURE / "GENERATE.py").is_file()


# ==========================================================================
# has_tpm_state
# ==========================================================================


def test_has_tpm_state(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    assert ts.has_tpm_state(str(d)) is False

    (d / "swtpm.lock").touch()          # runtime lock: not state
    assert ts.has_tpm_state(str(d)) is False
    (d / "TMP2-00.permall").touch()     # swtpm temp file: not state
    assert ts.has_tpm_state(str(d)) is False

    (d / "tpm2-00.permall").write_bytes(b"\x00")
    assert ts.has_tpm_state(str(d)) is True
    assert ts.has_tpm_state(d) is True  # Path accepted

    tpm12 = tmp_path / "t12"
    tpm12.mkdir()
    (tpm12 / "tpm-00.permall").write_bytes(b"\x00")
    assert ts.has_tpm_state(str(tpm12)) is True


def test_has_tpm_state_missing_dir_raises(tmp_path):
    with pytest.raises(ts.TpmStateError):
        ts.has_tpm_state(str(tmp_path / "nope"))


# ==========================================================================
# provision_tpm_state — the policy
# ==========================================================================


def test_provision_existing_state_is_kept(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    marker = b"KEEP-ME"
    (d / "tpm2-00.permall").write_bytes(marker)
    got = ts.provision_tpm_state(str(d), swtpm_setup_bin="")
    assert got.method == "existing"
    assert got.state_dir == str(d.resolve())
    assert (d / "tpm2-00.permall").read_bytes() == marker  # untouched


def test_provision_runs_swtpm_setup(tmp_path):
    fake = _write_exec(tmp_path, "swtpm_setup", FAKE_SETUP)
    rec = tmp_path / "recorded-argv"
    d = tmp_path / "state"
    saved = _with_env(tmp_path, {"FAKE_SETUP_ARGV": str(rec)})
    try:
        got = ts.provision_tpm_state(
            str(d), swtpm_setup_bin=fake, fixture_dir=str(tmp_path / "unused")
        )
    finally:
        _restore(saved)
    assert got.method == "swtpm_setup"
    assert (d / "tpm2-00.permall").is_file()
    # The exact golden command line was executed.
    recorded = rec.read_text().splitlines()
    assert recorded[1:] == ["--tpm2", "--tpmstate", f"dir={d.resolve()}"]


def test_provision_setup_failure_falls_back_to_fixture(tmp_path):
    fake = _write_exec(tmp_path, "swtpm_setup", FAKE_SETUP)
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "tpm2-00.permall").write_bytes(b"FIXTURE-BYTES")
    d = tmp_path / "state"

    saved = _with_env(tmp_path, {"FAKE_SETUP_EXIT": "3"})
    try:
        got = ts.provision_tpm_state(
            str(d), swtpm_setup_bin=fake, fixture_dir=str(fixture)
        )
    finally:
        _restore(saved)
    assert got.method == "fixture"
    assert (d / "tpm2-00.permall").read_bytes() == b"FIXTURE-BYTES"
    assert "swtpm_setup failed" in got.detail  # the attempt is recorded


def test_provision_setup_success_without_state_falls_back_to_fixture(tmp_path):
    fake = _write_exec(tmp_path, "swtpm_setup", FAKE_SETUP)
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "tpm2-00.permall").write_bytes(b"FIXTURE-BYTES")
    d = tmp_path / "state"

    saved = _with_env(tmp_path, {"FAKE_SETUP_WRITE_STATE": "0"})
    try:
        got = ts.provision_tpm_state(
            str(d), swtpm_setup_bin=fake, fixture_dir=str(fixture)
        )
    finally:
        _restore(saved)
    assert got.method == "fixture"
    assert (d / "tpm2-00.permall").read_bytes() == b"FIXTURE-BYTES"
    assert "wrote no state files" in got.detail


def test_provision_seeds_committed_fixture_by_default(tmp_path):
    # No swtpm_setup (forced empty) -> the committed repo fixture is used.
    d = tmp_path / "state"
    got = ts.provision_tpm_state(str(d), swtpm_setup_bin="")
    assert got.method == "fixture"
    src = next(COMMITTED_FIXTURE.glob("tpm2-*"))
    dst = next(d.glob("tpm2-*"))
    assert dst.read_bytes() == src.read_bytes()  # real bytes, byte-for-byte
    # The fixture itself is never mutated.
    assert sorted(p.name for p in COMMITTED_FIXTURE.iterdir()) == sorted(
        p.name for p in (COMMITTED_FIXTURE / "tpm2-00.permall",
                         COMMITTED_FIXTURE / "GENERATE.py")
    )
    assert not (COMMITTED_FIXTURE / ".lock").exists()


def test_provision_explicit_fixture_dir(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "tpm2-00.permall").write_bytes(b"A")
    (fixture / "tpm2-00.save").write_bytes(b"B")
    (fixture / "notes.txt").write_text("not state\n")
    (fixture / ".lock").touch()
    d = tmp_path / "state"
    got = ts.provision_tpm_state(str(d), swtpm_setup_bin="", fixture_dir=str(fixture))
    assert got.method == "fixture"
    names = sorted(p.name for p in d.iterdir())
    assert names == ["tpm2-00.permall", "tpm2-00.save"]  # state only
    assert (d / "tpm2-00.permall").read_bytes() == b"A"
    assert (d / "tpm2-00.save").read_bytes() == b"B"
    # The fixture is copied, not moved/locked.
    assert (fixture / "tpm2-00.permall").read_bytes() == b"A"


def test_provision_no_source_raises(tmp_path):
    with pytest.raises(ts.TpmStateError):
        ts.provision_tpm_state(
            str(tmp_path / "state"),
            swtpm_setup_bin="",
            fixture_dir=str(tmp_path / "missing-fixture"),
        )
    # A fixture dir with no state files is a no-source too.
    empty = tmp_path / "empty-fixture"
    empty.mkdir()
    with pytest.raises(ts.TpmStateError):
        ts.provision_tpm_state(
            str(tmp_path / "state2"), swtpm_setup_bin="", fixture_dir=str(empty)
        )


def test_provision_creates_state_dir(tmp_path):
    d = tmp_path / "deep" / "nested" / "state"
    ts.provision_tpm_state(str(d), swtpm_setup_bin="")
    assert d.is_dir()
    assert next(d.glob("tpm2-*")) is not None


def test_provision_validation():
    with pytest.raises(ValueError):
        ts.provision_tpm_state("")
    with pytest.raises(ValueError):
        ts.provision_tpm_state("/x", setup_timeout=0)
    with pytest.raises(ValueError):
        ts.provision_tpm_state("/x", setup_timeout=-1)
