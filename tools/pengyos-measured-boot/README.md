# pengyos-measured-boot

Boot under a software TPM (`swtpm`), read the Platform Configuration Registers
(PCRs), and verify them against an expected event-log policy.

This is a Python, **standard-library-only** CLI for the PengyOS project. The tool is designed to
run **without real TPM hardware, without root, and without network**: the
`swtpm` backend is an abstraction that is mocked in tests and can be swapped for
a real `swtpm` behind the opt-in `PENGYOS_IT=1` integration flag.

## Install

```bash
pip install -e .            # from a checkout
pip install -e '.[dev]'     # + pytest for the test suite
```

## Usage

```bash
# Boot an image, read PCRs, and compare to expected values.
pengyos-measured-boot run --image IMG --expect pcr7=0xdeadbeef [--json OUT]

# Verify committed PCRs against a policy file.
pengyos-measured-boot verify --pcrs pcrs.json --policy policy.toml [--json OUT]
```

`--expect` pairs are `PCR=VALUE` (repeatable). `VALUE` is parsed as hex
(`0x`-prefixed) or decimal.

## Status

Scaffold complete: package layout, CLI argument parsing/dispatch, GitHub-ready
files, and a green test suite. Both backends are wired: `verify` compares
committed PCRs against a policy file, and `run` boots an image under swtpm,
reads the PCRs, and verifies them against `--expect`.

The [`swtpm`](pengyos_measured_boot/swtpm.py) runner abstraction is in place: a
mockable `SwtpmRunner` (injectable subprocess layer) plus golden-assertable
argv builders. The [`pcrs`](pengyos_measured_boot/pcrs.py) module parses the
read-step stdout (`tpm2_getpcrs --format json`) and the committed fixture
format into a `Pcrs` container. The [`policy`](pengyos_measured_boot/policy.py)
module verifies PCRs against an expected-value policy; the `verify` backend
wires `--pcrs` + `--policy` through it. The [`boot`](pengyos_measured_boot/boot.py)
module orchestrates the `run` backend: boot + read + parse + verify against
`--expect`, composing the swtpm runner, the PCR parser, and the policy engine.

The `verify` command fails closed and specifically: a tampered or missing
required PCR is reported by name with the expected vs. actual value (exit code
2). See the committed [`policy.toml`](policy.toml),
[`pcrs.json`](pcrs.json), and [`pcrs-tampered.json`](pcrs-tampered.json).

## Development

```bash
pytest -q            # run the unit suite (no network / root / hardware)
PENGYOS_IT=1 pytest -q   # opt-in integration tests against real swtpm
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [`LICENSE`](LICENSE).
