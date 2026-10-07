# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Key-set definition and Secure Boot file-permission policy in
  `pengyos_sbkeys/keys.py`: the PK/KEK/db key set and the `0600` private-key /
  `0644` certificate policy (key directory `0700`).
- `pengyos-sbkeys init` now materializes the key set under `--out`, enforcing
  the permission policy via `chmod` (independent of umask) and printing only a
  per-file summary — never key material.
- `pengyos-sbkeys enroll-commands` generates the `sbsigntools` commands that
  enroll PK/KEK/db into the firmware EFI Secure Boot variables (`cert-to-efi-sig-list`
  + `efi-sig-list -a`). GUIDs are derived deterministically (RFC 4122 v5 from a
  fixed project namespace) so the output is golden-testable. Supports a shell
  script (default) and `--json` output. Reads only the public certificates; the
  private keys are never opened and the output never contains key material.
  Fails closed (exit 1) if a required certificate is missing.
- `pengyos_sbkeys/enroll.py`: the enrollment engine (`build_enrollment`,
  `render_shell`, `render_json`) kept separate from the dispatcher for testability.
- Tests for the policy (`test_keys.py`) and the `init` command
  (`test_init.py`), including a permissive-umask enforcement test and a
  no-key-material-leak assertion.
- Tests for `enroll-commands` (`test_enroll.py`): deterministic GUID policy,
  golden shell output, valid JSON structure, enrollment ordering, fail-closed
  on a missing certificate, and a no-key-material-leak assertion for both the
  shell and JSON output.
- `pengyos-sbkeys sign` generates the `sbsign`/`sbverify` commands that sign a
  Unified Kernel Image (UKI) with the db signing key and then verify the
  resulting signature. It prints a `sh` script (default) or a `--json` summary,
  resolves the default certificate (`KEY` -> `KEY.cert`) and signed output
  (`<uki>.signed`) when not given, and fails closed (exit 1) on a missing key,
  certificate or UKI. The private key is only referenced by path -- never
  opened -- so key material never appears in the output. The commands are
  generated (not executed), so tests never call `sbsign`/`sbverify`. `sign
  --execute` (actually running the tools) is a future subtask and raises
  `NotYetImplemented`.
- `pengyos_sbkeys/sign.py`: the signing engine (`build_signing`, `Signing`,
  `render_shell`, `render_json`) kept separate from the dispatcher for
  testability.
- `pengyos-sbkeys` now **refuses to operate if key file permissions are wrong**
  (fail closed). Before `enroll-commands` (which scans the key directory) and
  `sign` (which uses the signing key) touch existing private material, the new
  `pengyos_sbkeys/security` module verifies every managed private key has no
  group/world bits (`0600` or stricter, e.g. `0400`). A private key found with
  more permissive bits is treated as compromised: the command exits 1 with an
  `insecure key permissions` message on stderr instead of enrolling or signing.
  Public certificates are always considered secure (world-readable by nature),
  and an unmanaged key the guard cannot classify is never blocked, so foreign
  keys still sign. `PermCheckError` (a `RuntimeError`) is raised by the engine
  helpers and mapped to exit 1 by the CLI.
- `pengyos_sbkeys/security.py`: the enforcement side of the perms policy
  (`perms_are_secure`, `check_dir`, `enforce_dir`, `check_key`, `enforce_key`,
  `resolve_key_file`, `PermCheckError`, `PERMISSIONS_MASK`), plus
  `keys.key_file_for_name` to resolve a managed key by filename.
- Tests for the perm guard (`test_security.py`): `0600`/`0400` secure, any
  group/world bit insecure, certificates always secure, directory-wide and
  single-key checks, the unmanaged-key exemption, and `enroll-commands`/`sign`
  CLI refusal (and the `init` -> `enroll` -> `sign` happy path).
- Tests for `sign` (`test_sign.py`): golden shell output, valid JSON structure,
  default certificate / signed-output resolution (and that the signed copy never
  overwrites the original), fail-closed on a missing key/cert/UKI, a
  no-key-material-leak assertion for both the shell and JSON output, and the
  `--execute` not-yet-implemented placeholder.
- The GitHub-ready contract (`spec.md`) is now enforced by an expanded
  `test_github_ready`: in addition to the required files (`README.md`, `LICENSE`
  MIT with "Copyright (c) 2026 Pat Wendorf", `CHANGELOG.md`, `.gitignore`), a
  `pyproject.toml` declaring the `pengyos-sbkeys` console script, an importable
  package and a non-empty test suite, it now checks that the README documents all
  three subcommands and the security posture (no key leak / perm refusal), and
  that the CHANGELOG carries both an `[Unreleased]` and a versioned section.
- `init` now materializes **real** PK/KEK/db key material with `openssl`
  (see `pengyos_sbkeys/generate.py`, replacing the random-bytes stand-in):
  EC (ECDSA) P-256 private keys in PKCS#8 PEM form via `openssl genpkey`, and
  self-signed X.509 certificates in PEM form via `openssl req -x509` (10-year
  lifetime, subject `CN=PengyOS Secure Boot <role>`). `generate_all` writes
  every private key first, then builds each certificate from its freshly
  generated key, in Secure Boot role order (PK, KEK, db). The `openssl` calls
  are the only external dependency; they are isolated in `generate._run` so
  they are mocked in unit tests (no real `openssl` required) and exercised for
  real only under the opt-in `PENGYOS_IT=1` integration guard. The private key
  is only ever handed to `openssl` by path when building a certificate -- its
  contents are never read by Python -- so key material never leaks into the
  `init` summary.

### Notes

- `init` writes real `openssl`-generated key material now (the previous
  random-bytes placeholder was replaced; see the entry above).

## [0.1.0] - 2026-09-18

### Added

- Scaffold of the `pengyos-sbkeys` CLI with `init`, `enroll-commands` and
  `sign` subcommands (argument parsing + dispatch).
- GitHub-ready project layout: `README.md`, `LICENSE` (MIT), `CHANGELOG.md`,
  `.gitignore` and `pyproject.toml`.
- pytest suite including `test_github_ready` and CLI smoke tests.
