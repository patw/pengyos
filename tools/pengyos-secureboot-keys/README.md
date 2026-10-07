# pengyos-sbkeys

Manage PengyOS Secure Boot key material (PK / KEK / db) and generate the signing
commands for a Unified Kernel Image (UKI).

This is part of the [PengyOS](../../README.md) project. Its purpose is signed,
measured boot that PengyOS controls.

## Security posture

- Private keys are treated as highly sensitive. The policy is `0600` on
  private key files.
- The CLI **never prints key material**. Any command that could surface a key
  value fails closed instead of leaking it.
- The CLI **refuses to operate if key file permissions are wrong**. Before
  `enroll-commands` and `sign` touch existing private material they verify every
  managed private key has no group/world bits (`0600` or stricter); a loosened
  key is treated as compromised and the command exits 1 instead of enrolling or
  signing with it.
- External tools (`cert-to-efi-sig-list`, `efi-sig-list`, `sbsign`, `sbverify`)
  are mocked in tests; real system integration is opt-in via `PENGYOS_IT=1`.

- `init` produces **real** key material with the `openssl` command-line tool
  (EC P-256 private keys + self-signed X.509 certificates). `openssl` must be
  available on `PATH` to run `init`; the generated private keys are written
  `0600` and the summary never prints key material.

## Install

```bash
pip install -e .
```

This exposes the `pengyos-sbkeys` console script. The runtime depends only on
the Python standard library.

## Usage

```bash
# 1. Generate PK/KEK/db key material under keys/
pengyos-sbkeys init --out keys/

# 2. Print the commands that enroll PK/KEK/db into firmware
pengyos-sbkeys enroll-commands --keys keys/
pengyos-sbkeys enroll-commands --keys keys/ --json

# 3. Sign a UKI with the db private key
pengyos-sbkeys sign uki.efi --key keys/DB.key --cert keys/DB.cert
```

Run `pengyos-sbkeys --help` (or `<command> --help`) for the full interface.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
