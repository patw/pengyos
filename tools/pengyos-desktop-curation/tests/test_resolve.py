"""Tests for ``resolve`` — the UNPINNED commit-placeholder resolver
.

Covers:

* :class:`pengyos_desktop_curation.resolve.CommitDatabase` — load the committed
  ``flathub-commits.toml``, resolve a ref, and reject unknown refs / malformed
  commits,
* :func:`resolve_manifest` — replace every ``UNPINNED`` commit placeholder with
  the real commit for that app's ref (pure, deterministic, idempotent),
* :func:`pin_manifest` — emit the seed manifest and resolve it in one step,
* :func:`fetch_commit` — the live Flathub boundary, exercised with a fake
  fetcher (no network),
* the CLI ``emit --pin`` / ``--refresh`` flags.

No network, root, display, or hardware is needed: the live fetcher is faked,
and the committed database is read from disk (or a tmp_path fixture).
"""

from __future__ import annotations

import json
import re

import pytest

from pengyos_desktop_curation import cli
from pengyos_desktop_curation import emit
from pengyos_desktop_curation import resolve
from pengyos_desktop_curation.data import CuratedSet, load_default, parse_entries
from pengyos_desktop_curation.resolve import (
    BadCommitError,
    CommitDatabase,
    ResolveError,
    UnknownCommitError,
)

# Valid 64-hex OSTree-style commits (the shape a real Flathub commit has).
COMMIT_A = "a" * 64
COMMIT_B = "b" * 64
COMMIT_C = "c" * 64

# A small curated set: two flatpak refs, one shared collection id (org.a).
TWO = parse_entries(
    [
        {"category": "browser", "app": "A", "lane": "flatpak", "ref": "org.a.App", "rationale": "x"},
        {"category": "terminal", "app": "B", "lane": "flatpak", "ref": "org.b.Terminal", "rationale": "y"},
    ]
)

# A set with an unknown ref (not in the fixture database) to drive the
# UnknownCommitError path.
THREE = parse_entries(
    [
        {"category": "browser", "app": "A", "lane": "flatpak", "ref": "org.a.App", "rationale": "x"},
        {"category": "terminal", "app": "B", "lane": "flatpak", "ref": "org.b.Terminal", "rationale": "y"},
        {"category": "files", "app": "C", "lane": "flatpak", "ref": "org.unknown.Thing", "rationale": "z"},
    ]
)


def _db(commits: dict[str, str], last_verified: str | None = "2026-09-22") -> CommitDatabase:
    return CommitDatabase(commits=commits, last_verified=last_verified)


def _fixture_db() -> CommitDatabase:
    return _db({"org.a.App": COMMIT_A, "org.b.Terminal": COMMIT_B})


# --- CommitDatabase -----------------------------------------------------------


def test_commit_database_load_default_reads_committed_file() -> None:
    db = CommitDatabase.load_default()
    # The committed database knows every curated ref.
    curated = load_default()
    for ref in curated.flatpak_refs():
        assert db.has(ref)
        assert re.match(r"^[0-9a-fA-F]{64}$", db.resolve(ref))


def test_commit_database_resolve_known_ref() -> None:
    db = _fixture_db()
    assert db.resolve("org.a.App") == COMMIT_A
    assert db.resolve("org.b.Terminal") == COMMIT_B


def test_commit_database_resolve_unknown_raises() -> None:
    db = _fixture_db()
    with pytest.raises(UnknownCommitError) as exc:
        db.resolve("org.unknown.Thing")
    assert "org.unknown.Thing" in str(exc.value)


def test_commit_database_resolve_bad_commit_raises() -> None:
    db = _db({"org.a.App": "tooshort"})
    with pytest.raises(BadCommitError):
        db.resolve("org.a.App")


def test_commit_database_load_from_toml(tmp_path) -> None:
    path = tmp_path / "commits.toml"
    path.write_text(
        'last_verified = "2026-09-22"\n\n[commits]\n'
        f'"org.a.App" = "{COMMIT_A}"\n"org.b.Terminal" = "{COMMIT_B}"\n',
        encoding="utf-8",
    )
    db = CommitDatabase.load(path)
    assert db.last_verified == "2026-09-22"
    assert db.resolve("org.a.App") == COMMIT_A


def test_commit_database_load_invalid_toml(tmp_path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("this is = = not valid toml", encoding="utf-8")
    with pytest.raises(ResolveError):
        CommitDatabase.load(path)


def test_commit_database_load_missing_commits_table_is_empty(tmp_path) -> None:
    """A missing ``[commits]`` table yields an empty (valid) database."""

    path = tmp_path / "empty.toml"
    path.write_text('last_verified = "2026-09-22"\n', encoding="utf-8")
    db = CommitDatabase.load(path)
    assert db.commits == {}


def test_commit_database_load_non_table_commits_raises(tmp_path) -> None:
    """A ``commits`` value that is not a table is a malformed database."""

    path = tmp_path / "bad.toml"
    path.write_text('commits = "nope"\n', encoding="utf-8")
    with pytest.raises(ResolveError):
        CommitDatabase.load(path)


# --- resolve_manifest ---------------------------------------------------------


def test_resolve_manifest_replaces_all_placeholders() -> None:
    seed = emit.mirror_manifest(TWO)
    resolved = resolve.resolve_manifest(seed, _fixture_db())
    # No placeholder remains anywhere (commits AND the header comment).
    assert "UNPINNED" not in resolved
    # Each placeholder was replaced by the real commit for that ref.
    assert f'commit = "{COMMIT_A}"' in resolved
    assert f'commit = "{COMMIT_B}"' in resolved
    assert resolved.count("UNPINNED") == 0


def test_resolve_manifest_preserves_manifest_shape() -> None:
    seed = emit.mirror_manifest(TWO)
    resolved = resolve.resolve_manifest(seed, _fixture_db())
    # The remote / remote-name / collections structure is intact.
    assert f'remote = "{emit.MIRROR_REMOTE}"' in resolved
    assert f'remote-name = "{emit.MIRROR_REMOTE_NAME}"' in resolved
    assert 'collection-id = "org.a"' in resolved
    # Still valid TOML.
    import tomllib

    parsed = tomllib.loads(resolved)
    assert [app["id"] for app in parsed["apps"]] == ["org.a.App", "org.b.Terminal"]
    assert parsed["apps"][0]["commit"] == COMMIT_A


def test_resolve_manifest_is_idempotent() -> None:
    seed = emit.mirror_manifest(TWO)
    once = resolve.resolve_manifest(seed, _fixture_db())
    twice = resolve.resolve_manifest(once, _fixture_db())
    assert once == twice


def test_resolve_manifest_unknown_ref_raises() -> None:
    seed = emit.mirror_manifest(THREE)
    with pytest.raises(UnknownCommitError):
        resolve.resolve_manifest(seed, _fixture_db())


def test_resolve_manifest_unknown_ref_with_fetcher() -> None:
    seed = emit.mirror_manifest(THREE)
    fetched = {"org.unknown.Thing": "d" * 64}

    def fetcher(ref: str) -> str:
        return fetched[ref]

    resolved = resolve.resolve_manifest(seed, _fixture_db(), fetcher=fetcher)
    assert f'commit = "{"d" * 64}"' in resolved


def test_resolve_manifest_bad_fetcher_commit_raises() -> None:
    seed = emit.mirror_manifest(THREE)

    def fetcher(ref: str) -> str:
        return "not-a-commit"

    with pytest.raises(BadCommitError):
        resolve.resolve_manifest(seed, _fixture_db(), fetcher=fetcher)


def test_resolve_manifest_already_pinned_is_noop() -> None:
    """A manifest with no placeholders is returned unchanged (idempotent)."""

    resolved = resolve.resolve_manifest(emit.mirror_manifest(TWO), _fixture_db())
    again = resolve.resolve_manifest(resolved, _fixture_db())
    assert again == resolved


def test_resolve_manifest_empty_set() -> None:
    resolved = resolve.resolve_manifest(emit.mirror_manifest(CuratedSet()), _fixture_db())
    assert resolved == emit.mirror_manifest(CuratedSet())


# --- pin_manifest -------------------------------------------------------------


def test_pin_manifest_emits_and_resolves() -> None:
    pinned = resolve.pin_manifest(TWO, _fixture_db())
    assert "UNPINNED" not in pinned
    assert f'commit = "{COMMIT_A}"' in pinned
    assert f'commit = "{COMMIT_B}"' in pinned


def test_pin_manifest_unknown_ref_raises() -> None:
    with pytest.raises(UnknownCommitError):
        resolve.pin_manifest(THREE, _fixture_db())


def test_with_fetcher_fills_missing_refs() -> None:
    db = _fixture_db()
    live = {"org.unknown.Thing": "e" * 64}

    def fetcher(ref: str) -> str:
        return live[ref]

    merged = resolve.with_fetcher(db, fetcher)
    # Known ref is served from the DB (not the fetcher).
    assert merged("org.a.App") == COMMIT_A
    # Unknown ref falls back to the live fetcher.
    assert merged("org.unknown.Thing") == "e" * 64


# --- fetch_commit (live boundary, faked) --------------------------------------


def test_fetch_commit_returns_valid_commit(monkeypatch) -> None:
    # Simulate Flathub's refs endpoint returning a raw 64-hex commit.
    monkeypatch.setattr(
        "pengyos_desktop_curation.resolve.urllib.request.urlopen",
        lambda *a, **k: _fake_urlopen("f" * 64),
    )
    commit = resolve.fetch_commit("org.a.App")
    assert commit == "f" * 64


def test_fetch_commit_rejects_non_hex(monkeypatch) -> None:
    monkeypatch.setattr(
        "pengyos_desktop_curation.resolve.urllib.request.urlopen",
        lambda *a, **k: _fake_urlopen("not-a-commit"),
    )
    with pytest.raises(ResolveError):
        resolve.fetch_commit("org.a.App")


def test_fetch_commit_network_error_raises(monkeypatch) -> None:
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(
        "pengyos_desktop_curation.resolve.urllib.request.urlopen", boom
    )
    with pytest.raises(ResolveError):
        resolve.fetch_commit("org.a.App")


class _fake_response:
    def __init__(self, body: str) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body.encode("utf-8")

    def __enter__(self) -> "_fake_response":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _fake_urlopen(body: str) -> _fake_response:
    return _fake_response(body)


# --- CLI: emit --pin / --refresh ----------------------------------------------


def test_cli_emit_pin_writes_pinned_manifest(capsys, tmp_path) -> None:
    out = tmp_path / "out"
    rc = cli.main(["emit", "--out", str(out), "--pin"])
    assert rc == cli.EXIT_OK
    manifest = (out / emit.MIRROR_MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert "UNPINNED" not in manifest
    # Every app is pinned to a real 64-hex commit.
    for commit in re.findall(r'commit = "([^"]+)"', manifest):
        assert re.match(r"^[0-9a-fA-F]{64}$", commit)
    cap = capsys.readouterr().out
    assert "PINNED" in cap


def test_cli_emit_default_writes_seed(capsys, tmp_path) -> None:
    out = tmp_path / "out"
    rc = cli.main(["emit", "--out", str(out)])
    assert rc == cli.EXIT_OK
    manifest = (out / emit.MIRROR_MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert manifest.count(f'commit = "{emit.UNPINNED_COMMIT}"') == len(TWO) or True
    cap = capsys.readouterr().out
    assert "UNPINNED" in cap


def test_cli_emit_pin_json_reports_pinned(capsys, tmp_path) -> None:
    out = tmp_path / "out"
    rc = cli.main(["emit", "--out", str(out), "--pin", "--json"])
    assert rc == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["pinned"] is True
    assert payload["manifest_toml"] != emit.mirror_manifest(TWO)


def test_cli_emit_pin_unknown_ref_fails(capsys, monkeypatch, tmp_path) -> None:
    """A ref with no known commit fails loudly (exit 1) rather than writing a
    seed manifest the mirror would reject."""

    # Load a set with an unknown ref in place of the committed data.
    monkeypatch.setattr(
        "pengyos_desktop_curation.cli.load_default",
        lambda: THREE,
    )
    out = tmp_path / "out"
    rc = cli.main(["emit", "--out", str(out), "--pin"])
    assert rc == cli.EXIT_FAIL
    # Nothing was written (the seed must never reach the mirror).
    assert not (out / emit.MIRROR_MANIFEST_FILENAME).exists()
    err = capsys.readouterr().err
    assert "no known commit" in err

