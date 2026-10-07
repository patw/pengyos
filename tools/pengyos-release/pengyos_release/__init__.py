"""PengyOS release — publish a signed, versioned release to a local update feed.

Public API surface (grows as features land):

* :mod:`pengyos_release.signing` — injectable signing abstraction.
* :mod:`pengyos_release.manifest` — release metadata + feed-index schema
  (``ReleaseManifest``, ``FeedIndex``, ``Channel``, ``Artifact``, sha256 digests,
  and structural validation).
* :mod:`pengyos_release.feed` — feed directory layout, persistence, and
  :func:`validate_feed`.
* :mod:`pengyos_release.publish` — :func:`publish` (build output -> signed
  release under ``<feed>/pengyos/<edition>/<version>/``).
* :mod:`pengyos_release.feed` — feed directory layout, persistence,
  :func:`validate_feed`, the :func:`regenerate_index` (``index``),
  :func:`verify_feed` (``verify``), and :func:`retain` (``retain``) feed
  operations.
* :mod:`pengyos_release.cli` — the ``pengyos-release`` command line.
"""

from __future__ import annotations

from .errors import ChannelError
from .feed import (
    DEFAULT_SIGNER,
    FeedValidation,
    FeedVerification,
    RetainResult,
    regenerate_index,
    retain,
    validate_feed,
    verify_feed,
)
from .manifest import (
    Artifact,
    Channel,
    FeedIndex,
    ReleaseManifest,
    is_sha256_hex,
    sha256_bytes,
    sha256_file,
    validate_channel_name,
)
from .publish import ARTIFACTS, publish
from .signing import HmacSigner, Signer, StubSigner

__version__ = "0.10.0"

__all__ = [
    "__version__",
    # signing
    "Signer",
    "HmacSigner",
    "StubSigner",
    # manifest / data layer
    "Artifact",
    "Channel",
    "ReleaseManifest",
    "FeedIndex",
    "sha256_bytes",
    "sha256_file",
    "is_sha256_hex",
    "validate_channel_name",
    # feed
    "validate_feed",
    "verify_feed",
    "regenerate_index",
    "retain",
    "FeedValidation",
    "FeedVerification",
    "RetainResult",
    "DEFAULT_SIGNER",
    # publish
    "publish",
    "ARTIFACTS",
    # errors
    "ChannelError",
]
