"""Tests for the ``emit`` I/O layer + CLI.

Covers the ``emit`` subcommand:

* :func:`~pengyos_desktop_session.session.write_session` writes the five
  artifacts under ``--out`` (creating the dir), in canonical (stable) order,
  with byte-for-byte deterministic content;
* re-emitting is idempotent (identical content written back), and files that are
  not part of the artifact set are left untouched (emit writes, it does not wipe
  the overlay);
* the ``emit`` CLI returns ``EXIT_OK``, writes the files, and prints a
  human-readable summary (or JSON with ``--json``);
* overrides flow through: a custom session renders override content to disk.
"""

import json
from pathlib import Path

from pengyos_desktop_session import cli
from pengyos_desktop_session import emit
from pengyos_desktop_session.model import CosmicSession
from pengyos_desktop_session.session import write_session, written_paths

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- write_session: files land on disk --------------------------------------


def test_write_session_writes_all_five(tmp_path):
    written = write_session(tmp_path)
    assert written == list(emit.ARTIFACT_ORDER)
    for name in emit.ARTIFACT_ORDER:
        assert (tmp_path / name).is_file(), f"{name} not written"


def test_write_session_creates_missing_dir(tmp_path):
    target = tmp_path / "nested" / "overlay"
    assert not target.exists()
    written = write_session(target)
    assert target.is_dir()
    assert written == list(emit.ARTIFACT_ORDER)


def test_write_session_content_matches_renderer(tmp_path):
    write_session(tmp_path)
    artifacts = emit.render_artifacts()
    for name, expected in artifacts.items():
        actual = (tmp_path / name).read_text(encoding="utf-8")
        assert actual == expected, name


def test_write_session_order_is_canonical(tmp_path):
    """write_session returns filenames in the canonical ARTIFACT_ORDER."""
    assert write_session(tmp_path) == list(emit.ARTIFACT_ORDER)


def test_written_paths_maps_name_to_file(tmp_path):
    write_session(tmp_path)
    paths = written_paths(tmp_path)
    assert set(paths) == set(emit.ARTIFACT_ORDER)
    assert (paths["greetd.toml"]).read_text().startswith("# Emitted by")


# --- determinism + idempotency ----------------------------------------------


def test_emit_is_deterministic_across_runs(tmp_path):
    """Two writes to two dirs yield byte-identical files."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    write_session(a)
    write_session(b)
    for name in emit.ARTIFACT_ORDER:
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_reemit_overwrites_with_identical_content(tmp_path):
    first = write_session(tmp_path)
    # Mutate a file, then re-emit: it must be restored byte-for-byte.
    (tmp_path / "greetd.toml").write_text("corrupted\n", encoding="utf-8")
    second = write_session(tmp_path)
    assert first == second == list(emit.ARTIFACT_ORDER)
    assert (tmp_path / "greetd.toml").read_text() == emit.render_greetd_config()


def test_reemit_does_not_delete_unrelated_files(tmp_path):
    """emit writes its files; it does not wipe the overlay."""
    write_session(tmp_path)
    (tmp_path / "keep.txt").write_text("leave me\n", encoding="utf-8")
    write_session(tmp_path)
    assert (tmp_path / "keep.txt").read_text() == "leave me\n"


def test_write_session_no_overwrite_skips_existing(tmp_path):
    write_session(tmp_path)
    original = (tmp_path / "cosmic.desktop").read_text()
    (tmp_path / "cosmic.desktop").write_text("custom\n", encoding="utf-8")
    written = write_session(tmp_path, overwrite=False)
    # the existing file is skipped (still in the returned list, but not
    # rewritten) — assert it was left as the caller wrote it.
    assert (tmp_path / "cosmic.desktop").read_text() == "custom\n"
    assert original != "custom\n"


# --- overrides flow through to disk -----------------------------------------


def test_emit_writes_override_content(tmp_path):
    session = CosmicSession(compositor="weston", cosmic_greeter_exec="weston")
    write_session(tmp_path, session=session)
    desktop = (tmp_path / "cosmic.desktop").read_text()
    assert 'Compositor = "weston"' in desktop
    assert 'Exec = "weston"' in desktop
    launcher = (tmp_path / "cosmic-session-launch.sh").read_text()
    assert "exec weston" in launcher


# --- emit CLI ---------------------------------------------------------------


def test_emit_cli_returns_ok_and_writes_files(tmp_path, capsys):
    out = tmp_path / "overlay"
    rc = cli.main(["emit", "--out", str(out)])
    assert rc == cli.EXIT_OK
    for name in emit.ARTIFACT_ORDER:
        assert (out / name).is_file(), f"{name} not written by CLI"


def test_emit_cli_prints_written_files(capsys, tmp_path):
    cli.main(["emit", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    for name in emit.ARTIFACT_ORDER:
        assert name in out


def test_emit_cli_json_is_valid_and_structured(tmp_path, capsys):
    out = tmp_path / "overlay"
    cli.main(["emit", "--out", str(out), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["out"] == str(out)
    assert payload["files"] == list(emit.ARTIFACT_ORDER)


def test_emit_cli_json_files_are_in_canonical_order(tmp_path, capsys):
    cli.main(["emit", "--out", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"] == sorted(payload["files"], key=emit.ARTIFACT_ORDER.index)


def test_emit_cli_json_without_writes_still_lists_all(tmp_path, capsys):
    """Even an empty/absent dir: emit writes all five and lists them."""
    cli.main(["emit", "--out", str(tmp_path / "fresh"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"] == list(emit.ARTIFACT_ORDER)


def test_emit_cli_creates_dir_if_absent(tmp_path, capsys):
    out = tmp_path / "fresh" / "overlay"
    assert not out.exists()
    rc = cli.main(["emit", "--out", str(out)])
    assert rc == cli.EXIT_OK
    assert out.is_dir()
    assert (out / "greetd.toml").is_file()
