# pengyos-secureboot-enroll

Sign and enrol the PengyOS Secure Boot chain: generate/rotate the
PK/KEK/db trust root, sign the Unified Kernel Image (UKI) + systemd-boot, emit
the MOK / custom-key enrolment material, and lint that every EFI binary on the
ESP is signed by a key present in `db`.

This is part of the [PengyOS](../../README.md) project. Its purpose is signed,
measured boot that PengyOS controls, so an A/B update can be trusted without a
human watching.

## Commands

```bash
# 1. Generate / rotate the PK/KEK/db trust root + signed key metadata
pengyos-secureboot-enroll keys --out DIR [--rotate] [--json]

# 2. Sign the UKI + systemd-boot on the ESP, then verify the signatures
pengyos-secureboot-enroll sign-chain --esp DIR --keys DIR [--signer stdlib|openssl] [--json]

# 3. Emit MOK / custom-key enrolment material + operator instructions
pengyos-secureboot-enroll enroll --keys DIR --mode mok|custom [--json]

# 4. Lint the ESP: every EFI binary must be signed by a key present in db
pengyos-secureboot-enroll lint --esp DIR --keys DIR [--json]
```

Run `pengyos-secureboot-enroll --help` (or `<command> --help`) for the full
interface.

## Security posture

- Private keys are treated as highly sensitive. The policy is `0600` on private
  key files.
- The CLI **never prints key material**. Any command that could surface a key
  value fails closed instead of leaking it.
- The CLI **refuses to operate if key file permissions are wrong**. Before it
  touches existing private material it verifies every managed private key has no
  group/world bits (`0600` or stricter); a loosened key is treated as compromised
  and the command exits 1 instead of enrolling or signing with it.
- Signing runs through an injectable signer (standard library + an openssl-CLI
  shim). **No root, no real NVRAM** — nothing writes to firmware.
- External tools (`openssl`, `sbsign`, `sbverify`, `mokutil`) are mocked in
  tests; real system integration is opt-in via `PENGYOS_IT=1`.

## Install

```bash
pip install -e ".[dev]"
```

This exposes the `pengyos-secureboot-enroll` console script. The runtime depends
only on the Python standard library.

## Development

```bash
pytest -q
```

## Layout

- `pengyos_secureboot_enroll/cli.py` — the CLI dispatcher (argument parsing +
  routing).
- `pengyos_secureboot_enroll/truststate.py` — the trust-state data model: the
  PK/KEK/db key set and how each key is identified (SHA-256 fingerprint of its
  DER cert, the MOK/mokutil key id), plus the `EspManifest`/`EspBinary` set of
  EFI binaries the ESP must ship, each signed by a key. Pure data, no I/O.
- `pengyos_secureboot_enroll/bootchain.py` — the pinned PengyOS ESP boot-chain
  layout: the concrete set of EFI binaries the ESP ships (`/EFI/systemd/
  systemd-bootx64.efi`, the UEFI-spec fallback `/EFI/BOOT/BOOTX64.EFI`, and one
  UKI per installed version/slot under `/EFI/Linux/`). This is the manifest that
  `sign-chain` signs and `lint` verifies.
- `pengyos_secureboot_enroll/keys.py` — PK/KEK/db generation + rotation + signed,
  hashable key metadata (implemented). Generation runs through an injectable
  openssl shim; the trust root is loaded from disk (key id = SHA-256 of the DER
  cert) and the metadata is signed by the PK key.
- `pengyos_secureboot_enroll/sign_chain.py` — sign the UKI + bootloader, record
  each signature binding (sha256 + db key id), verify, and persist
  `signatures.json` (+ the Ed25519 verification-key sidecar) for `lint`.
  Implemented.
- `pengyos_secureboot_enroll/signer.py` — the injectable signer: a stdlib
  Ed25519 implementation (default) and an openssl-CLI shim (real EC P-256 ECDSA).
- `pengyos_secureboot_enroll/ed25519.py` — the pure-Python Ed25519 (RFC 8032)
  primitive the stdlib signer is built on.
- `pengyos_secureboot_enroll/enroll.py` — the `enroll` subtask (implemented):
  extracts the DER `db` certificate (the `mokutil --import` artefact) and builds
  the enrolment material in `--mode mok` (the DER cert + `mokutil --import`
  instructions) or `--mode custom` (a self-authenticating JSON bundle carrying
  the `db` key id, the DER cert, and the PK-signed key metadata) plus operator
  instructions. Public material (`0644`); the private keys are never read out;
  no firmware NVRAM is written.
- `pengyos_secureboot_enroll/lint.py` — the `lint` subtask (implemented):
  verifies, for each expected EFI binary on the ESP (the pinned boot-chain
  manifest), that it is present, signed by a key in `db`, and that the recorded
  signature verifies over the current bytes. Three rules -- `unsigned`,
  `untrusted_signer`, `signature_invalid` -- each fires a `LintViolation`; the
  ESP lints clean only when there are none. The signature checked is the
  binding `sign-chain` wrote to `signatures.json` (standing in for the Authenticode
  signature the firmware reads from the binary). No root, no real NVRAM.
- `tests/` — pytest suite, including `test_github_ready`.
