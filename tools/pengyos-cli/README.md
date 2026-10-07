# pengyos — the meta-CLI

`pengyos` is one entry point over the PengyOS toolchain. PengyOS is organised as many small,
bounded tools under `tools/` (build, verify, triage, profile, sbom, …). This
crate is the thin, scriptable surface that ties them together:

```text
pengyos build <target> | verify <image> | triage | profile | sbom <artifact> | doctor
```

## Purpose

A coherent, ownable toolchain surface (see the project spec): a single binary
with a consistent UI, consistent `--help`/exit-code behaviour, and consistent
`--json` output, instead of a scatter of Python scripts with different flags.

## Layout

This is a single Cargo package that builds both a library and a binary:

```text
tools/pengyos-cli/
├── Cargo.toml          # package manifest (lib `pengyos_cli` + bin `pengyos`)
├── src/
│   ├── lib.rs          # version, documented subcommand surface, framework
│   ├── args.rs         # clap-style argument parsing (std-only)
│   ├── config.rs       # minimal std-only TOML config loading
│   ├── dispatch.rs     # subcommand registry + injectable Runner
│   ├── doctor.rs       # native host-readiness check
│   ├── json.rs         # std-only JSON emitter for the `--json` schema
│   └── main.rs         # the `pengyos` binary (thin argv entry point)
├── tests/
│   ├── version.rs      # integration tests: spawn the real binary, check exit codes
│   └── github_ready.rs # GitHub-ready contract (README/LICENSE/CHANGELOG/.gitignore)
├── README.md
├── CHANGELOG.md
├── LICENSE
└── .gitignore
```

The library is **standard-library only** on purpose, so it builds and tests
fully offline. Any dependency is declared in `Cargo.toml`, added per feature.

## Building

```console
$ cargo build --offline        # no network needed for the std-only scaffold
$ cargo run --offline -- --version
pengyos 0.1.0
```

## Usage

```text
pengyos — the PengyOS meta-CLI

USAGE:
    pengyos <command> [args]

COMMANDS:
    build        build a target image
    verify       verify a built image
    triage       triage update candidates
    profile      profile the host
    sbom         emit a software bill of materials
    doctor       report host readiness

OPTIONS:
    -V, --version   Print version and exit
    -h, --help      Print this help and exit

Run `pengyos <command> --help` for command-specific help.
```

Each command shells out to the matching Python tool under `tools/`
(`pengyos-build`, `pengyos-verifier`, …).

## Error surfacing

Each subcommand forwards its parsed arguments to its sibling tool. When the tool
exits non-zero, `pengyos` prepends a one-line annotation to stderr naming the
subcommand, the resolved tool, and the exit status — so a failure is visible even
when the tool itself is quiet:

```console
$ pengyos build x86_64
pengyos build: `pengyos-build` exited with status 1
pengyos-build: error: target `x86_64` is not in the targets file
```

The tool's own stdout/stderr pass through untouched; a successful run is left
unchanged. The meta-CLI's own exit code is the tool's exit code.

## `--json` output

Every subcommand can emit a stable machine-readable document instead of the human
report, by passing `--json` anywhere in `argv`:

```console
$ pengyos build x86_64 --json
{"command":"build","executable":"pengyos-build","args":["x86_64"],"exit_code":0,"stdout":"","stderr":"","ok":true}
```

The document carries the subcommand, the resolved tool, the forwarded argv, the
exit code, the captured stdout/stderr, and an `ok` flag (`exit_code == 0`).
`doctor` additionally embeds its readiness matrix under a `checks` array, so
callers can always read `exit_code` / `ok` uniformly:

```console
$ pengyos doctor --json
{"command":"doctor","executable":"pengyos-doctor","args":[],"exit_code":1,"stdout":"","stderr":"","ok":false,"checks":[...]}
```

`--json` is a global flag consumed by the meta-CLI: it is never forwarded to the
sibling tool, and in JSON mode the tool's raw stderr is captured verbatim (no
text-mode annotation is added), so scripts get a clean single-line JSON document
on stdout.

## Configuration

Global configuration is optional and expressed in TOML (the same format the
sibling `pengyos-tiers` tool uses). The first file found wins, in this order:

1. `$PENGYOS_CONFIG` (an explicit path),
2. `$XDG_CONFIG_HOME/pengyos/config.toml`,
3. `~/.config/pengyos/config.toml`.

A missing file is not an error — an empty configuration is used. A present but
invalid file is a hard error. The one key used so far maps a subcommand to its
sibling tool's executable name:

```toml
[tools]
build = "pengyos-build"
verify = "pengyos-verifier"
```

Any subcommand without an entry falls back to the convention `pengyos-<name>`.

## Doctor — host readiness

`pengyos doctor` answers the question *"is this host ready to build PengyOS?"*
before anything else runs. It evaluates a fixed readiness matrix and prints a
scriptable report:

```text
$ pengyos doctor
pengyos doctor — report host readiness
READINESS:
  [PASS] buildroot pinned 2026.02.3
  [PASS] kvm       /dev/kvm available
  [FAIL] tools     missing: pengyos-build, pengyos-verifier, ...

1 required check(s) failed. Host is not ready.
```

The checks:

| Check       | What it verifies                                             | Severity  |
|-------------|-------------------------------------------------------------|-----------|
| `buildroot` | A well-formed Buildroot pin (`YYYY.MM[.N]`) is known         | required  |
| `kvm`       | `/dev/kvm` is readable (QEMU hardware acceleration)          | optional  |
| `tools`     | The sibling PengyOS tools are on `PATH`                      | required  |

Exit code is `0` when every required check passes (host is ready), `1` when a
required check fails, and `2` for a usage error. Run with `--verbose` to annotate
soft warnings with their severity.

## Exit codes

| Code | Meaning                                             |
|-----:|-----------------------------------------------------|
| `0`  | Success (including `--help` / `--version`)          |
| `2`  | Usage error (missing command, unknown command, bad flags) |

## Testing

Tests need no network, root, display, or hardware — external tools are mocked
and real system integration is opt-in behind `PENGYOS_IT=1`.

```console
$ cargo test --offline
```

The suite asserts the documented subcommand surface, the binary's exit codes,
and the GitHub-ready contract.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
