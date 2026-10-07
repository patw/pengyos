"""Tests for ``pengyos_cosmic_native_deps.verify`` (hash verification).

The *hash-verify* item re-checks each buildable recipe's
pinned sha256 against its live upstream tarball. The download is the only
network touch, so these tests are fully offline: they inject a :data:`DownloadFn`
that returns a fixture tarball's bytes (or raises :class:`DownloadError` / a
``urllib`` error) and assert the :class:`VerifyResult` the verify logic derives.

The two gitlab archives (greetd, xdg-desktop-portal) sit behind a Cloudflare
challenge from this host — a challenge page is an HTML blob that is *not* a gzip
stream — so the critical behaviour pinned here is that a non-gzip download is
reported as :attr:`DownloadStatus.CHALLENGE_DETECTED`, never mistaken for a good
source.
"""

from __future__ import annotations

import gzip
import json
import pathlib
import tarfile
import urllib.error

import pytest

import pengyos_cosmic_native_deps.cli as cli
from pengyos_cosmic_native_deps.recipes import (
    BuildSystem,
    Recipe,
    default_recipe_catalogue,
)
from pengyos_cosmic_native_deps.verify import (
    DownloadError,
    DownloadStatus,
    is_gzip_stream,
    sha256_bytes,
    summary,
    verify_catalogue,
    verify_recipe,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOGUE = default_recipe_catalogue()


# --- fixtures ---------------------------------------------------------------


def _make_tarball(tmp_path: pathlib.Path, name: str, content: bytes = b"cosmic-native-dep") -> pathlib.Path:
    """A real gzip tarball of *content* at *tmp_path/*name; returns its path."""
    path = tmp_path / name
    with gzip.open(path, "wb") as fh:
        fh.write(content)
    return path


def _recipe(
    name: str = "cosmic-portal",
    *,
    hash: str = "a" * 64,
    build_system: BuildSystem = BuildSystem.CARGO,
    source: str = "cosmic-portal-epoch-1.8.0.tar.gz",
    version: str = "epoch-1.8.0",
    download_url: str = "https://example.com/cosmic.tar.gz",
) -> Recipe:
    """A buildable :class:`Recipe` with a pinned *hash*."""
    return Recipe(
        name=name,
        version=version,
        url=f"https://example.com/{name}",
        download_url=download_url,
        build_system=build_system,
        license="GPL-3.0-or-later",
        source=source,
        hash=hash,
        br_symbols=("BR2_PACKAGE_XDG_DESKTOP_PORTAL_BACKEND_COSMIC",),
    )


def _none_recipe(name: str = "fonts") -> Recipe:
    """A data-only (``none``) :class:`Recipe` (no hash, no source)."""
    return Recipe(
        name=name,
        version="",
        url="https://example.com/fonts",
        download_url="",
        build_system=BuildSystem.NONE,
        license="OFL-1.1",
        source="",
        hash="",
        br_symbols=("BR2_PACKAGE_FIRA_FONT",),
    )


# --- the primitives ---------------------------------------------------------


def test_is_gzip_stream_detects_the_gzip_magic():
    assert is_gzip_stream(b"\x1f\x8b" + b"rest")
    assert not is_gzip_stream(b"<html><!-- challenge --></html>")
    # An empty blob has no magic to speak of.
    assert not is_gzip_stream(b"")


def test_sha256_bytes_matches_the_stdlib():
    import hashlib

    data = b"the quick brown fox"
    assert sha256_bytes(data) == hashlib.sha256(data).hexdigest()


# --- verify_recipe: the happy path ------------------------------------------


def test_verify_recipe_verified_when_the_hash_matches(tmp_path):
    tarball = _make_tarball(tmp_path, "cosmic-portal-epoch-1.8.0.tar.gz")
    data = tarball.read_bytes()
    real_hash = sha256_bytes(data)

    recipe = _recipe(hash=real_hash)
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return data

    result = verify_recipe(recipe, download=fetch)

    assert result.status is DownloadStatus.VERIFIED
    assert result.expected_hash == real_hash
    assert result.actual_hash == real_hash
    assert calls == [recipe.download_url]  # the download seam hit the URL


def test_verify_recipe_uses_the_recipe_download_url(tmp_path):
    tarball = _make_tarball(tmp_path, "greetd-0.10.3.tar.gz")
    data = tarball.read_bytes()
    recipe = _recipe(
        name="greetd",
        hash=sha256_bytes(data),
        source="greetd-0.10.3.tar.gz",
        download_url="https://gitlab.example/greetd.tar.gz",
    )

    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return data

    result = verify_recipe(recipe, download=fetch)
    assert result.status is DownloadStatus.VERIFIED
    assert result.recipe.name == "greetd"
    assert calls == [recipe.download_url]


# --- verify_recipe: the failure paths ---------------------------------------


def test_verify_recipe_reports_a_hash_mismatch(tmp_path):
    tarball = _make_tarball(tmp_path, "cosmic.tar.gz")
    data = tarball.read_bytes()

    recipe = _recipe(hash="b" * 64)  # wrong hash
    result = verify_recipe(recipe, download=lambda url: data)

    assert result.status is DownloadStatus.MISMATCH
    assert result.expected_hash == "b" * 64
    assert result.actual_hash == sha256_bytes(data)
    assert "expected" in result.detail and result.actual_hash in result.detail


def test_verify_recipe_detects_a_non_gzip_challenge_page():
    """A challenge page (HTML) is a 200 that is not a gzip tarball."""
    challenge = b"<!DOCTYPE html><html><head><title>Sign in</title>"

    recipe = _recipe()
    result = verify_recipe(recipe, download=lambda url: challenge)

    assert result.status is DownloadStatus.CHALLENGE_DETECTED
    assert result.actual_hash == ""
    assert "not a gzip" in result.detail


def test_verify_recipe_propagates_a_download_error_as_its_status():
    recipe = _recipe()
    boom = DownloadError(DownloadStatus.CHALLENGE_DETECTED, "403 Forbidden: challenge")

    def fetch(url: str) -> bytes:
        raise boom

    result = verify_recipe(recipe, download=fetch)
    assert result.status is DownloadStatus.CHALLENGE_DETECTED
    assert "challenge" in result.detail


def test_verify_recipe_reports_a_network_failure():
    recipe = _recipe()

    def fetch(url: str) -> bytes:
        raise urllib.error.URLError("name resolution failed")

    result = verify_recipe(recipe, download=fetch)
    assert result.status is DownloadStatus.DOWNLOAD_FAILED
    assert result.actual_hash == ""
    assert "URLError" in result.detail


# --- verify_recipe: data-only recipes ---------------------------------------


def test_verify_recipe_skips_data_only_recipes_without_downloading():
    recipe = _none_recipe()
    called = False

    def fetch(url: str) -> bytes:
        nonlocal called
        called = True
        return b""

    result = verify_recipe(recipe, download=fetch)
    assert result.status is DownloadStatus.VERIFIED
    assert result.expected_hash == ""
    assert not called  # no download for a data-only recipe


# --- verify_catalogue + summary ---------------------------------------------


def test_verify_catalogue_verifies_every_buildable_and_skips_none_download():
    buildable = [r for r in CATALOGUE.recipes if r.build_system is not BuildSystem.NONE]
    none = [r for r in CATALOGUE.recipes if r.build_system is BuildSystem.NONE]

    assert buildable, "expected at least one buildable recipe in the catalogue"

    def fetch(url: str) -> bytes:
        # A well-formed gzip blob whose hash we do NOT know — every buildable
        # recipe will therefore MISMATCH, and the none recipe will VERIFIED.
        return gzip.compress(b"fixture")

    results = verify_catalogue(CATALOGUE, download=fetch)
    by_name = {r.recipe.name: r for r in results}

    for recipe in buildable:
        assert by_name[recipe.name].status is DownloadStatus.MISMATCH
    for recipe in none:
        assert by_name[recipe.name].status is DownloadStatus.VERIFIED


def test_summary_counts_by_status():
    results = [
        verify_recipe(_recipe(hash="a" * 64), download=lambda url: b"\x1f\x8bx"),
        verify_recipe(_recipe(hash="b" * 64), download=lambda url: b"\x1f\x8bx"),
        verify_recipe(_none_recipe(), download=lambda url: b""),
    ]
    counts = summary(results)
    assert counts[DownloadStatus.VERIFIED] == 1  # the none recipe
    assert counts[DownloadStatus.MISMATCH] == 2  # the two buildable recipes
    assert sum(counts.values()) == 3


# --- the CLI: verify --------------------------------------------------------


def _fake_results(*statuses: DownloadStatus) -> list:
    """Fake VerifyResults (names cycle through the catalogue buildables)."""
    buildable = [r for r in CATALOGUE.recipes if r.build_system is not BuildSystem.NONE]
    results = []
    for i, status in enumerate(statuses):
        recipe = buildable[i % len(buildable)]
        results.append(
            type(
                "R",
                (),
                {
                    "recipe": recipe,
                    "expected_hash": recipe.hash,
                    "actual_hash": "c" * 64 if status is DownloadStatus.VERIFIED else "",
                    "status": status,
                    "detail": status.value,
                },
            )()
        )
    return results


def test_cli_verify_text_and_exit_zero_when_all_verified(capsys, monkeypatch):
    monkeypatch.setattr(cli, "verify_catalogue", lambda catalogue, **kw: _fake_results(DownloadStatus.VERIFIED))
    rc = cli.main(["verify"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "verified" in out


def test_cli_verify_exit_nonzero_when_any_unverified(capsys, monkeypatch):
    monkeypatch.setattr(
        cli,
        "verify_catalogue",
        lambda catalogue, **kw: _fake_results(DownloadStatus.VERIFIED, DownloadStatus.CHALLENGE_DETECTED),
    )
    rc = cli.main(["verify"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "challenge_detected" in out


def test_cli_verify_json_writes_a_report_file(tmp_path, capsys, monkeypatch):
    out_file = tmp_path / "verify.json"
    monkeypatch.setattr(cli, "verify_catalogue", lambda catalogue, **kw: _fake_results(DownloadStatus.VERIFIED))
    rc = cli.main(["verify", "--json", str(out_file)])
    assert rc == 0
    assert out_file.is_file()
    report = json.loads(out_file.read_text(encoding="utf-8"))
    assert report["summary"]["verified"] == 1
    assert report["results"][0]["status"] == "verified"
    # The machine view is deterministic (sorted keys).
    assert "catalogue" in report


def test_cli_verify_names_filters_to_the_requested_recipes(monkeypatch):
    seen: list = []

    def fake(catalogue, **kw):
        seen.extend(catalogue.recipes)
        return []

    monkeypatch.setattr(cli, "verify_catalogue", fake)
    cli.main(["verify", "--names", "cosmic-portal"])
    assert [r.name for r in seen] == ["cosmic-portal"]


def test_cli_verify_unknown_name_raises_cli_error(capsys):
    with pytest.raises(cli.CliError) as exc:
        cli._cmd_verify(_ns(names=["not-a-recipe"]))
    assert "unknown recipe name" in str(exc.value)


def test_cli_verify_help_lists_the_subcommand():
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with pytest.raises(SystemExit):
            cli.main(["verify", "--help"])
    assert "--names" in buf.getvalue()


def _ns(**kwargs: object) -> argparse.Namespace:  # noqa: F821 (imported below)
    import argparse

    return argparse.Namespace(**kwargs)


# --- the working download method (opt-in, live network) ----------------------
#
# The two gitlab archives (greetd, xdg-desktop-portal) are behind a Cloudflare
# challenge from this host, so the *offline* suite injects fixtures. The
# resolution to this item was to download them from their canonical GitHub
# mirrors instead — the GitHub archive is byte-identical to the pinned tarball
# (same commit, same sha256). These tests hit the live GitHub endpoints and
# prove the pinned sha256 matches, so they are opt-in behind PENGYOS_IT=1.


@pytest.mark.integration
@pytest.mark.parametrize(
    ("name", "expected_hash"),
    [
        ("greetd", "ee5cb70e0add4ca9c9fe57e47581ab0002d44c07743fb5492469f3b570db640b"),
        (
            "xdg-desktop-portal",
            "5afeda953c3743efaa1160e73d62c5bfcac2ceca60e0e134e9b48ed651783625",
        ),
    ],
)
def test_live_github_archive_matches_the_pinned_hash(name: str, expected_hash: str):
    """The canonical GitHub mirror of each gitlab-challenged archive verifies
    against the pinned sha256 — the working download method for this item."""
    recipe = CATALOGUE.by_name(name)
    assert recipe is not None, f"{name} is not in the catalogue"
    assert recipe.hash == expected_hash

    result = verify_recipe(recipe)  # live urllib download
    assert result.status is DownloadStatus.VERIFIED, result.detail
    assert result.actual_hash == expected_hash
    assert result.recipe.name == name
