# PengyOS attest — append-only hash-chained event log

## Summary
A tamper-evident, append-only attestation log for build and change events, with verification.

## Purpose
The trust record for everything PengyOS builds or changes (SPEC §1.5/§3).

## Deliverable
A Python CLI + library `pengyos-attest` providing `append`, `show`, `verify`, `export` over an append-only JSONL hash chain.

## Interface
- pengyos-attest append --log L --type build --data JSON
- pengyos-attest verify --log L [--anchor HASH] [--public-key FILE] | show --log L [--tail N] | export --log L --out F
- pengyos-attest sign --log L --signing-key FILE | keygen --seed PATH

## Record schema and hash rule (fixed 2026-09-14, iteration 2)
Each log line is a JSON object with required fields:

- `seq`: 1-based, contiguous integer (bool is not a valid seq).
- `ts`: ISO-8601 UTC string ending in `Z` (canonical form
  `YYYY-MM-DDTHH:MM:SSZ`; fractional seconds allowed). The schema pins the
  FORMAT; calendar plausibility is not validated.
- `type`: non-empty string.
- `data`: any JSON-serializable value (`null` is valid).
- `prev_hash`: 64 lowercase hex chars; the `hash` of the previous record.
- `hash`: 64 lowercase hex chars, computed as below.

Hash rule (the single definition; `pengyos_attest/record.py` implements it):

    canonical(record) = json.dumps(record minus "hash",
                                   sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True)
    hash = sha256(record["prev_hash"].encode("ascii")
                  + canonical(record).encode("utf-8")).hexdigest()

Genesis record: `prev_hash = "0" * 64` (GENESIS_PREV_HASH).

Because `prev_hash` is a field of the record, `hash` is a pure function of the
record alone: self-integrity needs no external state, and chain linkage
(record N+1's `prev_hash` == record N's `hash`) is a separate check. Extra
keys are tolerated and included in the hash (forward-compat, e.g. a future
`sig` field); mutating or adding an extra key invalidates the hash.

Known-answer vectors (pinned in `tests/test_record.py`):
- genesis record `{seq:1, ts:"2026-01-01T00:00:00Z", type:"build",
  data:{"commit":"abc123"}, prev_hash:"0"*64}` ->
  `5d133068004e620a7c8da3d15b1b927b5aae0ff08e6361452b98e9d674832347`
- `{seq:2, ts:"2026-01-01T00:00:01Z", type:"event", data:null,
  prev_hash:<genesis hash>}` ->
  `38eb473499a5f12025106c37f875ace960bdc064a07a320251c0436b67d02882`

## Append (item 3 — atomic, crash-safe)
`append` reads the last record to derive the next `prev_hash` (the last
record's `hash`) and `seq` (last `seq` + 1); a missing/empty log starts at the
genesis record. The new record is written atomically: the full log is written
to a temp file in the same directory, `fsync`'d (file, then best-effort
directory), then moved into place with an atomic rename (`os.replace`). The
resulting file is therefore always either the old content or the old content
plus the new complete line — never a torn line. A corrupt existing log is
rejected (`LogError`), so a bad head cannot be silently extended. Multi-writer
concurrency (a file lock) is the separate item 6 subtask; item 3 is correct
for a single writer.

## Concurrency (item 6, fixed 2026-09-14 iteration 6)
`append_event` holds an **exclusive advisory lock** across the entire
read-last-record → build → atomic-rename critical section, so concurrent
appenders (threads or separate processes) are serialized and no two writes
compute the same `seq` or clobber each other (the last-rename-wins / lost-update
race that a plain atomic rename alone does not prevent).

- The lock is a POSIX `fcntl.flock(fd, LOCK_EX)` on a **sibling sidecar file**
  `<log>.lock` (named by `chain.lock_path(log)`, suffix
  `chain.LOCK_SUFFIX == ".lock"`), in the SAME directory as the log. The sidecar
  is a stable inode that is never renamed, so the lock survives the log's own
  `os.replace`; locking the log itself would be defeated by the rename (the lock
  is on the old inode). `flock` is per-inode, advisory, inherited by `fork()`,
  and released automatically when the descriptor is closed — including on a
  crash — so a dead writer cannot wedge the chain.
- On platforms without `fcntl` (non-POSIX) the lock degrades to a no-op
  (single-writer only); the tool's target is Linux, where the lock is real.
- The `.lock` file is an **expected, permanent sibling** of the log (NOT a
  temp-file stray) and is not part of the chain; it may be created but is never
  deleted by `append`. A corrupt-log `LogError` propagates with the lock
  released (no lock leak).
- Acceptance: concurrent appends (threads) produce a valid chain of the
  expected length (`verify` passes, contiguous `seq`, correct `prev_hash`
  linkage).

**Cross-process acceptance for `sign` (fixed 2026-09-14, iteration 10):**
`sign_head` shares the SAME lock: its verify-chain → read-head → duplicate
check → sidecar read-modify-rename critical section runs under the same
exclusive `flock` on the same `<log>.lock` sidecar as `append_event`
(the chain walk before the lock is read-only). Because `flock` is
per-inode and shared across processes, the duplicate check is ATOMIC across
process boundaries: N barrier-synchronized signers in separate processes,
all signing the same head with the same key, yield EXACTLY ONE success —
every other process is refused by the duplicate check
(`SigningError` "already signed") — and the sidecar ends with exactly one
valid entry covering the head (a no-lock implementation lets all N "succeed"
and corrupts the sidecar with duplicates/lost updates). A `sign` in a child
process must block while another process holds the lock and proceed only
after release. Appenders and signers in separate processes interleave
safely through the one lock (complete, link-consistent chain; every
sidecar entry valid for a record really in the chain).

## Verification (tests are first-class)
- append 3 records → verify true; chain links correct.
- edit a middle record → verify fails with the right seq.
- truncate the log → verify reports the lost tail (if anchored) / still-valid-prefix semantics documented.
- reorder records → verify fails.
- concurrent appends (threads) produce a valid chain of expected length.

Fixed (2026-09-14, iteration 4): `chain.verify_chain(log_path, *, anchor=None)`
returns a structured `VerifyReport(ok, record_count, first_bad_seq,
first_bad_line, reason)`; it never raises for corrupt content (only `OSError`
propagates). Walk, per non-blank line in file order: JSON parse → record
schema + self-hash recompute → `seq` contiguity from 1 → `prev_hash`
linkage (first record against the genesis value). The walk STOPS at the first
failing record; `first_bad_seq` is that record's own seq, or
`first_bad_line` (1-based file line) when the line is not even a parseable
record. Semantics: a missing/empty log is a valid empty chain (ok); a
truncated log WITHOUT an anchor is a self-consistent prefix and verifies OK
(the lost tail is undetectable without external state); WITH `--anchor
HASH` the final record's `hash` must equal the anchor, otherwise the report
is a truncation finding ("records after seq N were lost"). The `verify`
CLI exits 0 with `OK: chain verified (N records)` on a clean chain and 1
with `FAIL: <reason> (first bad seq=N, line M)` on a finding or I/O error.

## Show (fixed 2026-09-14, iteration 5)
`show --log L [--tail N] [--json]` prints the records in the log (the last N
with `--tail`, N >= 1; `--tail 0` or negative is a usage error, exit 2).

- Pretty (default), one 3-line block per record:
  `seq=N ts=<ts> type=<type>` / `  hash: <64-hex>` / `  data: <canonical JSON of data>`.
  Tolerated extra keys (e.g. a future `sig`) are NOT shown in pretty mode.
- `--json` emits one compact JSON line per record (`sort_keys`, compact
  separators, `ensure_ascii`) — byte-identical to the line stored in the log
  for valid records.
- A missing/empty log is a valid empty chain: nothing is printed, exit 0
  (consistent with `verify`). A corrupt log is an operation failure (exit 1).

## Export (fixed 2026-09-14, iteration 5; format v2 fixed iteration 9)
`export --log L --out F` writes a self-contained bundle to F, written
atomically (temp file + fsync + rename, like the log). The SOURCE chain is
verified first; a corrupt source log (including a corrupt or invalid
signature sidecar) is refused (`LogError`, CLI exit 1). A missing/empty log
exports a valid empty bundle.

Bundle format (`format = "pengyos-attest/export"`, `format_version = 2`):

    {"format": "pengyos-attest/export", "format_version": 2,
     "exported_at": <ISO-8601 Z>, "record_count": N,
     "head_hash": <final record hash, or null>,
     "root_hash": <Merkle root>, "records": [<all records, seq order>],
     "sigs": [<every <log>.sigs sidecar entry, in file order>, or []
              when the sidecar does not exist]}

`sigs` (v2 addition, iteration 9) is a snapshot of the head-signature
sidecar so an observer holding ONLY the bundle can re-check every signature
(each entry is self-verifying — it carries its own `public_key`) and see
whether — and by which keys — the chain has been anchored. A stale
(signature no longer covers the head) entry is still carried: it is valid
stored anchoring evidence, and head COVERAGE is a `verify --public-key`
concern, not a bundle concern. The pre-export `verify_chain` already
re-validates every sidecar entry, so a bundle never carries a corrupt or
invalid entry; export of a chain whose sidecar is corrupt/invalid is
refused. The v1 shape (`format_version = 1`, NO `sigs` key) is the
pre-signing format; v1 bundles remain verifiable.

Merkle root rule (the one and only definition; `chain.merkle_root`):
leaves are the records' `hash` values in seq order; each parent is
`sha256(ascii(left + right)).hexdigest()` of adjacent nodes; an odd final
node is DUPLICATED before pairing (Bitcoin style); an empty list has the
fixed root `sha256(b"")` (`chain.EMPTY_CHAIN_ROOT`).

`chain.verify_export(bundle) -> None | str` re-checks a bundle WITHOUT the
original log: format/version (both 1 and 2 accepted, `chain.
SUPPORTED_EXPORT_FORMAT_VERSIONS`), records walk under the same chain rules
as `verify_chain` (schema + self-hash + seq contiguity + prev_hash linkage),
`record_count` / `head_hash` / `root_hash` (recomputed) agreement, and — for
v2 — `sigs` must be a list whose entries are well-formed (shape per
`signing.entry_shape_error`, the SAME definition `load_signatures` uses) and
carry a VALID ed25519 signature over their own seq/head_hash under their
own public_key (`signing.entry_is_valid`); v1 bundles must NOT carry a
`sigs` key at all. Any edit, dropped tail, stale hash, or tampered/invalid
signature entry is reported as an error string.

## Head signing (item 7, fixed 2026-09-14, iteration 7)
Optional ed25519 signing of the chain head for external anchoring.

**Design decision (why detached):** a signature CANNOT be a field of the head
record — extra keys participate in the record's self-hash, so a `sig` key
would change the very hash it is supposed to cover (circular). Signatures are
therefore *detached* and stored in an append-only sidecar
`<log>.sigs` (JSONL, sibling of the log, compact sorted-key lines like the
chain; named by `signing.sigs_path`, suffix `signing.SIGS_SUFFIX == ".sigs"`;
same atomic write + same exclusive lock as the chain). Each entry is
self-verifying because it carries its own public key::

    {"seq": N, "head_hash": <64 hex>, "ts": <ISO-8601 Z>,
     "public_key": <64 hex>, "sig": <128 hex>}

**Signed message** (`signing.head_message`, the one and only definition):

    b"pengyos-attest/head-v1\n" + b"seq=<N>\n" + b"hash=<64 hex>\n"

i.e. the head's chain POSITION (`seq`) and CONTENT (`hash`, which through the
hash rule binds the whole chain prefix). Ed25519 is deterministic: same head
+ seed ⇒ identical signature (re-signable, diffable). The ed25519 primitive
is in-tree pure stdlib (`pengyos_attest/ed25519.py`, RFC 8032 Appendix C)
pinned by the RFC 8032 §7.1 vectors in `tests/test_ed25519.py`.

**Keys:** a *signing key* is a 32-byte seed; a *verification key* is a
32-byte public key. Key files hold exactly 32 raw bytes OR a text file with
the 64-character *lowercase* hex of those bytes (whitespace tolerated).
A verify-key file may also hold a *seed*: 32 bytes have (up to) two valid
interpretations (raw as public key if on-curve; always as a seed with the
derived key), and an on-curve seed makes them ambiguous, so
`signing.verification_key_candidates` returns up to both and a check passes
iff one candidate matches — "the file that signed can verify".
`keygen --seed PATH` writes the seed (mode 0600) and derived public key to
`PATH.pub` (mode 0644) from `os.urandom`; it NEVER overwrites an existing
file. `sign` verifies the chain first (a corrupt chain cannot be anchored,
`LogError`), signs the head under the chain's exclusive lock, and REFUSES to
duplicate a (seq, head_hash, key) already signed (`SigningError`).

**Verify semantics:** `verify --public-key FILE` / `verify_chain(
public_key=...)` — after the chain walk (and any anchor mismatch; chain
findings are reported BEFORE signature findings), the sidecar must contain a
VALID entry with `seq` == head seq, `head_hash` == head hash, and an entry
`public_key` among the key's candidates. Whenever `<log>.sigs` EXISTS,
EVERY entry is re-checked for self-consistency even without `--public-key`
(a tampered entry fails). Consequences: a once-signed chain truncated away
from its signed head FAILS `--public-key` (the sidecar proves the lost tail
even without `--anchor`); an old signature for a superseded head remains a
valid stored entry and does not cover the new head. An empty chain with
`--public-key` fails (no head to sign). `VerifyReport.signature_count`
carries the number of sidecar entries when the sidecar was consulted
(`None` otherwise).

**CLI exit codes:** `sign`: 0 (prints `signed seq=N head=… key=…`), 1
corrupt/empty chain or duplicate, 2 bad key file. `keygen`: 0 (prints paths
+ `public key: <64 hex>`), 1 would overwrite an existing file. `verify
--public-key`: 0 (OK line plus `head signed by key <16 hex>...`), 1
unsigned/wrong-key/tampered sidecar, 2 bad key file.

## GitHub-ready contract (item 8, fixed 2026-09-14)
The repository must be ready to push to GitHub at ANY commit.
`tests/test_github_ready.py` asserts the contract:

- `README.md`, `LICENSE`, `CHANGELOG.md`, `.gitignore` exist and are
  non-empty.
- `LICENSE` starts with "MIT License" and contains
  `Copyright (c) 2026 Pat Wendorf`.
- The README names the tool, mentions EVERY documented subcommand
  (`append`/`show`/`verify`/`export`/`sign`/`keygen`), and cross-links
  `spec.md` — docs stay in sync with the interface.
- `CHANGELOG.md` is Keep-a-Changelog style (`# Changelog` heading,
  `## [x.y.z] - YYYY-MM-DD` sections in newest-first order) and its NEWEST
  section equals the current package `__version__`. A version bump without a
  matching changelog section fails the suite.
- `.gitignore` covers the Python build/test artifacts (`__pycache__/`,
  `*.py[cod]`, `*.egg-info/`, `.pytest_cache/`, `build/`, `dist/`).
- "Full green test suite": an OPT-IN `PENGYOS_IT=1` test re-runs
  `pytest -q` in a subprocess from the repo root and asserts exit 0; the
  child process clears `PENGYOS_IT` so the run can never recurse. It is
  skipped by default because the outer `pytest -q` already runs on
  every iteration (that run IS the green check in the ordinary loop).

## Acceptance criteria
- Any modification/removal is detected by verify.
- Appends are atomic and crash-safe.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Distributed consensus
- Being a general logging system

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
