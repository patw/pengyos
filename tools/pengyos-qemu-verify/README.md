# pengyos-qemu-verify

A **headless QEMU/KVM boot-test harness** for [PengyOS](../../README.md):
boot a PengyOS disk image under QEMU (q35), wait for readiness markers on the
serial console, run a set of assertions, and emit **JUnit XML + a JSON result**
so that "it boots" becomes an automated, machine-checkable fact.

Part of the PengyOS verification harness (SPEC §7).

> The **project-level** runbook — booting, SSH-ing into a guest, the serial console,
> and screenshotting a GL desktop — lives at
> [`../../docs/qemu-testing.md`](../../docs/qemu-testing.md). This tool is its
> headless-boot half.

## Status

| Subsystem | State |
|-----------|-------|
| Project scaffold (packaging, entry point, pytest) | ✅ done |
| CLI argument surface (`--version`, `boot`, `probe`) | ✅ defined; `probe`/`--dry-run` implemented |
| `smoke` (declarative matrix over an image) | ✅ done |
| `desktop` (the COSMIC session check, was `scripts/qemu-desktop-check.py`) | ✅ done (folded in 2026-10-05; `release.sh` calls it) |
| QEMU argv construction (KVM/no-KVM, kernel/disk) | ✅ done |
| UEFI (OVMF) argv wiring (locate firmware, pflash/`-bios`) | ✅ done |
| swtpm TPM2 argv wiring + process lifecycle | ✅ done |
| Boot launch + serial readiness detection (timeout-safe) | ✅ done |
| Assertion runner (present/absent log checks, per-check evidence) | ✅ done (wired into the boot flow) |
| JUnit XML / JSON emitters (`--json` / `--junit`, written even on failure) | ✅ done (reports carry the checks the boot flow attached) |
| `--dry-run` / `probe` (print the resolved argv, no side effects) | ✅ done |
| Integration test (`PENGYOS_IT=1`, real QEMU boots) | ✅ done (opt-in; discovers prebuilt images) |
| `test_github_ready` | ✅ done |

See `spec.md` for the contract.

## Install

Requires Python ≥ 3.9. No third-party runtime dependencies (standard library
only). Install from the repository root:

```sh
pip install -e .
```

That provides the `pengyos-qemu-verify` console script. You can also run it
directly:

```sh
python -m pengyos_qemu_verify --version
```

## Usage

```
pengyos-qemu-verify [--version] {boot,probe,smoke} ...

pengyos-qemu-verify boot <image>
    [--kernel BZIMAGE] [--uefi] [--tpm]
    [--ram MB] [--timeout SECONDS] [--settle SECONDS]
    [--expect REGEX]... [--json OUT] [--junit OUT]
    [--hostfwd SPEC]... [--dry-run] [--no-snapshot]

pengyos-qemu-verify probe <image>
    [--kernel BZIMAGE] [--uefi] [--tpm] [--ram MB] [--timeout SECONDS]
    [--hostfwd SPEC]... [--no-snapshot]

pengyos-qemu-verify smoke
    [--matrix PATH] [--image IMAGE] [--variant ID]...
    [--settle SECONDS] [--json OUT] [--junit OUT] [--dry-run]

pengyos-qemu-verify desktop <installer-image>
    [--out PREFIX] [--timeout SECONDS] [--settle SECONDS]
    [--no-screenshot] [--json OUT]
```

* `boot` — launch QEMU headless (swtpm too, with `--tpm`), stream the
  serial log, wait until **all** `--expect` markers have appeared (default
  `login:`) or `--timeout` elapses (QEMU is then terminated — the harness
  never hangs past the deadline), and print a readiness summary.
  With `--json OUT` / `--junit OUT` it also writes the JSON and JUnit XML
  reports — even on failure (including a missing image) — and prints each
  path. Once the boot is over, the default assertion set (`reached-login`,
  `no-kernel-panic`) is run over the captured serial log and each check is
  printed as a `check:    <name> PASS|FAIL — <evidence>` line; the exit code
  is `0` only when the boot is ready **and** every check passed (a ready boot
  whose log contains `Kernel panic` exits `1`). The checks still run over
  whatever was captured — a timeout or a QEMU launch failure gets its checks
  evaluated over the partial/empty log. The serial log (and its workdir) are
  kept for inspection.
* `probe` / `boot --dry-run` — print the resolved QEMU argv for inspection
  **without launching anything** (no workdir, no serial log, no swtpm, no QEMU).
  The output is a shell-pasteable command line; the only I/O is a read-only OVMF
  lookup, so a not-yet-built image/firmware renders too. Both entry points share
  one render path and print the identical argv.
* `--hostfwd SPEC` (repeatable) — forward a host port into the guest over QEMU
  user networking, e.g. `--hostfwd tcp::2222-:22` maps host port 2222 to the
  guest's sshd. Emitted as `-nic user,hostfwd=…`; omit it to keep QEMU's default
  NIC (no forwarding). Combine with a PengyOS image that ships sshd + an
  injected key to boot, `ssh -p 2222 root@127.0.0.1`, and inspect the live state.
* `smoke` — run a declarative **smoke matrix**: boot every variant declared in
  the matrix file (default `smoke/core.toml` — the PengyOS Core boot matrix:
  GRUB handoff, systemd, networkd/resolved, chrony, sshd, login, and no
  failed units, panics or I/O errors; `smoke/server.toml` covers the Server
  edition), evaluate that variant's checks over its captured serial log, and
  print a per-variant summary. `--variant ID` runs a subset; `--json`/`--junit`
  write the combined report (one JUnit `<testsuite>` per variant, so the usual
  CI/JUnit consumers read it unchanged). All checks are blocking — the exit
  code is `0` only when every variant booted ready **and** every check passed.
  `smoke --dry-run` prints the plan (variants, their checks, each variant's QEMU
  argv) and boots nothing.
* `--settle SECONDS` (`boot`, `smoke`) — keep reading the serial log for this
  many seconds **after** readiness, then terminate QEMU. Readiness is the login
  prompt; the login banner and the shell prompt follow it, and the matrices'
  `autologin-shell` check can only pass if they were captured. Default
  `DEFAULT_SETTLE` (4 s); `0` stops on readiness, which is the pre-0.4.0
  behaviour. The window is counted in the reported `duration`.
* `desktop` — the COSMIC session check, folded in from `scripts/qemu-desktop-check.py`
  on 2026-10-05 so the Desktop gate is one tool with one exit code. It boots the
  installer stick with a rendering GPU inside Xvfb, **types a probe on the
  guest's serial console**, and passes only if the console answered (a shell),
  `cosmic-comp`, `cosmic-panel` and `pengyos-install` are running, and the system
  reports no failed unit. Writes `PREFIX.png`, `PREFIX.serial.log`,
  `PREFIX.qemu.log`. Needs KVM, OVMF, Xvfb and ffmpeg. The pure half (argv,
  environment, report parsing) is unit-tested against real captures; the live
  half is what `release.sh` runs.
* **Artifact safety (default).** The guest disk is attached through a QEMU
  throwaway overlay (`snapshot=on`), so the backing image is opened **read-only**
  and a verification boot never writes to it — its hash stays stable, and the
  `sha256sum -c` users are told to run keeps working. Use
  `--no-snapshot` to write guest changes through to the image (only for boots
  that must persist guest state, e.g. an installer flow).

### Exit codes

| Code | Meaning |
|:----:|---------|
| `0` | Boot reached readiness and all checks passed |
| `1` | Boot/matrix ran but failed (timeout, QEMU failure, a failed check, missing image/firmware/swtpm) |
| `2` | Usage error (bad/missing arguments, unusable smoke matrix) |
| `3` | Reserved for a subcommand that is scheduled but not yet implemented (none currently) |

## Testing

The test suite needs no network, root, real QEMU, or a display — it uses
fixtures and a fake `qemu` script. Real integration tests are opt-in behind
`PENGYOS_IT=1`.

```sh
# full unit suite (must be green)
pytest -q

# opt-in integration tests (real QEMU + a prebuilt image required)
PENGYOS_IT=1 pytest -q
```

Integration tests are **strictly opt-in**: the boot tests skip unless
`PENGYOS_IT=1`, and skip again (with an actionable message) when no image is
found. Candidates are discovered in priority order: `PENGYOS_IT_IMAGE`
(optional `PENGYOS_IT_KERNEL`, `PENGYOS_IT_UEFI=1`), then the Core build
outputs in `~/build/pengyos/output/pengyos-core/images/`: `bzImage` +
`rootfs.ext4` (bare-metal), then `pengyos-core-x86_64.img` (UEFI, when OVMF is
installed). Per-boot timeout: `PENGYOS_IT_TIMEOUT` seconds (default 180).

## Project layout

```
pengyos-qemu-verify/
├── pengyos_qemu_verify/
│   ├── __init__.py      # package metadata (__version__)
│   ├── __main__.py      # python -m entry point
│   ├── cli.py           # argparse surface + dispatch
│   ├── argv.py          # QEMU argv construction + OVMF locator (pure, golden-tested)
│   ├── tpm.py           # swtpm process lifecycle (fake-tested)
│   ├── boot.py          # headless boot: launch, serial streaming, readiness, timeout
│   ├── assertions.py    # assertion runner: present/absent log checks → PASS/FAIL + evidence (pure)
│   ├── smoke.py         # declarative smoke matrix: loader, runner, JSON/JUnit emitters (pure)
│   └── reports.py       # JUnit XML + JSON emitters (pure) + file writers
├── smoke/
│   ├── core.toml        # the PengyOS Core smoke matrix (data, grounded in real logs)
│   └── server.toml      # the PengyOS Server smoke matrix
├── tests/
│   ├── conftest.py      # makes the package importable without install
│   ├── fixtures/        # captured serial-log samples + real Core boot captures
│   ├── test_scaffold.py # scaffold-level tests
│   ├── test_argv.py     # argv-construction goldens
│   ├── test_ovmf.py     # OVMF locator + UEFI wiring goldens
│   ├── test_tpm.py      # swtpm argv + lifecycle against a fake swtpm
│   ├── test_boot.py     # boot lifecycle against a fake qemu + fixtures
│   ├── test_assertions.py # assertion runner against log strings + fixtures
│   ├── test_reports.py  # JUnit/JSON emitters + CLI report wiring (fake qemu)
│   ├── test_smoke.py    # smoke matrix: validation, runner, reports, CLI, real-log grounding
│   ├── test_snapshot.py # artifact safety: disks are snapshotted by default
│   └── test_integration.py # opt-in real-QEMU boots (PENGYOS_IT=1) + discovery unit tests
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── LICENSE              # MIT
└── .gitignore
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
