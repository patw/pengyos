# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.0] — 2026-10-03

### Added

- `download-publish` lists the **Server NVIDIA** image
  (`IMAGE_ID=pengyos-server-nvidia`), between Server and Core.

## [0.8.0] — 2026-09-30

### Added

- **The public update feed** (`pengyos_release/osupdate.py`), read by
  `pengyos-update` on installed machines: `update-publish` (a Buildroot output
  -> `update/<IMAGE_ID>/<YY.MM.N>/` with `rootfs.ext4.zst`, `bzImage`, a
  `pengyos-update-1` `manifest.json` and its ed25519 `manifest.json.sig`),
  `update-promote` (testing -> stable without re-signing) and `update-feed`
  (Atom `feed.xml` + `index.html`). Signatures are `pengyos-sig-v1`,
  byte-compatible with `pengyos-sign verify`; keys are `pengyos-sign keygen`
  files. Signing needs `cryptography` on the build host only.

## [0.7.0] — 2026-09-24

### Added

- **Robust GitHub-ready contract enforcement** (`tests/test_github_ready.py`,
  8 -> 13). The `test_github_ready` subtask is now complete: beyond asserting
  that `README.md`, `LICENSE`, `CHANGELOG.md`, and `.gitignore` exist, the suite
  now verifies the contract from spec.md is *actually* met — "Docs are authored
  — keep them in sync":
  - `test_gitignore_is_functional` — `.gitignore` must actually ignore the noisy
    artifacts (`__pycache__/`, `*.py[cod]`, `*.egg-info/`, `.pytest_cache/`), not
    merely exist.
  - `test_readme_is_nonempty_and_describes_the_tool` — the README must be
    non-empty and describe the tool (mentions `pengyos`/`pengyos-release` and
    `publish`/`feed`), not a placeholder.
  - `test_changelog_uses_keep_a_changelog_format` — the CHANGELOG must use
    Keep a Changelog `## [x.y.z]` version headings.
  - `test_readme_and_license_agree_on_copyright_holder` — the README's
    "Copyright (c) ..." line must agree with the LICENSE's (trailing sentence
    punctuation is normalised), so a copyright drift fails the build.
  - `test_pyproject_license_matches_licenses_file` — `pyproject.toml`'s declared
    license must be MIT, matching the LICENSE file.
- Version bumped 0.6.0 -> 0.7.0. Tests: 208 -> 213.

## [0.6.0] — 2026-09-24

### Added

- **A committed fixture build-output** (`tests/fixtures/sample-build/`): a real,
  read-only directory shipped in the repo that mirrors a `pengyos-build` output
  (``images/rootfs.img``, ``images/uki.efi``, ``sbom.json``, ``attestation.json``,
  ``repro-report.txt``). The `build_output` test fixture now points at it instead
  of generating bytes in `tmp_path`, so the suite is *grounded* in a fixed input.
- **Golden publish** (`tests/test_fixture_golden.py`): publishing the fixed
  fixture is deterministic — two independent publishes are byte-identical, the
  published rootfs round-trips back to the fixture, the verbatim artifacts match
  the source bytes, and the recorded digests match the source digests.
- **`tests/test_fixture_golden.py`** (16): fixture well-formedness, the golden
  publish property, and that `verify` catches each mutation distinctly.

### Changed

- **`verify_feed` now reports mutations distinctly.** An *unsigned* release (no
  signature present) reports "manifest/index has no signature"; a *tampered*
  stored file reports "digest mismatch"; a *dangling* channel pointer reports
  "dangles". A *wrong* (but present) signature still reports "did not verify" —
  so `verify` can tell "never signed" apart from "signed by the wrong key".
- Version bumped 0.5.0 -> 0.6.0.

## [0.5.0] — 2026-09-24

### Added

- **`retain`** — the `retain` feed operation (`pengyos_release/feed.py`) and the
  `pengyos-release retain` CLI handler. `retain(feed, *, keep, dry_run=True,
  signer=None) -> RetainResult` prunes old versions from a feed:
  - For every edition, keeps the newest `keep` versions (by `parse_version`
    order) and prunes the rest. `kept` / `pruned` come back oldest-first,
    edition-grouped, in lock-step with `list_all`.
  - **Never prunes a version a channel points at** — the updater fetches the
    pointed-at version, so pruning it would dangle the channel pointer. The
    protected set is read from the signed index.
  - **Dry run by default**: by default it reports what *would* be pruned without
    touching the feed. `--apply` actually deletes the pruned version dirs and
    re-signs the index (via `regenerate_index`) so the feed is left consistent —
    the pruned releases drop out of the index and surviving channel pointers
    still resolve.
  - CLI: `--keep` (required, >= 1), `--apply` (actually prune), `--json`. A dry
    run is the default; `--dry-run` is accepted for symmetry. `--keep 0` and a
    missing feed fail with a `FeedError` (exit 2).
- Public API: `pengyos_release.retain` and `pengyos_release.RetainResult`.

### Changed

- Version bumped 0.4.0 -> 0.5.0.

## [0.4.0] — 2026-09-24

### Added

- **`index`** — the `regenerate_index` feed operation (`pengyos_release/feed.py`)
  and the `pengyos-release index` CLI handler. `regenerate_index(feed, *,
  signer=None, channels=None)` rebuilds the signed :class:`FeedIndex` + the
  `channels/<name>` pointers from the on-disk state:
  - Discovers every release manifest under
    `<feed>/pengyos/<edition>/<version>/` (via `list_all`) and rebuilds a fresh
    signed index over them.
  - Reads the channel pointers from the durable plain-text mirror under
    `<feed>/channels/<name>` (written by `publish`/`index`) and mirrors them
    back into the rebuilt index, then writes the index + pointers atomically.
    This is the recovery path: a missing/corrupt `index.json` is rebuilt as long
    as the channel pointer files survive.
  - `channels` optionally filters which channels are (re)written (default: every
    channel that has a pointer file).
  - Requires the feed directory to exist (a typo'd `--feed` fails cleanly with a
    `FeedError`); a corrupt/missing `index.json` inside an existing feed still
    recovers. Deterministic — a second regenerate is byte-identical.
- **`verify`** — the `verify_feed` feed operation and the
  `pengyos-release verify` CLI handler. `verify_feed(feed, *,
  check_signatures=True, signer=None)` re-checks what `publish` stored:
  - Re-reads every stored artifact file and confirms its sha256 matches the
    digest recorded in the manifest (catches a tampered or partially-written
    rootfs/UKI/SBOM — the `verify` command's core job).
  - Verifies every manifest + the index signature against `signer` (default: a
    deterministic stub, so a stub-published feed verifies with no key; pass
    `--key` for a keyed HMAC feed).
  - Flags a **dangling channel pointer** (a pointer whose version has no manifest
    on disk) so a feed that lost a version does not silently keep pointing at it.
  - `check_signatures=False` re-checks only the hashes (no signer needed).
- Public API: `pengyos_release.verify_feed`, `pengyos_release.regenerate_index`,
  `pengyos_release.FeedVerification`, and `pengyos_release.DEFAULT_SIGNER`.

### Changed

- `verify` gains an optional `--key` (raw key or key-file, defaults to a
  deterministic stub).
- Version bumped 0.3.0 -> 0.4.0.

### Notes

- The `retain` handler is still scaffolded (parse + dispatch) and reports
  "not yet implemented" until its logic lands. See `implementation.md`.

## [0.3.0] — 2026-09-24

### Added

- **`publish`** — the first real release handler (`pengyos_release/publish.py`).
  `publish(build, edition, version, feed, *, signer=None, channel="stable")`
  turns a `pengyos-build` output into a signed, versioned release under
  `<feed>/pengyos/<edition>/<version>/`:
  - Collects the five release artifacts (rootfs, UKI, SBOM, attestation,
    reproducible-report) via a canonical `_ARTIFACT_SOURCES` mapping;
    `ARTIFACTS` exposes the published names.
  - **Deterministically compresses** the rootfs to `rootfs.img.gz` (gzip,
    `mtime=0`, no embedded filename, level 6) so two identical builds produce
    byte-identical archives — the release is the unit of change for a
    reproducible distro. Compression + hashing are streamed in 1 MiB blocks, so
    a large rootfs never has to fit in memory.
  - Records a **real sha256 over each stored file** (the digest is over the
    compressed rootfs, i.e. what lives on disk) and **signs every artifact and
    the manifest** itself.
  - Writes the signed `manifest.json`, then (unless `channel=None`) registers
    the release in the signed index and updates the channel pointer atomically;
    republishing the same edition+version replaces rather than duplicates.
  - CLI: `pengyos-release publish` now implements the handler (was a scaffold);
    adds an optional `--key` (raw key or key-file, defaults to a deterministic
    stub so a local feed signs without a key on disk).
- Public API: `pengyos_release.publish` and `pengyos_release.ARTIFACTS` are
  re-exported.

### Changed

- Version bumped 0.2.0 -> 0.3.0.

### Fixed

- `tests/test_github_ready::test_package_imports_with_version` now reads the
  version from `pyproject.toml` instead of hardcoding it, so it stops drifting.

### Notes

- The `index`/`verify`/`retain` handlers are still scaffolded (parse + dispatch)
  and report "not yet implemented" until their logic lands. See `implementation.md`.
- The default signer is a non-keyed stub (see `AGENTS.md`); use `--key` for a
  keyed HMAC feed.

## [0.2.0] — 2026-09-24

### Added

- Real sha256 digest computation over build artifacts: `sha256_bytes` and a
  streaming `sha256_file` (so a large rootfs image is hashed in chunks), plus
  `is_sha256_hex` and the `DIGEST_ALGORITHM` constant. Digests are lowercase hex
  (`sha256sum`-compatible).
- First-class Channel model: the `Channel` dataclass, `validate_channel_name`
  (lowercase `[a-z][a-z0-9-]*`), the `KNOWN_CHANNELS` defaults (`stable`,
  `testing`), and `FeedIndex` wiring (`channel`, `set_channel`,
  `channels_as_list`). Channel names/versions validate on construction.
- Structural validation of the committed, validated data: `Artifact.validate`,
  `ReleaseManifest.validate`, `FeedIndex.validate`, and the on-disk
  `validate_feed` walker (with `FeedValidation` results). `validate_feed` checks
  every release manifest + the signed index and its channel pointers, collecting
  every problem; `check_signatures=True` additionally verifies the index and
  each manifest against a signer.
- `Signer` protocol is now `@runtime_checkable` so `isinstance` works with any
  signer-like object.
- Tests for hashing, the channel model, and validation (56 -> 119).

### Notes

- The `publish`/`index`/`verify`/`retain` handlers are still scaffolded (parse +
  dispatch) and report "not yet implemented" until their logic lands. See
  `implementation.md`.

## [0.1.0] — 2026-09-24

### Added

- Scaffold the `pengyos_release` package and `pengyos-release` console script.
- Injectable signing abstraction (HMAC-SHA256 implementation + stub for tests).
- Release metadata + feed-index schema (`manifest.py`): canonicalised,
  deterministic manifests and a signed index with channel pointers.
- Feed layout, discovery, and atomic persistence (`feed.py`).
- CLI with `publish`, `index`, `verify`, and `retain` subcommands, dispatch,
  structured error handling, and `--json` output.
- Test suite (`pytest`) covering signing, the schema, feed layout, and the CLI.
- GitHub-ready docs: README, MIT LICENSE (Copyright (c) 2026 Pat Wendorf),
  CHANGELOG, and .gitignore.

### Notes

- The `publish`/`index`/`verify`/`retain` handlers are scaffolded (parse +
  dispatch) and report "not yet implemented" until their logic lands. See
  `implementation.md`.
