# pengyos-server-toolset

The **Server edition's remote-admin comfort set** for
[PengyOS](../../README.md). It ships two things, both validated by a pytest suite:

1. **`fragments/server-toolset.fragment`** — a Buildroot *external fragment*
   that enables the comfort-package set as `BR2_PACKAGE_*` symbols.
2. **`shell/`** — a curated, idempotent shell profile (bash + zsh) plus
   `tmux.conf`, the `zoxide` init snippet, the `fzf` init snippet and the
   `starship` prompt config.

The whole point (SPEC §Purpose): a fresh SSH login has the expected tools, and a
session survives a dropped connection (tmux) with pleasant scrollback/search by
default.

## What ships

| Path | Purpose |
|------|---------|
| `fragments/server-toolset.fragment` | Buildroot `select`/`depends on` enables for the comfort packages. |
| `shell/profile` | bash/zsh login profile: PATH, aliases, starship prompt, fzf, zoxide. |
| `shell/tmux.conf` | tmux defaults: 24k/truecolor, sane key-challs, status line, mouse. |
| `shell/zoxide.sh` | `zoxide` shell init (sourced on shell start). |
| `shell/fzf.sh` | `fzf` shell init: `FZF_DEFAULT_COMMAND` (rg/find), opts, keybindings. |
| `shell/starship.toml` | `starship` prompt theme, `telemetry = false` (no phone-home). |

The comfort tool set (the promised tools): **mosh, tmux, ripgrep, fd, bat, eza,
fzf, zoxide, starship, jq** (and friends).

> The eight core tools ship an upstream Buildroot package; `fzf` (a Buildroot
> `golang-package`) and `starship` (a `cargo-package`) are shipped by this repo's
> own `package/{fzf,starship}/` Buildroot packages, so all ten are enabled by the
> fragment. See [`spec.md`](spec.md) for the contract.

## Verification (machine-checked by tests)

The suite asserts, against the committed artifacts:

- **tool table ↔ fragment consistency** (bijective) — every promised tool maps
  to exactly one real `BR2_PACKAGE_*` symbol and the emitted fragment round-trips
  from the tool table;
- **shell snippets pass `sh -n`** and `tmux.conf` parses via a small structural
  check;
- **no-telemetry scan** flags a planted phone-home URL (the test has teeth);
- the **GitHub-ready contract** (`test_github_ready`): `README.md`, `LICENSE`
  (MIT, "Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md`, `.gitignore`, and a
  full green test suite.

Run the suite:

```sh
python3 -m pytest -q
```

Drift gate: `python3 -m toolset validate [PATH]` runs the validator over the
committed fragment (or `PATH`) and exits non-zero with a report if the tool set
and fragment have drifted apart.

Real system integration (installing the packages, booting a target, sourcing the
profile in a real shell) is out of scope and opt-in behind `PENGYOS_IT=1`.

## Layout

```
fragments/server-toolset.fragment   # Buildroot BR2_ package enables
shell/                              # shell profile + tmux.conf + zoxide/fzf init + starship.toml
package/{fzf,starship}/             # Buildroot golang/cargo packages for fzf + starship
tests/                              # pytest suite (drift-proof validator + github-ready)
pyproject.toml                      # PEP 621, MIT, pytest configured on tests/
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).

See `spec.md` for the contract.
