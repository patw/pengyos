"""Tests for ``emit``.

Covers the ``emit`` deliverable:

* :func:`pengyos_desktop_curation.emit.flatpak_refs` — the ref list payload
  (one ref per line, trailing newline, empty set → empty string),
* :func:`pengyos_desktop_curation.emit._collection_id` — collection-id derivation
  from a Flathub id,
* :func:`pengyos_desktop_curation.emit.mirror_manifest` — the mirror manifest
  (remote / remote-name / apps / collections, ``UNPINNED`` placeholder),
* :func:`pengyos_desktop_curation.emit.emit` + :meth:`EmitResult.write` — build
  and persist the artifacts,
* determinism: two runs against the same data are byte-identical,
* the emitted manifest is valid TOML whose apps match the curated refs,
* the CLI ``emit`` writes the files (exit 0), renders ``--json``, and requires
  ``--out`` (usage error without it).

No network, root, display, or hardware is needed. The emitted manifest is a
*seed* (commit = ``UNPINNED``); this suite validates it as TOML + shape, not as
a commit-pinned manifest (that is the mirror tool's validation).
"""

from __future__ import annotations

import json
import tomllib

import pytest

from pengyos_desktop_curation import cli
from pengyos_desktop_curation import emit
from pengyos_desktop_curation.data import (
    CuratedSet,
    load_default,
    parse_entries,
)

# The committed set: one flatpak entry per everyday category.
SET = load_default()

# Two-entry set: two refs, one collection id (org.a) appears twice.
TWO = parse_entries(
    [
        {"category": "browser", "app": "A", "lane": "flatpak", "ref": "org.a.App", "rationale": "x"},
        {"category": "terminal", "app": "B", "lane": "flatpak", "ref": "org.b.Terminal", "rationale": "y"},
    ]
)

# Three flatpak entries where two share a collection id (org.a), to test
# de-duplication of the derived collections list.
SHARED_COLLECTION = parse_entries(
    [
        {"category": "browser", "app": "A", "lane": "flatpak", "ref": "org.a.App", "rationale": "x"},
        {"category": "terminal", "app": "B", "lane": "flatpak", "ref": "org.a.Other", "rationale": "y"},
        {"category": "files", "app": "C", "lane": "flatpak", "ref": "org.c.Files", "rationale": "z"},
    ]
)


# --- flatpak_refs -------------------------------------------------------------


def test_flatpak_refs_is_one_ref_per_line() -> None:
    text = emit.flatpak_refs(SET)
    assert text == "".join(f"{ref}\n" for ref in SET.flatpak_refs())
    assert text.count("\n") == len(SET.flatpak_refs()) == 10


def test_flatpak_refs_only_includes_flatpak_lane() -> None:
    mixed = parse_entries(
        [
            {"category": "browser", "app": "A", "lane": "flatpak", "ref": "org.a.App", "rationale": "x"},
            {"category": "terminal", "app": "B", "lane": "appimage", "rationale": "y"},
        ]
    )
    assert emit.flatpak_refs(mixed) == "org.a.App\n"


def test_flatpak_refs_empty_set_is_empty_string() -> None:
    assert emit.flatpak_refs(CuratedSet()) == ""


def test_flatpak_refs_source_order() -> None:
    refs = emit.flatpak_refs(SET).splitlines()
    assert refs[0] == "org.mozilla.FirefoxRepo"
    assert refs[-1] == "org.gnome.Screenshot"


# --- _collection_id -----------------------------------------------------------


def test_collection_id_derives_org_prefix() -> None:
    assert emit._collection_id("org.mozilla.FirefoxRepo") == "org.mozilla"
    assert emit._collection_id("org.gnome.Calculator") == "org.gnome"
    assert emit._collection_id("io.example.App") == "io.example"


def test_collection_id_none_for_non_flathub_id() -> None:
    assert emit._collection_id("not-a-ref") is None
    assert emit._collection_id("") is None


# --- mirror_manifest ----------------------------------------------------------


def test_manifest_has_remote_and_remote_name() -> None:
    manifest = emit.mirror_manifest(SET)
    assert f'remote = "{emit.MIRROR_REMOTE}"' in manifest
    assert f'remote-name = "{emit.MIRROR_REMOTE_NAME}"' in manifest


def test_manifest_apps_match_curated_refs_in_order() -> None:
    manifest = emit.mirror_manifest(SET)
    for ref in SET.flatpak_refs():
        assert f'id = "{ref}"' in manifest
    # Every flatpak ref appears as an apps[] entry, in source order.
    for ref in SET.flatpak_refs():
        assert f'{{ id = "{ref}", commit = "{emit.UNPINNED_COMMIT}"' in manifest


def test_manifest_uses_unpinned_placeholder() -> None:
    manifest = emit.mirror_manifest(SET)
    # One placeholder per flatpak entry.
    assert manifest.count(f'commit = "{emit.UNPINNED_COMMIT}"') == len(SET)


def test_manifest_derives_collection_ids() -> None:
    manifest = emit.mirror_manifest(TWO)
    assert 'collection-id = "org.a"' in manifest
    assert 'collection-id = "org.b"' in manifest


def test_manifest_collections_are_deduplicated_in_first_seen_order() -> None:
    manifest = emit.mirror_manifest(SHARED_COLLECTION)
    parsed = tomllib.loads(manifest)
    # org.a appears on two apps but once in the collections list; first-seen.
    assert parsed["collections"] == ["org.a", "org.c"]
    assert parsed["collections"].index("org.a") < parsed["collections"].index("org.c")


def test_manifest_is_valid_toml() -> None:
    """The emitted manifest is valid TOML (the mirror tool parses it)."""

    manifest = emit.mirror_manifest(SET)
    parsed = tomllib.loads(manifest)
    assert parsed["remote"] == emit.MIRROR_REMOTE
    assert parsed["remote-name"] == emit.MIRROR_REMOTE_NAME
    assert len(parsed["apps"]) == len(SET)
    # Each app entry carries id + the UNPINNED placeholder commit.
    for app in parsed["apps"]:
        assert app["commit"] == emit.UNPINNED_COMMIT
        assert app["id"] in SET.flatpak_refs()


def test_manifest_apps_ids_equal_flatpak_refs_order() -> None:
    """The apps[] ids in the manifest are exactly the flatpak refs, in order."""

    manifest = emit.mirror_manifest(SET)
    parsed = tomllib.loads(manifest)
    assert [app["id"] for app in parsed["apps"]] == SET.flatpak_refs()


def test_manifest_empty_set_has_empty_apps() -> None:
    manifest = emit.mirror_manifest(CuratedSet())
    parsed = tomllib.loads(manifest)
    assert parsed["apps"] == []
    assert parsed["collections"] == []


# --- emit() + EmitResult.write ------------------------------------------------


def test_emit_builds_payload() -> None:
    result = emit.emit(SET)
    assert result.flatpak_refs == emit.flatpak_refs(SET)
    assert result.manifest_toml == emit.mirror_manifest(SET)
    assert result.artifacts == (emit.FLATPAK_REFS_FILENAME, emit.MIRROR_MANIFEST_FILENAME)


def test_emit_result_writes_two_files(tmp_path):
    result = emit.emit(SET)
    written = result.write(tmp_path)
    assert [p.name for p in written] == list(result.artifacts)
    refs_file = tmp_path / emit.FLATPAK_REFS_FILENAME
    manifest_file = tmp_path / emit.MIRROR_MANIFEST_FILENAME
    assert refs_file.is_file() and manifest_file.is_file()
    assert refs_file.read_text(encoding="utf-8") == result.flatpak_refs
    assert manifest_file.read_text(encoding="utf-8") == result.manifest_toml
    # The written manifest parses as TOML.
    tomllib.loads(manifest_file.read_text(encoding="utf-8"))


def test_emit_result_write_creates_missing_dir(tmp_path):
    result = emit.emit(SET)
    nested = tmp_path / "a" / "b" / "c"
    written = result.write(nested)
    assert len(written) == 2
    assert (nested / emit.FLATPAK_REFS_FILENAME).is_file()


# --- determinism --------------------------------------------------------------


def test_emit_is_deterministic() -> None:
    """Two runs against the same data are byte-identical."""

    a = emit.emit(SET)
    b = emit.emit(SET)
    assert a.flatpak_refs == b.flatpak_refs
    assert a.manifest_toml == b.manifest_toml


# --- CLI: emit ---------------------------------------------------------------


def test_cli_emit_writes_files_and_exits_zero(capsys, tmp_path):
    out = tmp_path / "out"
    rc = cli.main(["emit", "--out", str(out)])
    assert rc == cli.EXIT_OK
    assert (out / emit.FLATPAK_REFS_FILENAME).is_file()
    assert (out / emit.MIRROR_MANIFEST_FILENAME).is_file()
    out_text = capsys.readouterr().out
    assert emit.FLATPAK_REFS_FILENAME in out_text
    assert emit.MIRROR_MANIFEST_FILENAME in out_text


def test_cli_emit_json_exits_zero(capsys, tmp_path):
    out = tmp_path / "out"
    rc = cli.main(["emit", "--out", str(out), "--json"])
    assert rc == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["artifacts"] == [emit.FLATPAK_REFS_FILENAME, emit.MIRROR_MANIFEST_FILENAME]
    assert payload["flatpak_refs"] == SET.flatpak_refs()
    assert isinstance(payload["manifest_toml"], str)


def test_cli_emit_requires_out(tmp_path):
    """``emit`` requires ``--out``; without it, an argparse usage error (exit 2)."""
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["emit"])
    assert exc.value.code == cli.EXIT_USAGE


def test_cli_emit_json_matches_written_manifest(capsys, tmp_path):
    """The ``--json`` manifest payload equals what is written to disk."""
    out = tmp_path / "out"
    cli.main(["emit", "--out", str(out), "--json"])
    payload = json.loads(capsys.readouterr().out)
    written = (out / emit.MIRROR_MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert payload["manifest_toml"] == written
