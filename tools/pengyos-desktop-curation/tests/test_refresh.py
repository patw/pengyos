"""Tests for the commit-database persistence / refresh path
.

Covers:

* :func:`pengyos_desktop_curation.resolve.render` — pure, deterministic
  serialisation of a :class:`CommitDatabase` to committed-``flathub-commits.toml``
  text (header + ``last_verified`` + sorted ``[commits]`` table),
* :func:`pengyos_desktop_curation.resolve.write_db` — read-modify-write that
  preserves the existing file's header and refuses to clobber a corrupt file,
* :func:`pengyos_desktop_curation.resolve.refresh` — fetch every curated
  ``flatpak`` ref live, merge the fresh commits into the committed DB, and
  persist it back (self-update with real Flathub commits),
* the CLI ``emit --refresh`` wiring (fetched commits persisted, manifest resolved
  to fresh values, ``last_verified`` reported).

No network, root, display, or hardware is needed: the live Flathub boundary is
faked (``monkeypatch`` the ``urllib`` call), and the committed DB is pointed at a
``tmp_path`` file so the real ``flathub-commits.toml`` is never touched.
"""

from __future__ import annotations

import json
import re

import pytest
import tomllib

from pengyos_desktop_curation import cli
from pengyos_desktop_curation import resolve
from pengyos_desktop_curation.data import CuratedSet, parse_entries
from pengyos_desktop_curation.resolve import (
    BadCommitError,
    CommitDatabase,
    ResolveError,
)

COMMIT_A = "a" * 64
COMMIT_B = "b" * 64
COMMIT_C = "c" * 64
COMMIT_A2 = "d" * 64
COMMIT_B2 = "e" * 64

# A curated set with two flatpak refs.
TWO = parse_entries(
    [
        {"category": "browser", "app": "A", "lane": "flatpak", "ref": "org.a.App", "rationale": "x"},
        {"category": "terminal", "app": "B", "lane": "flatpak", "ref": "org.b.Terminal", "rationale": "y"},
    ]
)

# Same two refs, plus an appimage entry (which must be ignored by refresh).
TWO_AND_APPIMAGE = parse_entries(
    [
        {"category": "browser", "app": "A", "lane": "flatpak", "ref": "org.a.App", "rationale": "x"},
        {"category": "media", "app": "Img", "lane": "appimage", "rationale": "z"},
    ]
)


def _db(commits: dict[str, str], last_verified: str | None = "2026-09-01") -> CommitDatabase:
    return CommitDatabase(commits=commits, last_verified=last_verified)


def _write_toml(path, commits: dict[str, str], last_verified: str | None = "2026-09-01", header: str | None = None) -> None:
    header = header if header is not None else "# existing header\n"
    lines = [header.rstrip()]
    if last_verified is not None:
        lines.append(f'last_verified = "{last_verified}"')
    lines.append("")
    lines.append("[commits]")
    for ref in sorted(commits):
        lines.append(f'"{ref}" = "{commits[ref]}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- render -----------------------------------------------------------------


def test_render_includes_header_last_verified_and_commits() -> None:
    db = _db({"org.a.App": COMMIT_A, "org.b.Terminal": COMMIT_B})
    text = resolve.render(db, header="# h\n")
    assert "# h" in text
    assert 'last_verified = "2026-09-01"' in text
    assert '"org.a.App" = "%s"' % COMMIT_A in text
    assert '"org.b.Terminal" = "%s"' % COMMIT_B in text


def test_render_commits_are_sorted() -> None:
    db = _db({"org.z.Z": COMMIT_A, "org.a.A": COMMIT_B})
    text = resolve.render(db, header="")
    refs = re.findall(r'"([^"]+)" =', text)
    assert refs == sorted(refs) == ["org.a.A", "org.z.Z"]


def test_render_last_verified_none_drops_field() -> None:
    db = _db({"org.a.App": COMMIT_A}, last_verified=None)
    text = resolve.render(db, header="")
    assert "last_verified" not in text


def test_render_empty_commits_has_no_table() -> None:
    db = _db({}, last_verified=None)
    text = resolve.render(db, header="")
    assert text.strip() == ""


def test_render_is_valid_toml() -> None:
    db = _db({"org.a.App": COMMIT_A})
    parsed = tomllib.loads(resolve.render(db, header="# h\n"))
    assert parsed["last_verified"] == "2026-09-01"
    assert parsed["commits"]["org.a.App"] == COMMIT_A


def test_render_is_deterministic() -> None:
    db = _db({"org.a.App": COMMIT_A, "org.b.Terminal": COMMIT_B})
    assert resolve.render(db, header="# h\n") == resolve.render(db, header="# h\n")


# --- write_db ---------------------------------------------------------------


def test_write_db_writes_file(tmp_path) -> None:
    path = tmp_path / "flathub-commits.toml"
    db = _db({"org.a.App": COMMIT_A})
    resolve.write_db(path, db)
    assert path.is_file()
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["commits"]["org.a.App"] == COMMIT_A


def test_write_db_preserves_existing_header(tmp_path) -> None:
    path = tmp_path / "flathub-commits.toml"
    _write_toml(path, {"org.a.App": COMMIT_A}, header="# MY DOC HEAD\n# second\n")
    db = _db({"org.a.App": COMMIT_A2})
    resolve.write_db(path, db)
    text = path.read_text(encoding="utf-8")
    assert "# MY DOC HEAD" in text and "# second" in text
    # The commit value was updated.
    assert COMMIT_A2 in text and COMMIT_A not in text


def test_write_db_writes_default_header_when_missing(tmp_path) -> None:
    path = tmp_path / "flathub-commits.toml"
    db = _db({"org.a.App": COMMIT_A})
    resolve.write_db(path, db)
    text = path.read_text(encoding="utf-8")
    assert "Flathub commit database" in text


def test_write_db_refuses_corrupt_file(tmp_path) -> None:
    path = tmp_path / "flathub-commits.toml"
    path.write_text("this is = = not valid toml [[[", encoding="utf-8")
    db = _db({"org.a.App": COMMIT_A})
    with pytest.raises(ResolveError):
        resolve.write_db(path, db)
    # The corrupt file is left untouched.
    assert path.read_text(encoding="utf-8") == "this is = = not valid toml [[["


def test_write_db_refuses_non_table_commits(tmp_path) -> None:
    """A present-but-wrong-type ``commits`` value is rejected (not clobbered)."""

    path = tmp_path / "flathub-commits.toml"
    path.write_text('commits = "nope"\n', encoding="utf-8")
    db = _db({"org.a.App": COMMIT_A})
    with pytest.raises(ResolveError):
        resolve.write_db(path, db)
    assert path.read_text(encoding="utf-8") == 'commits = "nope"\n'


def test_write_db_missing_commits_table_is_empty(tmp_path) -> None:
    """A file with no ``[commits]`` table is treated as an empty (valid) DB
    (matching ``CommitDatabase.load``'s convention)."""

    path = tmp_path / "flathub-commits.toml"
    path.write_text('last_verified = "2026-09-01"\n', encoding="utf-8")
    db = _db({"org.a.App": COMMIT_A})
    resolve.write_db(path, db)
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["commits"] == {"org.a.App": COMMIT_A}


def test_write_db_preserves_non_curated_refs(tmp_path) -> None:
    """write_db writes exactly the DB passed to it; refs present in the DB are
    all written."""

    path = tmp_path / "flathub-commits.toml"
    db = _db({"org.a.App": COMMIT_A, "org.other.Thing": COMMIT_B})
    resolve.write_db(path, db)
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert set(parsed["commits"]) == {"org.a.App", "org.other.Thing"}


# --- refresh ----------------------------------------------------------------


def test_refresh_fetches_and_persists_all_curated_refs(tmp_path, monkeypatch) -> None:
    path = tmp_path / "flathub-commits.toml"
    _write_toml(path, {"org.a.App": COMMIT_A, "org.b.Terminal": COMMIT_B})

    live = {"org.a.App": COMMIT_A2, "org.b.Terminal": COMMIT_B2}

    def fetcher(ref: str) -> str:
        return live[ref]

    new_db = resolve.refresh(TWO, fetcher=fetcher, path=path, today="2026-09-22")
    assert new_db.last_verified == "2026-09-22"

    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["commits"]["org.a.App"] == COMMIT_A2
    assert parsed["commits"]["org.b.Terminal"] == COMMIT_B2
    assert parsed["last_verified"] == "2026-09-22"


def test_refresh_preserves_non_curated_refs_in_db(tmp_path, monkeypatch) -> None:
    """A ref in the DB that is not curated is preserved by the merge."""

    path = tmp_path / "flathub-commits.toml"
    _write_toml(path, {"org.a.App": COMMIT_A, "org.other.Thing": COMMIT_B})

    def fetcher(ref: str) -> str:
        return COMMIT_A2

    resolve.refresh(TWO, fetcher=fetcher, path=path, today="2026-09-22")
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert set(parsed["commits"]) == {"org.a.App", "org.b.Terminal", "org.other.Thing"}


def test_refresh_ignores_non_flatpak_refs(tmp_path) -> None:
    """An appimage entry contributes no commit; only flatpak refs are fetched."""

    path = tmp_path / "flathub-commits.toml"
    _write_toml(path, {"org.a.App": COMMIT_A})

    calls: list[str] = []

    def fetcher(ref: str) -> str:
        calls.append(ref)
        return COMMIT_A2

    resolve.refresh(TWO_AND_APPIMAGE, fetcher=fetcher, path=path, today="2026-09-22")
    assert calls == ["org.a.App"]  # the appimage ref was never fetched


def test_refresh_bad_commit_raises_and_writes_nothing(tmp_path) -> None:
    path = tmp_path / "flathub-commits.toml"
    _write_toml(path, {"org.a.App": COMMIT_A})
    before = path.read_text(encoding="utf-8")

    def fetcher(ref: str) -> str:
        return "not-a-commit"

    with pytest.raises(BadCommitError):
        resolve.refresh(TWO, fetcher=fetcher, path=path, today="2026-09-22")
    # The committed DB is left untouched (fetch fails before any write).
    assert path.read_text(encoding="utf-8") == before


def test_refresh_empty_curated_set_raises(tmp_path) -> None:
    path = tmp_path / "flathub-commits.toml"
    _write_toml(path, {"org.a.App": COMMIT_A})
    with pytest.raises(ResolveError):
        resolve.refresh(CuratedSet(), fetcher=lambda ref: COMMIT_A, path=path, today="2026-09-22")


def test_refresh_loads_existing_db_shape(tmp_path) -> None:
    """refresh loads the existing DB (last_verified + commits) before merging."""

    path = tmp_path / "flathub-commits.toml"
    _write_toml(path, {"org.a.App": COMMIT_A}, last_verified="2020-01-01")
    fetcher_calls = []

    def fetcher(ref: str) -> str:
        fetcher_calls.append(ref)
        return COMMIT_A2

    new_db = resolve.refresh(TWO, fetcher=fetcher, path=path, today="2026-09-22")
    # Both curated refs were fetched live (fresh values, not the DB's).
    assert sorted(fetcher_calls) == ["org.a.App", "org.b.Terminal"]
    # The fetched value won, and last_verified was bumped.
    assert new_db.resolve("org.a.App") == COMMIT_A2
    assert new_db.last_verified == "2026-09-22"


# --- CLI: emit --refresh ----------------------------------------------------


class _FakeResp:
    def __init__(self, body: str) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body.encode("utf-8")

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _flathub_fetcher(live: dict[str, str]):
    """A fake Flathub boundary keyed by the ref extracted from the request URL
    (``https://dl.flathub.org/repo/refs/stable/<ref>``)."""

    def fake_urlopen(req, *a, **k):
        ref = req.get_full_url().rsplit("/", 1)[-1]
        return _FakeResp(live[ref])

    return fake_urlopen


def test_cli_emit_refresh_persists_to_committed_db(tmp_path, monkeypatch, capsys) -> None:
    """``emit --refresh`` fetches live, persists the fresh commits back into the
    committed DB (pointed at a tmp file), and resolves the manifest to them."""

    committed = tmp_path / "flathub-commits.toml"
    _write_toml(committed, {"org.a.App": COMMIT_A, "org.b.Terminal": COMMIT_B})

    live = {"org.a.App": COMMIT_A2, "org.b.Terminal": COMMIT_B2}

    monkeypatch.setattr(
        "pengyos_desktop_curation.resolve.urllib.request.urlopen",
        _flathub_fetcher(live),
    )
    # Use the small 2-ref set (not the real 10-ref committed data).
    monkeypatch.setattr("pengyos_desktop_curation.cli.load_default", lambda: TWO)
    # Redirect the committed DB to the tmp file.
    monkeypatch.setattr("pengyos_desktop_curation.resolve.COMMIT_DATABASE_FILE", committed)
    # And refresh's default path (baked into the signature) — patch refresh to a
    # partial that points at the tmp file.

    real_refresh = resolve.refresh

    def refreshed(curated, *, fetcher, today=None):
        return real_refresh(curated, fetcher=fetcher, path=committed, today=today)

    monkeypatch.setattr("pengyos_desktop_curation.resolve.refresh", refreshed)

    out = tmp_path / "out"
    rc = cli.main(["emit", "--out", str(out), "--pin", "--refresh"])
    assert rc == cli.EXIT_OK

    # The committed DB was self-updated with the fresh values.
    parsed = tomllib.loads(committed.read_text(encoding="utf-8"))
    assert parsed["commits"]["org.a.App"] == COMMIT_A2
    assert parsed["commits"]["org.b.Terminal"] == COMMIT_B2
    assert parsed["last_verified"] == resolve._today()

    # The emitted manifest is pinned to the fresh values.
    manifest = (out / "mirror.toml").read_text(encoding="utf-8")
    assert "UNPINNED" not in manifest
    assert COMMIT_A2 in manifest and COMMIT_B2 in manifest

    # Text output reports the refresh.
    assert "last_verified=" in capsys.readouterr().out


def test_cli_emit_refresh_json_reports_last_verified(tmp_path, monkeypatch, capsys) -> None:
    committed = tmp_path / "flathub-commits.toml"
    _write_toml(committed, {"org.a.App": COMMIT_A, "org.b.Terminal": COMMIT_B})

    live = {"org.a.App": COMMIT_A2, "org.b.Terminal": COMMIT_B2}

    monkeypatch.setattr(
        "pengyos_desktop_curation.resolve.urllib.request.urlopen",
        _flathub_fetcher(live),
    )
    # Use the small 2-ref set (not the real 10-ref committed data).
    monkeypatch.setattr("pengyos_desktop_curation.cli.load_default", lambda: TWO)
    monkeypatch.setattr("pengyos_desktop_curation.resolve.COMMIT_DATABASE_FILE", committed)

    real_refresh = resolve.refresh

    def refreshed(curated, *, fetcher, today=None):
        return real_refresh(curated, fetcher=fetcher, path=committed, today=today)

    monkeypatch.setattr("pengyos_desktop_curation.resolve.refresh", refreshed)

    out = tmp_path / "out"
    cli.main(["emit", "--out", str(out), "--pin", "--refresh", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["refreshed"] is True
    assert payload["pinned"] is True
    assert payload["last_verified"] == resolve._today()


def test_cli_emit_pin_without_refresh_does_not_rewrite_db(tmp_path, monkeypatch, capsys) -> None:
    """``--pin`` alone resolves from the committed DB and must NOT rewrite it
    (the committed DB is the source of truth for ``--pin``)."""

    committed = tmp_path / "flathub-commits.toml"
    _write_toml(committed, {"org.a.App": COMMIT_A, "org.b.Terminal": COMMIT_B})
    before = committed.read_text(encoding="utf-8")

    # Make sure no fetcher is installed (a missing ref would fail loudly).
    out = tmp_path / "out"
    rc = cli.main(["emit", "--out", str(out), "--pin"])
    assert rc == cli.EXIT_OK
    assert committed.read_text(encoding="utf-8") == before


def test_cli_emit_refresh_bad_commit_fails_and_keeps_db(tmp_path, monkeypatch, capsys) -> None:
    """A ref whose fetched commit is not a real 64-hex commit fails loudly
    (exit 1) and leaves the committed DB untouched."""

    committed = tmp_path / "flathub-commits.toml"
    _write_toml(committed, {"org.a.App": COMMIT_A, "org.b.Terminal": COMMIT_B})
    before = committed.read_text(encoding="utf-8")

    def fake_urlopen(req, *a, **k):
        return _FakeResp("not-a-commit")

    monkeypatch.setattr("pengyos_desktop_curation.resolve.urllib.request.urlopen", fake_urlopen)
    # Use the small 2-ref set (not the real 10-ref committed data).
    monkeypatch.setattr("pengyos_desktop_curation.cli.load_default", lambda: TWO)
    monkeypatch.setattr("pengyos_desktop_curation.resolve.COMMIT_DATABASE_FILE", committed)


    real_refresh = resolve.refresh

    def refreshed(curated, *, fetcher, today=None):
        return real_refresh(curated, fetcher=fetcher, path=committed, today=today)

    monkeypatch.setattr("pengyos_desktop_curation.resolve.refresh", refreshed)

    out = tmp_path / "out"
    rc = cli.main(["emit", "--out", str(out), "--pin", "--refresh"])
    assert rc == cli.EXIT_FAIL
    assert "not a real OSTree commit" in capsys.readouterr().err
    assert committed.read_text(encoding="utf-8") == before
