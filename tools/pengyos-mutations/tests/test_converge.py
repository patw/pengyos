"""Tests for ``converge`` — turning a captured change into a declarative patch."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from pengyos_mutations import attest
from pengyos_mutations.converge import (
    ConvergeEntry,
    ConvergeError,
    ConvergeResult,
    converge,
    entries_for_change,
    emit_toml,
    render_entry,
    toml_escape,
)


def _apply_record(
    store, change_id, root, diff, desc="test change"
) -> None:
    """Append an apply record to a log so ``converge`` can find it."""
    attest.append_event(
        store / "events.jsonl",
        "apply",
        {
            "kind": "apply",
            "change_id": change_id,
            "desc": desc,
            "backend": "mock",
            "root": root.as_posix(),
            "command": ["bash", "-c", "echo hi"],
            "exit_code": 0,
            "diff": diff,
        },
    )


# --------------------------------------------------------------------------- #
# toml_escape
# --------------------------------------------------------------------------- #
def test_toml_escape_plain() -> None:
    assert toml_escape("hello") == "hello"


def test_toml_escape_specials() -> None:
    assert toml_escape('a"b\\c') == 'a\\"b\\\\c'


def test_toml_escape_newlines_and_tabs() -> None:
    assert toml_escape("x\ny\tz") == "x\\ny\\tz"


def test_toml_escape_control_char() -> None:
    assert toml_escape("a\x01b") == "a\\u0001b"


# --------------------------------------------------------------------------- #
# render_entry / emit_toml
# --------------------------------------------------------------------------- #
def test_render_entry_with_mode() -> None:
    entry = ConvergeEntry(
        path="/etc/a.conf", contents="v2\n", kind="modified", mode="0644"
    )
    text = render_entry(entry)
    assert text == (
        "[[file]]\n"
        'path = "/etc/a.conf"\n'
        'contents = "v2\\n"\n'
        'mode = "0644"'
    )


def test_render_entry_without_mode() -> None:
    entry = ConvergeEntry(path="/etc/a.conf", contents="v2", kind="created")
    text = render_entry(entry)
    assert "mode" not in text
    assert 'contents = "v2"' in text


def test_emit_toml_empty_has_header_only() -> None:
    result = ConvergeResult(
        change_id="c1", desc="d", root="/r", entries=[], skipped=[]
    )
    text = emit_toml(result)
    assert text.startswith("# Converged profile patch")
    assert "[[file]]" not in text
    assert text.endswith("\n")
    # Still valid TOML (an empty document).
    assert tomllib.loads(text) == {}


def test_emit_toml_roundtrips(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.conf").write_text("v2\n", encoding="utf-8")
    result = ConvergeResult(
        change_id="c1",
        desc="edit",
        root=root.as_posix(),
        entries=[
            ConvergeEntry(
                path=(root / "a.conf").as_posix(),
                contents="v2\n",
                kind="modified",
                mode="0644",
            )
        ],
        skipped=[],
    )
    doc = tomllib.loads(emit_toml(result))
    assert len(doc["file"]) == 1
    assert doc["file"][0] == {
        "path": (root / "a.conf").as_posix(),
        "contents": "v2\n",
        "mode": "0644",
    }


# --------------------------------------------------------------------------- #
# entries_for_change
# --------------------------------------------------------------------------- #
def test_entries_for_change_modified(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.conf").write_text("v2", encoding="utf-8")
    record = {"data": {"diff": [{"path": "a.conf", "kind": "modified"}]}}
    entries, skipped = entries_for_change(record, root)
    assert skipped == []
    assert len(entries) == 1
    assert entries[0].path == (root / "a.conf").as_posix()
    assert entries[0].contents == "v2"
    assert entries[0].kind == "modified"


def test_entries_for_change_created(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "new.conf").write_text("new", encoding="utf-8")
    record = {"data": {"diff": [{"path": "new.conf", "kind": "created"}]}}
    entries, skipped = entries_for_change(record, root)
    assert skipped == []
    assert entries[0].kind == "created"
    assert entries[0].contents == "new"


def test_entries_for_change_deleted_is_skipped(tmp_path) -> None:
    root = tmp_path / "root"
    record = {"data": {"diff": [{"path": "gone.conf", "kind": "deleted"}]}}
    entries, skipped = entries_for_change(record, root)
    assert entries == []
    assert skipped == [("gone.conf", "deletion is not declarative state")]


def test_entries_for_change_binary_is_skipped(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    # 0xff / 0xfe are invalid UTF-8 start bytes -> not decodable as text.
    (root / "blob.bin").write_bytes(b"\xff\xfe")
    record = {"data": {"diff": [{"path": "blob.bin", "kind": "created"}]}}
    entries, skipped = entries_for_change(record, root)
    assert entries == []
    assert len(skipped) == 1
    assert "binary" in skipped[0][1]


def test_entries_for_change_missing_is_skipped(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    record = {"data": {"diff": [{"path": "missing.conf", "kind": "created"}]}}
    entries, skipped = entries_for_change(record, root)
    assert entries == []
    assert len(skipped) == 1
    assert "no longer present" in skipped[0][1]


def test_entries_for_change_empty_diff(tmp_path) -> None:
    root = tmp_path / "root"
    record = {"data": {"diff": []}}
    entries, skipped = entries_for_change(record, root)
    assert entries == [] and skipped == []


# --------------------------------------------------------------------------- #
# converge (end to end, via a real log)
# --------------------------------------------------------------------------- #
def test_converge_writes_patch_for_modified_file(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.conf").write_text("v2", encoding="utf-8")
    store = tmp_path / "store"
    _apply_record(
        store, "c1", root, [{"path": "a.conf", "kind": "modified"}]
    )

    out = tmp_path / "patch.toml"
    result = converge("c1", str(out), store=str(store))

    assert isinstance(result, ConvergeResult)
    assert result.out == out
    assert len(result.entries) == 1
    assert result.entries[0].contents == "v2"
    assert result.skipped == []
    assert out.exists()

    doc = tomllib.loads(out.read_text(encoding="utf-8"))
    assert doc["file"][0]["contents"] == "v2"
    assert result.summary()  # non-empty


def test_converge_captures_mode(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    a = root / "a.conf"
    a.write_text("v2", encoding="utf-8")
    os.chmod(a, 0o640)
    store = tmp_path / "store"
    _apply_record(
        store, "c1", root, [{"path": "a.conf", "kind": "modified"}]
    )

    result = converge("c1", str(tmp_path / "p.toml"), store=str(store))
    assert result.entries[0].mode == "0640"


def test_converge_mixed_entries_and_skips(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "keep.conf").write_text("v2", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\xff\xfe")
    store = tmp_path / "store"
    _apply_record(
        store,
        "c1",
        root,
        [
            {"path": "keep.conf", "kind": "modified"},
            {"path": "blob.bin", "kind": "created"},
            {"path": "gone.conf", "kind": "deleted"},
        ],
    )

    result = converge("c1", str(tmp_path / "p.toml"), store=str(store))
    assert [e.path for e in result.entries] == [
        (root / "keep.conf").as_posix()
    ]
    skipped_paths = {p for p, _ in result.skipped}
    assert (root / "blob.bin").as_posix() in skipped_paths
    assert "gone.conf" in skipped_paths


def test_converge_unrecorded_raises(tmp_path) -> None:
    store = tmp_path / "store"
    with pytest.raises(ConvergeError, match="no recorded change"):
        converge("does-not-exist", str(tmp_path / "p.toml"), store=str(store))


def test_converge_rootless_record_raises(tmp_path) -> None:
    store = tmp_path / "store"
    attest.append_event(
        store / "events.jsonl",
        "apply",
        {"kind": "apply", "change_id": "c1", "diff": []},
    )
    with pytest.raises(ConvergeError, match="no tracked root"):
        converge("c1", str(tmp_path / "p.toml"), store=str(store))


def test_converge_empty_patch_when_nothing_changed(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    store = tmp_path / "store"
    _apply_record(store, "c1", root, [])

    out = tmp_path / "p.toml"
    result = converge("c1", str(out), store=str(store))
    assert result.entries == []
    assert result.skipped == []
    assert out.exists()
    tomllib.loads(out.read_text(encoding="utf-8"))  # valid, empty


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_converge_writes_patch(tmp_path, capsys) -> None:
    from pengyos_mutations.cli import main

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.conf").write_text("v2", encoding="utf-8")
    store = tmp_path / "store"
    _apply_record(
        store, "c1", root, [{"path": "a.conf", "kind": "modified"}]
    )

    out = tmp_path / "patch.toml"
    rc = main(["converge", "c1", "--out", str(out), "--store", str(store)])
    assert rc == 0
    assert out.exists()
    doc = tomllib.loads(out.read_text(encoding="utf-8"))
    assert doc["file"][0]["contents"] == "v2"
    assert "converged change" in capsys.readouterr().out


def test_cli_converge_unrecorded_returns_1(tmp_path, capsys) -> None:
    from pengyos_mutations.cli import main

    store = tmp_path / "store"
    rc = main(
        ["converge", "nope", "--out", str(tmp_path / "p.toml"),
         "--store", str(store)]
    )
    assert rc == 1
    assert "converge:" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Integration (opt-in)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    os.environ.get("PENGYOS_IT") != "1",
    reason="set PENGYOS_IT=1 to run integration tests",
)
def test_converge_via_cli_end_to_end(tmp_path) -> None:
    """A real apply -> converge cycle through the CLI produces a patch."""
    _REPO_ROOT = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.conf").write_text("v1", encoding="utf-8")
    store = tmp_path / "store"
    out = tmp_path / "patch.toml"

    apply = subprocess.run(
        [sys.executable, "-m", "pengyos_mutations", "apply", "--desc",
         "edit", "--store", str(store), "--root", str(root), "--",
         sys.executable, "-c", "open('a.conf','w').write('v2')"],
        cwd=str(root), capture_output=True, text=True, env=env,
    )
    assert apply.returncode == 0, apply.stderr

    change_id = attest.last_record(store / "events.jsonl")["data"]["change_id"]
    conv = subprocess.run(
        [sys.executable, "-m", "pengyos_mutations", "converge", change_id,
         "--out", str(out), "--store", str(store)],
        cwd=str(root), capture_output=True, text=True, env=env,
    )
    assert conv.returncode == 0, conv.stderr
    doc = tomllib.loads(out.read_text(encoding="utf-8"))
    assert doc["file"][0]["contents"] == "v2"
