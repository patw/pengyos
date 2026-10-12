# pengyos-install-tui

Guided **Core/Server installer wizard** + unattended **`--auto`** for PengyOS.

`pengyos-install-tui` is a from-scratch, **stdlib-only** Python tool that turns
the "how do I install PengyOS Core/Server" question into a deterministic,
offline-testable wizard — and into a machine-driven `--auto` run over
`install.toml` — without a GUI, so it works over console, serial, or SSH.

The wizard logic is a **pure state machine** (disk/target, edition, layout,
user, network, pengy, confirm) that produces the exact answer set
`pengyos-install` consumes. The `pengy` step — the assistant's endpoint — is the
one optional step: skipping it is a valid answer, and a plan that skips it
carries no `pengy` key at all. It never touches a disk or runs an install itself — it only decides
*what* to install and *where*.

## Status

**`install` is the console installer that actually installs.** It runs on the
live medium (console, serial or SSH): pick a disk from a numbered list (the
disk the live system runs from is shown but not selectable), a computer name,
a user and password (asked twice, hidden), an optional Pengy endpoint (base URL,
API key, model — Enter skips the whole page), type `erase` to confirm, and it
installs the medium's image through the same engine as the GUI
(`pengyos-install image`, see `tools/pengyos-install`) with a progress bar.
Bad input re-prompts with the reason; Ctrl-C or EOF before `erase` changes
nothing.

`run` / `auto` below only produce an answer set (disk, edition, layout, user,
network) — they never install, and that answer set does not match what the
engine can install today (the edition comes from the medium, and the only
layout is "erase the whole disk").

Both answer-set front-ends are implemented: the `install.toml` parser (`auto`) and the
interactive wizard engine (`run`). See [spec.md](spec.md) for the full contract.

## Layout

```
pengyos_install_tui/
  __init__.py   # public surface, __version__
  __main__.py   # python -m pengyos_install_tui
  cli.py        # argparse: run / auto subcommands, ExitCode convention
  model.py      # the pure wizard state machine — steps, validators, back-nav
  run.py        # render the model on stdin/stdout; --answers replay
  auto.py       # parse install.toml into the same answer set
tests/
  test_scaffold.py     # package/CLI import, version, parser wiring, exit codes
  test_model.py        # the pure wizard state machine
  test_auto.py         # the --auto install.toml front-end
  test_run.py          # the interactive run / --answers front-end
  test_github_ready.py # README/LICENSE/CHANGELOG/.gitignore/pyproject contract
```

## Usage

```console
$ pengyos-install-tui install [--payload DIR]
$ pengyos-install-tui run [--answers answers.json] [--json]
$ pengyos-install-tui auto --config install.toml [--json]
```

- **`run`** drives the wizard: render each step, apply the answer, and print
  the resulting plan. Replay it non-interactively with `--answers` (a JSON
  answer set) and/or emit the machine-readable plan with `--json`.
- **`auto`** parses an `install.toml` into the *same* answer set the
  interactive wizard produces, and prints the resulting plan (`--json` for the
  machine-readable form). A missing or invalid field is a reported error, not
  a crash.

Run it directly without installing:

```console
$ python3 -m pengyos_install_tui --help
```

## The wizard (model)

The wizard is a small, ordered state machine with no I/O, so the whole thing
is testable offline:

1. **target** — the target disk (e.g. `/dev/sdb`); validated as a block device
   at a documented minimum size.
2. **edition** — Core or Server.
3. **layout** — disk layout options (GPT + ESP + single ext4 root, the default).
4. **user** — the initial admin user (+ password handling).
5. **network** — network configuration.
6. **pengy** — the assistant's endpoint (base URL, API key, model). **Optional**:
   an empty answer is valid, so the step can always be skipped.
7. **confirm** — a final review of the derived plan before it is emitted.

Every step validates its input (an invalid answer is rejected with a message,
not a crash), the wizard supports **back-navigation** (return to the prior
step), and later steps **derive** their values from earlier answers.

## Safety

- The wizard is **pure**: it decides the plan; it never writes a disk or runs
  an install (that is `pengyos-install`'s job, and it is out of scope here).
- `auto` treats a missing/invalid `install.toml` field as a hard error with a
  message, never a traceback.

## Development

Python ≥ 3.11, standard library only (no third-party runtime dependencies).

```console
$ python3 -m venv .venv && . .venv/bin/activate
$ pip install -e '.[test]'
$ pytest -q
```

The test suite is fully hermetic: no network, no root, no real hardware.

### Integration test (opt-in: `PENGYOS_IT=1`)

`tests/test_integration.py` performs an opt-in end-to-end run (skipped unless
`PENGYOS_IT=1`). See [spec.md](spec.md).

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
