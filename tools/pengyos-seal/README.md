# pengyos-seal

Wire provenance into a pengyos-build output — the closing glue for **US-CORE-6**.

This is part of the [PengyOS](../../README.md) project. Its purpose is signed,
measured boot that PengyOS controls. `pengyos-seal` is the glue that turns a
raw pengyos-build output into a build the external witness can verify: it signs
the expected artifacts, places the SBOM at the verifier's conventional root
name, surfaces the repro report, builds the witness layout, and runs
`pengyos-verifier` — in one command. It **never builds** and **never writes
outside the build directory** it is given.

## What it does

Given a pengyos-build output directory and a signing key, `pengyos-seal run`
closes the provenance loop in order:

1. **sign** — invoke `pengyos-sign` for each expected artifact
   (`images/rootfs.ext4`, `images/bzImage`), producing the `pengyos-sig-v1`
   detached signature sibling.
2. **name** — normalise the SBOM to the verifier's conventional root name
   (`sbom.spdx.json`) so the witness can find it.
3. **repro** — surface the reproducibility report at the conventional root name
   (existing, or freshly diffed against `--prev`).
4. **witness** — build the witness layout with `pengyos-witness-bundle`.
5. **verify** — run `pengyos-verifier` and surface its verdict + exit code.

Every external tool call (`pengyos-sign`, `pengyos-witness-bundle`,
`pengyos-verifier`, `pengyos-repro-report`) goes through an **injected runner**
seam, so the suite stays offline, root-free, and free of real artifacts: tests
inject a recording in-memory runner instead of touching the host or the
network.

## Install

```bash
pip install -e ".[dev]"
```

This exposes the `pengyos-seal` console script. The runtime depends only on the
Python standard library; `pytest` is a dev-only dependency.

## Usage

```bash
# 1. Enumerate the seal plan for a build dir (no side effects).
pengyos-seal plan --build DIR
#    ...or emit the plan as JSON:
pengyos-seal plan --build DIR --json

# 2. Seal a build dir: sign, name, repro, witness, verify.
pengyos-seal run --build DIR --key SEED
#    ...with a public key for cryptographic verification, an attest log, and a
#    previous build for the repro diff:
pengyos-seal run --build DIR --key SEED --pub KEY.pub --attest LOG --prev PREV
#    ...and capture the machine-readable seal result (to a file, or stdout with
#    `--json` / `--json -`):
pengyos-seal run --build DIR --key SEED --json OUT
```

The `plan` stage is a deterministic, side-effect-free enumeration of the seal
steps. The five `run` stages — **sign**, **name**, **repro**, **witness**, and
**verify** — are wired into `Seal.run` in provenance order (`sign` → `name` →
`repro` → `witness` → `verify`). The witness builds the layout with
`pengyos-witness-bundle`; `pengyos-verifier` is the closing gate — its exit code
is the verdict, and `pengyos-seal run` returns it unchanged.

### Overriding the tool binaries

Each external tool can be pointed at an explicit binary with a `--*-bin` flag
(`--sign-bin`, `--verify-bin`, `--witness-bin`, `--repro-bin`). This is the
production seam for the injected runner: it lets a caller pin a specific build
of a tool without shadowing `PATH`.

## The seal plan

`plan` discovers the real artifact/SBOM names in `--build` (reusing the
`pengyos-witness-bundle` alias convention: the build emits
`images/<target>.spdx.json`, the witness reads `sbom.spdx.json`) and enumerates
the ordered steps the seal will take — `sign` → `name` → `repro` → `witness` →
`verify` — with the exact argv each step would run. It is deterministic and has
no side effects (it only reads the build tree). `--json` emits the machine
view (discovered `target`/`sbom_source`/`repro_source`, the repro status, and
each step's argv) instead of the text view.

The expected artifacts and conventional names are pinned to the
`pengyos-verifier` check definitions so the producer and the witness cannot
drift:

| Role | Conventional name | Source of truth |
|------|-------------------|-----------------|
| Expected artifacts | `images/rootfs.ext4`, `images/bzImage` | `pengyos-verifier` `EXPECTED_ARTIFACTS` |
| SBOM root name | `sbom.spdx.json` | `pengyos-verifier` `SBOM_FILENAMES[0]` |
| Repro report | `repro-report.json` | `pengyos-verifier` `REPRO_REPORT_FILENAME` |
| Signature suffix | `.sig` | `pengyos-verifier` `SIGNATURE_SUFFIX` |

The signing steps show a `<SEED>` placeholder in the plan (the key seed is a
human boundary and `plan` takes no `--key`); `run` fills in the real seed.

## Machine-readable output

`pengyos-seal run` surfaces its result on stdout (one line per stage). Add
`--json OUT` to also write the full machine-readable seal result to `OUT` (or to
stdout with `--json` / `--json -`):

```json
{
  "build_dir": "...",
  "plan": { "target": "m2", "sbom_source": "images/m2.spdx.json", "steps": [...] },
  "signed": ["images/rootfs.ext4", "images/bzImage"],
  "names": {
    "sbom": { "source": "images/m2.spdx.json", "root": "sbom.spdx.json" },
    "repro": { "source": "images/repro-report.json", "root": "repro-report.json" }
  },
  "repro": { "status": "existing" },
  "verifier": {
    "exit_code": 0,
    "verdict": "PASS",
    "checks": { "sbom": "PASS", "signatures": "PASS", "repro-score": "PASS",
                "expected-files": "PASS", "attest-chain": "PASS" }
  }
}
```

The JSON is the seal's own record of what it did (the sealed plan, the artifacts
signed, the names normalised to the verifier's conventional root names, the repro
status) plus what `pengyos-verifier` reported: its per-check verdicts (`checks`),
overall `verdict`, and `exit_code`. `checks` is populated only when the verifier
emits a structured `{"checks": {...}}` summary — the seal records only what the
verifier actually reports, never invents a check. Every field is `null` where a
stage did not run, so a downstream tool consumes it without a second pass.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

Integration tests (real system calls against a real build dir) are opt-in behind
`PENGYOS_IT=1`; unit tests need no network, root, display, or hardware.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
