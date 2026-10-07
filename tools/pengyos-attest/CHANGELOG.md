# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.10.0] - 2026-09-14

### Added
- `tests/test_concurrency_sign.py`: cross-PROCESS lock contention for
  `sign` (implementation.md item 9). Real separate processes (no threads
  standing in) now prove the exclusive `<log>.lock` flock works across
  process boundaries: (a) six barrier-synchronized worker processes racing
  `chain.sign_head` on the same head with the same key — exactly one
  succeeds, the other five are refused by the duplicate check, and the
  sidecar ends with exactly one valid, head-covering entry (deterministic
  ed25519 lets the test assert the exact surviving signature); (b) the same
  race through the CLI — five subprocesses run `pengyos-attest sign`
  simultaneously behind a start-gate file, one exits 0 and the rest exit 1
  with "already signed"; (c) a child process attempting `sign` provably
  BLOCKS (sidecar not written, child not exited) while another process
  holds the lock, and succeeds only after release; (d) a mixed race of
  child-process appends and signers sharing one barrier — the chain stays a
  complete, link-consistent chain and every sidecar entry is a valid
  signature over a record that really was in the chain.

## [0.9.0] - 2026-09-14

### Changed
- The export bundle is now **format version 2**: it includes `sigs`, a
  snapshot of the `<log>.sigs` head-signature sidecar (in file order; `[]`
  when the sidecar does not exist), so an observer holding only the bundle
  can re-check every detached signature and see which keys anchored the
  chain. `chain.verify_export` accepts BOTH versions: v2 bundles require
  `sigs` to be a list of well-formed entries each carrying a valid ed25519
  signature over its own seq/head_hash (shape rule shared with the sidecar
  loader via the new `signing.entry_shape_error`), while v1 (pre-signing)
  bundles remain verifiable but must not carry a `sigs` key. Exporting a
  chain whose sidecar is corrupt or holds an invalid signature is refused
  (`LogError`, CLI exit 1). The CLI `export` summary now reports the
  captured signature count when non-empty.

## [0.8.0] - 2026-09-14

### Added
- `tests/test_github_ready.py` (implementation.md item 8): the GitHub-ready
  contract is now machine-checked. Asserts that `README.md`, `LICENSE`,
  `CHANGELOG.md` and `.gitignore` exist and are non-empty; the license is
  MIT with "Copyright (c) 2026 Pat Wendorf"; the README names the tool,
  documents every subcommand (`append`/`show`/`verify`/`export`/`sign`/
  `keygen`) and cross-links `spec.md`/`implementation.md`; the CHANGELOG is
  Keep-a-Changelog style with sections in newest-first order and its newest
  `## [x.y.z] - YYYY-MM-DD` section equal to the current package version
  (a version bump without a changelog section fails the suite); and
  `.gitignore` covers the Python build/test artifacts. The "full green
  test suite" half of the contract is an opt-in `PENGYOS_IT=1` test that
  re-runs `pytest -q` in a subprocess (the child clears `PENGYOS_IT`, so it
  can never recurse).

## [0.7.0] - 2026-09-14

### Added
- Ed25519 head signing (implementation.md item 7): chain heads can now be
  anchored to an ed25519 key for external verification.
- `pengyos_attest/ed25519.py`: pure-stdlib Ed25519 (RFC 8032 Appendix C)
  — CPython has no built-in ed25519, so the primitive is in-tree and pinned
  by the RFC 8032 §7.1 test vectors (`tests/test_ed25519.py`); no runtime
  dependency added.
- `pengyos_attest/signing.py`: detached head signatures over the pinned
  canonical message `b"pengyos-attest/head-v1\nseq=<N>\nhash=<64hex>\n"`
  (head `seq` + `hash` — the hash binds the whole chain prefix), stored in
  an append-only sidecar `<log>.sigs` (JSONL; each entry is self-verifying:
  `seq`, `head_hash`, `ts`, `public_key`, `sig`). Key handling: 32-byte
  seed / public key from raw-bytes or 64-lowercase-hex text files; a
  verify-key file may hold a seed (two-interpretation candidate semantics —
  the file that signed can verify); `generate_keypair` (seed 0600 +
  `PATH.pub` 0644 from `os.urandom`, never overwrites).
- New CLI subcommands: `sign --log L --signing-key FILE` (verifies the chain
  first — a corrupt or empty chain cannot be anchored; refuses duplicate
  (seq, head_hash, key) signatures; exit 0/1/2) and `keygen --seed PATH`
  (exit 0, or 1 when an existing file would be overwritten).
- `chain.sign_head(log_path, seed, *, ts=None)`: signs the current head
  under the same exclusive lock as append, appending the entry to the
  `.sigs` sidecar via the same atomic write as the chain.
- `verify --public-key FILE` / `verify_chain(..., public_key=...)`: the
  current head must be covered by a VALID signature from that key; whenever
  the `.sigs` sidecar exists, EVERY entry is re-checked (tampered entries
  fail even without the flag). A signed chain truncated away from its
  signed head therefore fails `--public-key` even without `--anchor` (the
  sidecar proves the lost tail). Chain findings (tamper/anchor) are
  reported before signature findings.
- `VerifyReport.signature_count`: number of sidecar entries when the
  sidecar was consulted (`None` otherwise).
- `tests/test_ed25519.py` (RFC 8032 §7.1 vectors in both directions +
  negative cases) and `tests/test_signing.py` (keygen, key files, entry
  tamper variants, sidecar, `sign_head`, `verify --public-key` semantics,
  CLI end-to-end incl. the full keygen→sign→verify operator flow).

## [0.6.0] - 2026-09-14

### Added
- Concurrency safety (implementation.md item 6): `append_event` now holds an
  **exclusive advisory lock** across the entire read-last-record → build →
  atomic-rename critical section, so concurrent appenders (threads or separate
  processes) are serialized and can no longer lose an update (two appends both
  reading the same head and writing the same `seq`) or clobber each other's
  write. A plain atomic rename alone does not prevent this; the lock does.
- `chain.lock_path(log)` / `chain.LOCK_SUFFIX` (`.lock`): the lock is a POSIX
  `fcntl.flock(LOCK_EX)` on a sibling `<log>.lock` sidecar file in the same
  directory as the log. The sidecar is a stable inode that is never renamed, so
  the lock survives the log's own `os.replace`; `flock` is released
  automatically when the descriptor is closed (including on crash), so a dead
  writer cannot wedge the chain. On non-POSIX platforms without `fcntl` the
  lock degrades to a no-op (single-writer). The `.lock` file is an expected,
  permanent sibling of the log — not a temp-file stray and not part of the
  chain. A corrupt-log `LogError` propagates with the lock released (no leak).
- `tests/test_concurrency.py`: the lock-file naming contract; a deterministic
  mutual-exclusion test (an append blocks while the lock is held and proceeds
  on release, proving the lock is real, not a no-op); no-lock-leak after a
  failed append; and the acceptance test — high-contention threaded appends
  (8×4, 6×3 via the CLI, 10×2) yielding a valid, link-consistent chain of the
  expected length (`verify` passes, contiguous `seq`, correct `prev_hash`
  linkage).

## [0.5.0] - 2026-09-14

### Added
- `show` (implementation.md item 5): `show --log L [--tail N] [--json]`
  pretty-prints the records (`seq=N ts=… type=…` plus full `hash:` and
  canonical `data:` lines), or emits one compact JSON line per record with
  `--json` (byte-identical to the stored line for valid records). `--tail N`
  limits the output to the last N records (N >= 1; `--tail 0`/negative is a
  usage error, exit 2). A missing/empty log prints nothing and exits 0; a
  corrupt log exits 1.
- `export` (implementation.md item 5): `export --log L --out F` bundles the
  whole chain with a Merkle-ish **root hash** into a self-contained JSON
  bundle (`format` = `pengyos-attest/export`, `format_version` = 1,
  `exported_at`, `record_count`, `head_hash`, `root_hash`, `records`),
  written atomically like the log. The source chain is verified first — a
  corrupt chain is refused (`LogError`, exit 1); a missing/empty log exports
  a valid empty bundle.
- `chain.merkle_root(hashes)`: Merkle root over the record hashes in seq
  order — parents are `sha256(ascii(left + right))`, an odd final node is
  duplicated before pairing (Bitcoin style), and the empty chain has the
  fixed root `sha256(b"")` (`chain.EMPTY_CHAIN_ROOT`).
- `chain.export_chain(log_path, out_path, *, ts=None)` and
  `chain.verify_export(bundle)`: the latter re-checks a bundle offline —
  format/version, the records walk under the same chain rules as
  `verify_chain` (shared walk), and `record_count`/`head_hash`/`root_hash`
  recomputation — returning `None` when consistent, else an error string.
- `tests/test_show.py` and `tests/test_export.py`: pretty/`--json`/`--tail`
  formats and exit codes, Merkle root structure (single/odd/even leaf counts,
  duplicate-odd rule, empty root), bundle round-trips, tamper detection in
  bundles (edited record, dropped tail, stale root/head/count, wrong format),
  corrupt-source refusal, atomic bundle writes, and CLI end-to-end coverage.

## [0.4.0] - 2026-09-14

### Added
- `verify` (implementation.md item 4): `chain.verify_chain(log_path,
  *, anchor=None)` walks the chain, recomputes every hash, and returns a
  structured `VerifyReport(ok, record_count, first_bad_seq, first_bad_line,
  reason)` instead of raising. Per non-blank line it checks JSON parsing,
  record schema + self-hash recomputation, `seq` contiguity from 1, and
  `prev_hash` linkage — so any field edit, reordering, duplication, gap, or
  broken link is detected at the FIRST bad seq (or file line, for a
  non-parseable line).
- Truncation semantics: a truncated log without an anchor is a
  self-consistent prefix and verifies OK (documented in `spec.md`); with
  `--anchor HASH` the chain must end at exactly that hash, otherwise the
  report is a truncation finding ("records after seq N were lost").
- The `verify` CLI subcommand is now real (`--log L [--anchor HASH]`):
  exit `0` with `OK: chain verified (N records)` on a clean chain (a
  missing/empty log is a valid empty chain), exit `1` with
  `FAIL: <reason> (first bad seq=N, line M)` on any finding or I/O error.
- `tests/test_verify.py`: the spec's four verification scenarios (3 appends
  OK, middle-record edit fails at the right seq, anchored truncation reports
  the lost tail / unanchored prefix stays valid, reorder fails) plus forged
  hashes, duplicates, gaps, unparseable lines, linkage checks, forward-compat
  extra keys, and full CLI exit-code coverage.

## [0.3.0] - 2026-09-14

### Added
- Atomic, crash-safe `append` (implementation.md item 3):
  `pengyos_attest/chain.py` reads the last record to derive the next
  `prev_hash` (last record's `hash`) and `seq` (last `seq` + 1) — a missing or
  empty log starts the chain at the genesis record — builds the next
  self-consistent record via `record.new_record`, and writes the log
  atomically (temp file + `fsync` + atomic `os.replace` rename) so a crash or
  a racing reader never sees a half-written line.
- Reader helpers: `chain.read_records`, `chain.last_record`, and
  `chain.chain_length`; a corrupt existing log is rejected with `LogError`.
- The `append` CLI subcommand is now real (`--log L --type T --data JSON`):
  exit `0` on success (prints `appended seq=N hash=…`), `1` on a corrupt log or
  I/O failure, `2` on malformed `--data` JSON or an invalid `--type`.
- `tests/test_append.py`: genesis/continuation, 3-record linked chain, any
  JSON payload, corrupt-log rejection, blank-line tolerance, no stray temp
  files, always-valid file after each append, and CLI exit-code coverage.

## [0.2.0] - 2026-09-14

### Added
- Record schema + canonical JSON serialization (implementation.md item 2):
  `pengyos_attest/record.py` defines the record fields
  (`seq`/`ts`/`type`/`data`/`prev_hash`/`hash`), the canonical JSON
  serialization (`sort_keys`, compact separators, `ensure_ascii`), and the
  hash rule `hash = sha256(prev_hash || canonical(record-without-hash))` with
  genesis `prev_hash = "0" * 64` — now fixed in `spec.md` with known-answer
  vectors.
- `new_record(...)` builds self-consistent records; `validate_record(...)`
  checks the schema, chain linkage (`expected_seq` / `expected_prev_hash`),
  and hash recomputation; `to_json_line` / `from_json_line` round-trip
  records as deterministic JSONL lines.
- `tests/test_record.py`: schema, canonicalization, hash-rule (known-answer),
  tamper detection, and JSONL round-trip tests.

## [0.1.0] - 2026-09-14

### Added
- Project scaffold (implementation.md item 1): `pyproject.toml` (PEP 621, MIT,
  `setuptools` build backend, stdlib-only), the `pengyos_attest` package, and the
  `pengyos-attest` console script.
- CLI argument surface (implementation.md item 1): `--version` plus the four
  documented subcommands — `append` (`--log L --type T --data JSON`), `show`
  (`--log L [--tail N] [--json]`), `verify` (`--log L`), and `export`
  (`--log L --out F`) — with the flags, defaults, and metavar in `spec.md`.
  The subcommand *logic* is tracked as its own subtasks in `implementation.md`
  and currently returns a clear `not implemented yet` message with a distinct
  exit code (`3`); `--version` and no-arg help are real and working.
- `python -m pengyos_attest` entry point alongside the console script.
- `README.md`, `CHANGELOG.md`, `LICENSE` (MIT, Copyright (c) 2026 Pat Wendorf),
  and `.gitignore`.
- Scaffold test suite (`tests/test_scaffold.py`) covering package metadata,
  entry-point behaviour, the CLI argument surface, and the declared entry point.
