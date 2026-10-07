# pengyos-recipe-server-clis

Buildroot recipes for the PengyOS **Server** comfort CLIs (US-SRV-1 / US-SRV-3).

This repo authors the pinned [Buildroot](https://buildroot.org) recipes for the
Server remote-admin and monitoring CLIs that Buildroot 2026.08 does not ship.
Buildroot already ships `tmux`, `ripgrep`, `jq`, `fd`, `bat`, `eza`, and `yq`
in the Server toolset, but it lacks `zoxide`, `sd`, `node_exporter`, `btop`,
`helix`, and `neovim` — so the US-SRV toolset cannot be fully expressed until we
own those recipes.

> **Recipes are our data.** The tool catalogue is committed, and the Buildroot
> recipe files are emitted deterministically from it — mirroring the `fzf` /
> `starship` recipe shape already in `pengyos-server-toolset`.

## Tools

| Tool          | Buildroot symbol          |
| ------------- | ------------------------- |
| `zoxide`      | `BR2_PACKAGE_ZOXIDE`      |
| `sd`          | `BR2_PACKAGE_SD`          |
| `node_exporter` | `BR2_PACKAGE_NODE_EXPORTER` |
| `btop`        | `BR2_PACKAGE_BTOP`        |
| `helix`       | `BR2_PACKAGE_HELIX`       |
| `neovim`      | `BR2_PACKAGE_NEOVIM`      |

## CLI

```
pengyos-recipe-server-clis list [--json]
pengyos-recipe-server-clis emit --out DIR [--json]
pengyos-recipe-server-clis lint --dir DIR [--json]
```

- `list` — print the committed tool catalogue.
- `emit` — write `package/<tool>/{Config.in,<tool>.mk,<tool>.hash}` for every
  tool into `--out`, plus `fragments/server-clis.fragment`.
- `lint` — validate a recipe directory (hash present, version pinned exactly,
  source URL present, license declared) and exit non-zero on any violation.

## Layout

```
pengyos_recipe_server_clis/   # the Python package (catalogue, emit, lint)
package/<tool>/               # emitted Buildroot recipes (Config.in + .mk + .hash)
fragments/                    # emitted Buildroot config fragments
tests/                        # pytest suite (no network / root / display needed)
```

## Development

```
python3 -m pytest -q
```

Integration tests (validating the pinned hashes against the live upstream
tarballs) are opt-in behind `PENGYOS_IT=1`.

## License

MIT — Copyright (c) 2026 Pat Wendorf.
