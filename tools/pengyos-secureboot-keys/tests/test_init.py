"""Tests for the ``init`` command: key-set materialization + perms policy.

``init`` must create the output directory, write one file per role in the key
set, and enforce the ``0600``/``0644`` permission policy. It must also fail
closed rather than leak key material.
"""

import os
import stat
from pathlib import Path

import pytest

from pengyos_sbkeys import cli
from pengyos_sbkeys.keys import KEY_FILES, is_private, perms_for


@pytest.fixture(autouse=True)
def _fake_generate_all(monkeypatch):
    """Keep ``init`` openssl-free.

    ``init`` now produces real key material via ``openssl`` (through
    ``pengyos_sbkeys.generate.generate_all``). These tests exercise ``init``'s
    own contract -- directory/permission handling and the summary output --
    without invoking a real ``openssl``, so ``generate_all`` is replaced with a
    stand-in that writes dummy PEM files. Real generation is covered in
    ``test_generate.py`` (unit, mocked ``subprocess.run``; and an opt-in
    ``PENGYOS_IT`` integration test against real ``openssl``).
    """

    def _fake(keys_dir):
        keys_dir = Path(keys_dir)
        keys_dir.mkdir(parents=True, exist_ok=True)
        for key_file in KEY_FILES:
            prefix = (
                b"-----BEGIN PRIVATE KEY-----FAKE\n"
                if is_private(key_file)
                else b"-----BEGIN CERTIFICATE-----FAKE\n"
            )
            (keys_dir / key_file.name).write_bytes(prefix)

    monkeypatch.setattr(cli, "generate_all", _fake)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_init_creates_all_key_files(tmp_path):
    rc = cli.main(["init", "--out", str(tmp_path / "keys")])
    assert rc == 0
    for key_file in KEY_FILES:
        assert (tmp_path / "keys" / key_file.name).is_file()


def test_init_files_are_nonempty(tmp_path):
    out = tmp_path / "keys"
    assert cli.main(["init", "--out", str(out)]) == 0
    for key_file in KEY_FILES:
        data = (out / key_file.name).read_bytes()
        assert len(data) > 0


def test_init_applies_private_key_0600(tmp_path):
    out = tmp_path / "keys"
    assert cli.main(["init", "--out", str(out)]) == 0
    for key_file in KEY_FILES:
        path = out / key_file.name
        if is_private(key_file):
            assert _mode(path) == 0o600, f"{key_file.name} must be 0600"
        else:
            assert _mode(path) == 0o644, f"{key_file.name} must be 0644"


def test_init_enforces_policy_even_under_permissive_umask(tmp_path):
    # A permissive umask must not loosen the private keys: chmod sets exact bits.
    old_umask = os.umask(0o000)
    try:
        out = tmp_path / "keys"
        assert cli.main(["init", "--out", str(out)]) == 0
        for key_file in KEY_FILES:
            path = out / key_file.name
            assert _mode(path) == perms_for(key_file), f"{key_file.name} perms"
    finally:
        os.umask(old_umask)


def test_init_creates_key_dir_at_0700(tmp_path):
    out = tmp_path / "keys"
    assert cli.main(["init", "--out", str(out)]) == 0
    assert _mode(out) == 0o700


def test_init_does_not_touch_perms_of_preexisting_dir(tmp_path):
    out = tmp_path / "keys"
    out.mkdir()
    os.chmod(out, 0o755)
    assert cli.main(["init", "--out", str(out)]) == 0
    # We created the dir ourselves, so it should be tightened; but a dir the
    # caller passed in must keep its perms. Here we pre-created it.
    assert _mode(out) == 0o755
    # Files still get the correct perms regardless.
    for key_file in KEY_FILES:
        assert _mode(out / key_file.name) == perms_for(key_file)


def test_init_refuses_when_out_is_a_file(tmp_path):
    a_file = tmp_path / "not_a_dir"
    a_file.write_text("nope")
    rc = cli.main(["init", "--out", str(a_file)])
    assert rc == 1


def test_init_prints_summary_without_key_material(tmp_path, capsys):
    out = tmp_path / "keys"
    assert cli.main(["init", "--out", str(out)]) == 0

    captured = capsys.readouterr()
    # Summary must name every generated file.
    for key_file in KEY_FILES:
        assert key_file.name in captured.out

    # The raw key bytes must never be printed. Decode the file contents as
    # latin-1 (a 1:1 byte mapping) so any leaked material would show up verbatim
    # in the already-decoded stdout.
    leaked = False
    for key_file in KEY_FILES:
        blob = (out / key_file.name).read_bytes().decode("latin-1")
        if blob in captured.out:
            leaked = True
    assert not leaked, "init must not print raw key material"


def test_init_calls_generate_all_for_real_key_material(tmp_path, monkeypatch):
    # The whole point of this subtask: ``init`` delegates materialization to
    # ``generate_all`` (real openssl), rather than writing random bytes.
    calls = []

    def _spy(keys_dir):
        calls.append(Path(keys_dir))
        keys_dir = Path(keys_dir)
        keys_dir.mkdir(parents=True, exist_ok=True)
        for key_file in KEY_FILES:
            (keys_dir / key_file.name).write_bytes(b"-----BEGIN PRIVATE KEY-----\n")

    monkeypatch.setattr(cli, "generate_all", _spy)
    out = tmp_path / "keys"
    assert cli.main(["init", "--out", str(out)]) == 0
    assert calls == [out]
    for key_file in KEY_FILES:
        assert (out / key_file.name).is_file()
