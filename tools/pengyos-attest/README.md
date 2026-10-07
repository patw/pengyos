# pengyos-attest

A **tamper-evident, append-only hash-chained attestation log** for
[PengyOS](../../README.md): record build and change events as
an append-only JSONL chain where each record is linked to its predecessor by a
`sha256` hash, so that any modification, reordering, or truncation of a
committed record is detectable.

The trust record for everything PengyOS builds or changes (SPEC §1.5/§3).

## Status

| Subsystem | State |
|-----------|-------|
| Project scaffold (packaging, entry point, pytest) | ✅ done |
| CLI argument surface (`--version`, `append`, `show`, `verify`, `export`) | ✅ defined (subcommand logic scheduled) |
| Record schema + canonical JSON serialization | ✅ done |
| `append` (read last record, compute hash, atomic write + fsync) | ✅ done |
| `verify` (walk chain, recompute hashes, detect tamper/truncation/reorder) | ✅ done |
| `show` (pretty + `--json`) and `export` (Merkle-ish root hash) | ✅ done |
| Concurrency (file lock so appends can't corrupt the chain) | ✅ done |
| Optional ed25519 head signing (`sign` / `keygen` / `verify --public-key`) | ✅ done |
| `test_github_ready` (GitHub-ready contract test) | ✅ done |
| Export bundle format v2 (signature sidecar in the bundle) | ✅ done |
| Cross-process lock contention for `sign` (duplicate refusal under concurrent signers) | ✅ done |

See `spec.md` for the contract.

## Install

Requires Python ≥ 3.9. No third-party runtime dependencies (standard library
only). Install from the repository root:

```sh
pip install -e .
```

That provides the `pengyos-attest` console script. You can also run it
directly:

```sh
python -m pengyos_attest --version
```

## Usage

```
pengyos-attest [--version] {append,show,verify,export,sign,keygen} ...

pengyos-attest append --log L --type T [--data JSON]
    Append one event record to the hash chain at L.
        --log L        Path to the append-only JSONL hash-chain file (required).
        --type T       Event type (record ``type`` field). Default: event.
        --data JSON    Event payload as a JSON string. Default: null.

pengyos-attest verify --log L [--anchor HASH] [--public-key FILE]
    Verify the integrity of the chain at L: walk the chain, recompute each
    hash, detect tamper / truncation / reordering, and report the first bad seq.
        --log L          Path to the log file (required).
        --anchor HASH    The hash of a previously committed head record; the
                         chain must end at exactly this hash. This is what
                         makes a lost (truncated) tail provable — without it,
                         a self-consistent prefix verifies OK.
        --public-key F   A 32-byte ed25519 public-key (or seed) file: the
                         current chain head must be covered by a valid
                         signature from that key in the <log>.sigs sidecar;
                         every stored signature is also re-checked.

pengyos-attest sign --log L --signing-key FILE
    Sign the current chain head with an ed25519 key and append a
    self-verifying signature entry to the <log>.sigs sidecar (external
    anchoring). A corrupt or empty chain is refused; a head already signed
    by the same key is refused.
        --log L          Path to the log file (required).
        --signing-key F  32-byte ed25519 seed file (raw bytes or 64-hex
                         text); generate one with keygen.

pengyos-attest keygen --seed PATH
    Generate a fresh ed25519 keypair: the 32-byte seed at PATH (mode 0600)
    and the derived public key at PATH.pub (mode 0644). Never overwrites an
    existing file.

pengyos-attest show --log L [--tail N] [--json]
    Pretty-print the records in L (the last N with --tail), or one JSON record
    per line with --json.
        --log L        Path to the log file (required).
        --tail N       Show only the last N records.
        --json         Emit one record per line as JSON.

pengyos-attest export --log L --out F
    Bundle the log plus a Merkle-ish root hash that anchors the whole chain,
    written to F.
        --log L        Path to the log file (required).
        --out F        Path to write the export bundle (required).
```

Exit codes: `0` success, `1` an operation reported a failure (e.g. a failed
`verify`), `2` bad usage, `3` subcommand recognised but not yet implemented.

## Data model

Each log line is a JSON object:

```json
{"seq": 1, "ts": "2026-01-01T00:00:00Z", "type": "build", "data": {...}, "prev_hash": "...", "hash": "..."}
```

`hash = sha256(prev_hash || canonical(record-without-hash))`, where the
canonical serialization is `json.dumps` with sorted keys, compact separators,
and ASCII-only output. The genesis record uses `prev_hash = "0" * 64`. The
exact rule, field constraints, and known-answer vectors are fixed in `spec.md`
("Record schema and hash rule"); the Merkle root rule and the export bundle
format are pinned there as well ("Export").

## Append behaviour

`append` reads the last record to derive the next `prev_hash` (the last
record's `hash`) and `seq` (last `seq` + 1); a missing or empty log starts the
chain at the genesis record (`seq=1`, `prev_hash = "0" * 64`). The new record is
written **atomically**: the full log is written to a temp file in the same
directory, `fsync`'d, and moved into place with an atomic rename
(`os.replace`), so a crash or a racing reader never observes a half-written
line. A corrupt existing log is rejected (`LogError`).

## Concurrency (multi-writer safe)

Because the append is a read-last → build → **rename** sequence, a plain atomic
rename alone is not enough for concurrent writers: two appends could read the
same head and both write the same `seq`, one clobbering the other (a lost
update). `append` therefore holds an **exclusive advisory lock** (POSIX
`fcntl.flock`) on a sibling `<log>.lock` file across the whole critical
section, serializing appenders from other threads or processes so the chain is
never corrupted. The `.lock` sidecar is a stable, permanent sibling of the log
(not a temp-file stray, and not part of the chain) and is released automatically
when a writer exits or crashes. See `spec.md` ("Concurrency").

## Head signing (ed25519, external anchoring)

A chain head can be anchored to an ed25519 key so that an external observer
holding only the PUBLIC key can verify that the head (and everything before
it) was committed by the key holder. `keygen --seed PATH` creates the keypair;
`sign --log L --signing-key FILE` signs the current head; `verify --log L
--public-key FILE` requires the head to be covered by a valid signature.

Signatures are **detached**: they live in an append-only sidecar
`<log>.sigs` (one self-verifying JSON line each, carrying its own
`public_key`), not inside chain records — a `sig` key on a record would
change the record's own hash (extra keys participate in it). The signed
message is the head's `seq` + `hash` (a versioned canonical form pinned in
`spec.md`); since the record hash chains, it binds the whole prefix.
`verify` re-checks **every** stored signature whenever the sidecar exists
(tampered entries fail even without `--public-key`), so a signed chain that
was truncated away from its signed head fails `--public-key` — the sidecar
proves the lost tail even without `--anchor`. The ed25519 primitive is
pure standard library (`pengyos_attest/ed25519.py`, RFC 8032), pinned by
the RFC 8032 §7.1 test vectors. See `spec.md` ("Head signing").

## Show & export

`show --log L [--tail N] [--json]` prints the records — pretty by default
(`seq=N ts=… type=…` plus full `hash:` and canonical `data:` lines), or one
compact JSON line each with `--json`; `--tail N` limits the output to the
last N records. A missing/empty log prints nothing (exit 0); a corrupt log
exits 1.

`export --log L --out F` bundles the whole chain with a Merkle-ish **root
hash** that anchors the entire log in one sha256 — the bundle is
self-contained (`format`/`format_version`/`exported_at`/`record_count`/
`head_hash`/`root_hash`/`records`/`sigs`) and re-checkable offline via
`chain.verify_export(bundle)`. In format **version 2** the bundle also
carries `sigs`, a snapshot of the `<log>.sigs` head-signature sidecar (empty
list when the chain was never signed), so an observer holding only the
bundle can re-check every detached signature and see which keys anchored the
chain; v1 (pre-signing) bundles remain verifiable. The source chain is
verified before export, so a corrupt chain — including a corrupt or invalid
signature sidecar — is refused. See `spec.md` ("Show", "Export") for the
pinned format and the Merkle rule.

## Development

```sh
python3 -m pytest -q
```

The test suite is the objective function: it must be green (`pytest -q`).
Real system integration is opt-in behind `PENGYOS_IT=1`.
