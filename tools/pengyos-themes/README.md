# pengyos-themes — "Themes"

Icon themes, colour themes and **Looks** (an icon theme and a colour theme
together) for the COSMIC desktop, installed **per user**: nothing in the image
changes, and what you pick lives in your home directory on the `data` partition,
so it survives A/B updates. The contract is [`spec.md`](spec.md).

Status (0.3.0): the **Themes window** and the CLI ship in the Desktop, in one
binary: `pengyos-themes` with no arguments opens the window (App Library, System
folder), with arguments it is the CLI. The Desktop also takes its look from here:
the catalogue's `[defaults]` (Glacier for dark, Glacier Light for light, with
Papirus, which ships in the image) are generated into its COSMIC defaults.

The window has three pages: **Looks** (the front page: a card per Look with a
preview in its own colours and icons, Apply, and *Save as a Look*), **Icons**
(the installed themes with previews and Use; the catalogue's packs with Install,
Update, Remove) and **Colours** (your library and the catalogue's, and a
cosmic-themes.org browser). Undo and Reset are in the header. Downloads run off
the UI thread; everything else is the same library calls as the CLI.

```
pengyos-themes icons list | install <id> | use <theme> | remove <id>
pengyos-themes colours search [TEXT] [--order popular|new|name] [--color HEX] [--page N]
pengyos-themes colours apply <id|file.ron> | library | forget <id>
pengyos-themes looks list | apply <id> | save <name> | forget <id>
pengyos-themes undo | reset | status [--json]
pengyos-themes follow                    # the session helper (autostart)
pengyos-themes system-defaults DIR       # the image's COSMIC defaults (see below)
```

- **Icons.** The [catalogue](catalogue/catalogue.toml) lists complete themes only
  (each must pass `board/desktop/check-mime-icons.py`). `install` downloads the
  pack, refuses it unless the sha256 matches the catalogue, and unpacks only the
  declared theme directories into `~/.local/share/icons`, where COSMIC Settings'
  icon picker finds them. `use` writes `com.system76.CosmicTk` `icon_theme`, as
  Settings does; every app switches at once. `remove` goes back to Cosmic first
  if the theme was in use.
- **Colours.** `search` browses [cosmic-themes.org](https://cosmic-themes.org)'s
  public API (no account, nothing sent about you); themes this COSMIC cannot
  parse are left out. `apply` takes an id from the last search, a catalogue or
  library id, or a `.ron` file, and writes it exactly as Settings' *Import* does.
  Every theme you apply is kept in your library
  (`~/.local/share/pengyos-themes/colours/`), so it works offline later.
- **Looks.** Four curated ones ship (Glacier, Glacier Light, Meadow, Ember: Papirus
  icons with colour themes of our own); `looks save` keeps what the desktop shows now.
  Applying a Look installs its icons if needed, then changes everything in one
  step.
- **Undo.** Before any change the tool snapshots every key it may write;
  `undo` puts the last one back byte for byte (20 steps kept). `reset` restores
  COSMIC's icons and its shipped colours (dark or light stays as you have it),
  and is itself undoable.

- **Icons follow dark and light.** COSMIC has one icon theme for both modes.
  `follow` runs in each Desktop session and swaps a pack's declared pair
  (Papirus Dark / Papirus Light) when the mode changes, from Settings or the
  sunset switch. Any other icon theme is left alone.
- **The image's defaults.** `board/desktop/theme-defaults` is generated:

      PENGYOS_THEMES_CATALOGUE=catalogue/catalogue.toml \
        cargo run -q -- system-defaults ../../buildroot-external/board/desktop/theme-defaults

  Rerun it after changing a default Look's colours or bumping the libcosmic pin;
  `tests/cli.rs` fails while the committed files differ.

It refuses to run as root for anything that changes state: themes are per user.

## Layout

| Path | What |
| --- | --- |
| `src/catalogue.rs` | the catalogue format and every rule it must keep |
| `src/fetch.rs` | downloads over curl: https only, a size cap and a timeout on every request |
| `src/unpack.rs` | the safe `.tar.gz` unpacker |
| `src/icons.rs` | install / use / remove |
| `src/colours.rs` | parse, search, the library, apply, what is in use now |
| `src/looks.rs` | Looks |
| `src/desktop.rs` | the COSMIC config keys, undo snapshots, reset, the image's defaults |
| `src/follow.rs` | icons that follow dark and light |
| `src/main.rs` | the CLI (and, with no arguments, the window) |
| `src/app.rs` | the Themes window (feature `cosmic`) |
| `catalogue/` | the catalogue and our colour themes (to `/usr/share/pengyos/themes/`) |

`cosmic-theme` and `cosmic-config` are pinned to the libcosmic rev the Desktop's
COSMIC is built with (`scripts/cosmic-libcosmic.pin`), because the keys written
here are read by that COSMIC; bump them together.

## Tests

```sh
cargo test        # in the build box: export PATH="$HOME/.cargo/bin:$PATH"
```

Everything runs over temporary homes with the network stubbed: checksum and
hostile-archive refusals leave nothing behind, Settings-equivalent writes, undo
byte for byte, offline Looks, and the shipped catalogue's validity.

To try the CLI without touching your desktop, point it at a scratch home:
`XDG_CONFIG_HOME=/data/tmp/t/config XDG_DATA_HOME=/data/tmp/t/data XDG_CACHE_HOME=/data/tmp/t/cache PENGYOS_THEMES_CATALOGUE=catalogue/catalogue.toml target/debug/pengyos-themes …`.
