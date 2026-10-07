"""Verify each buildable recipe's pinned sha256 against the live upstream tarball.

The recipe catalogue (``data/recipes.json``) records, for every dependency that
``2026.08`` does not ship, the pinned ``sha256`` of the upstream source. A
pinned hash is only as trustworthy as the tarball it was computed from, so this
module re-downloads each buildable recipe's source from
:attr:`.Recipe.download_url` and re-checks the digest.

This is the cheapest rung of the verification ladder that proves the *source* is
real and correct (ladder rung **c** — ``make <pkg>-source`` inside Buildroot
does exactly this). It never compiles anything.

The download is the only network touch. The two gitlab archives (greetd,
xdg-desktop-portal) sit behind a Cloudflare challenge from this host, so the
download seam is injectable: tests feed a local fixture tarball and the live run
hits the real endpoints. A downloaded blob that is not a gzip stream — a
Cloudflare / GitLab sign-in challenge is an HTML page that never starts with the
gzip magic bytes — is reported as :attr:`DownloadStatus.CHALLENGE_DETECTED`,
never mistaken for a good source.

Runtime depends only on the Python standard library.
"""

from __future__ import annotations

import hashlib
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable

from .recipes import BuildSystem, Recipe, RecipeCatalogue, default_recipe_catalogue

# The two magic bytes every gzip stream begins with (RFC 1952). A Cloudflare or
# GitLab sign-in challenge is an HTML page — it never starts with these bytes, so
# a ``200`` response that fails this check is a challenge, not a source.
_GZIP_MAGIC = b"\x1f\x8b"

# A hex sha256 digest, matching the catalogue's own shape.
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

# Per-download network timeout, seconds.
_DEFAULT_TIMEOUT = 30


class DownloadStatus(str, Enum):
    """The outcome of re-checking one recipe's pinned sha256."""

    #: The re-downloaded tarball's sha256 matches the pinned hash.
    VERIFIED = "verified"
    #: The tarball downloaded and is a real gzip, but its sha256 differs.
    MISMATCH = "mismatch"
    #: The endpoint answered, but not with a gzip tarball (a challenge page).
    CHALLENGE_DETECTED = "challenge_detected"
    #: The download itself failed (network / HTTP / DNS).
    DOWNLOAD_FAILED = "download_failed"


@dataclass(frozen=True)
class VerifyResult:
    """The outcome of verifying one recipe's pinned sha256."""

    recipe: Recipe
    """The recipe that was verified."""

    expected_hash: str
    """The sha256 pinned in the catalogue."""

    actual_hash: str
    """The sha256 of the re-downloaded tarball (``""`` if none was obtained)."""

    status: DownloadStatus
    """What happened during verification."""

    detail: str
    """A human-readable one-line explanation (the mismatch, or the failure)."""


@dataclass(frozen=True)
class DownloadError(Exception):
    """A download ran but did not yield a usable gzip tarball.

    Carries the :class:`DownloadStatus` so the caller can report
    :attr:`DownloadStatus.CHALLENGE_DETECTED` / :attr:`DownloadStatus.DOWNLOAD_FAILED`
    cleanly instead of raising a raw ``urllib`` error.
    """

    status: DownloadStatus
    detail: str


DownloadFn = Callable[[str], bytes]
"""A download seam: given an archive *url*, return its raw bytes.

The default uses ``urllib`` against the live endpoint; tests inject a function
that returns a fixture tarball's bytes, so the verify logic is exercised with no
network. A seam that cannot produce a tarball raises :class:`DownloadError`."""


def is_gzip_stream(chunk: bytes) -> bool:
    """Whether *chunk* begins with the gzip magic bytes (the first 2 bytes)."""
    return chunk[:2] == _GZIP_MAGIC


def sha256_bytes(data: bytes) -> str:
    """The hex sha256 of in-memory *data*."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | "os.PathLike[str]") -> str:
    """The hex sha256 of the file at *path*, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda fh=fh: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _urllib_download(url: str, *, timeout: int) -> bytes:
    """Download *url* over HTTP(S), returning the raw response bytes."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "pengyos-cosmic-native-deps-verify/0.1"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def verify_recipe(
    recipe: Recipe,
    *,
    download: DownloadFn | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> VerifyResult:
    """Re-check *recipe*'s pinned sha256 against its upstream tarball.

    *data-only* (``none*) recipes carry no hash, so they are trivially
    :attr:`DownloadStatus.VERIFIED` with no download.

    For a *buildable* recipe: download the tarball (via *download*, or the live
    ``urllib`` default), reject a non-gzip blob as
    :attr:`DownloadStatus.CHALLENGE_DETECTED`, and compare the digest to the
    pinned hash (:attr:`DownloadStatus.VERIFIED` / :attr:`DownloadStatus.MISMATCH`).
    """
    if recipe.build_system is BuildSystem.NONE:
        return VerifyResult(
            recipe=recipe,
            expected_hash="",
            actual_hash="",
            status=DownloadStatus.VERIFIED,
            detail="data-only recipe: no pinned hash to verify",
        )

    if download is not None:
        fetch: DownloadFn = download
    else:

        def fetch(url: str) -> bytes:
            return _urllib_download(url, timeout=timeout)

    try:
        data = fetch(recipe.download_url)
    except DownloadError as exc:
        return VerifyResult(
            recipe=recipe,
            expected_hash=recipe.hash,
            actual_hash="",
            status=exc.status,
            detail=exc.detail,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return VerifyResult(
            recipe=recipe,
            expected_hash=recipe.hash,
            actual_hash="",
            status=DownloadStatus.DOWNLOAD_FAILED,
            detail=f"{type(exc).__name__}: {exc}",
        )

    if not is_gzip_stream(data):
        preview = "".join(chr(b) if 32 <= b < 127 else "." for b in data[:64])
        return VerifyResult(
            recipe=recipe,
            expected_hash=recipe.hash,
            actual_hash="",
            status=DownloadStatus.CHALLENGE_DETECTED,
            detail=(
                "downloaded blob is not a gzip tarball "
                f"(challenge page?): {preview!r}"
            ),
        )

    actual = sha256_bytes(data)
    if actual != recipe.hash:
        return VerifyResult(
            recipe=recipe,
            expected_hash=recipe.hash,
            actual_hash=actual,
            status=DownloadStatus.MISMATCH,
            detail=f"expected {recipe.hash}, got {actual}",
        )
    return VerifyResult(
        recipe=recipe,
        expected_hash=recipe.hash,
        actual_hash=actual,
        status=DownloadStatus.VERIFIED,
        detail=f"{recipe.source}: {actual}",
    )


def verify_catalogue(
    catalogue: RecipeCatalogue | None = None,
    *,
    download: DownloadFn | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> list[VerifyResult]:
    """Verify every *buildable* recipe in *catalogue* (data-only skipped).

    Returns one :class:`VerifyResult` per buildable recipe, in declaration
    order. Data-only recipes are reported as
    :attr:`DownloadStatus.VERIFIED` with no download.
    """
    recipes: Iterable[Recipe] = (
        catalogue.recipes if catalogue is not None else default_recipe_catalogue()
    )
    return [
        verify_recipe(recipe, download=download, timeout=timeout)
        for recipe in recipes
    ]


def summary(results: Iterable[VerifyResult]) -> dict[DownloadStatus, int]:
    """Count results by :class:`DownloadStatus` (a stable, ordered mapping)."""
    counts: dict[DownloadStatus, int] = {status: 0 for status in DownloadStatus}
    for result in results:
        counts[result.status] += 1
    return counts
