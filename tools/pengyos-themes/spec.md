# pengyos-themes — spec

Status: **agreed**, 2026-11-22 (operator decisions recorded under *Decisions*). The CLI is
built (0.1.0); the window and the image wiring are next.

## Why

The Desktop ships one icon theme (Cosmic, over a partial Pop) and COSMIC's default
colours. Users want more of both, and the base image must stay clean: every theme
shipped in the rootfs is tens of MB in an 8 GiB A/B slot, and a theme that isn't
complete draws blank icons (2026-11-22: 113 MIME types blank in Files). Flatpak cannot
deliver host icon themes, and the root is read-only.

So themes are an **optional, per-user install**: nothing in the image but this tool and
a small catalogue. What a user picks lands in their home directory on the `data`
partition, survives A/B updates, and shows up in COSMIC Settings as if it were native.

**Icons and colours in one place.** COSMIC treats them as two unrelated settings, on two
different screens. This tool puts them together, and adds the thing neither screen has:
a **Look**, an icon theme paired with a colour theme, applied in one step and undone in
one step.

## What it is

One Rust crate, `tools/pengyos-themes`, two front ends over one library:

- `pengyos-themes` — a CLI (built first; drives the tests and the gate);
- *Themes* — a standalone libcosmic app (second), host-built like the other PengyOS
  apps (`scripts/build-cosmic.sh`), Desktop only. Its `.desktop` file declares
  `Categories=System;Settings;`, so the App Library's *System* folder lists it (that
  folder is a `Categories: ["System"]` filter; no change to the folder list).

Why standalone and not a page in COSMIC Settings: Settings has no extension point, so
a page there would be a large carried patch on one of COSMIC's busiest codebases,
re-fitted at every COSMIC bump. The app writes only the public config COSMIC Settings
and Tweaks already use (`CosmicTk.icon_theme`, the `CosmicTheme.*` builders), so all
three always agree on what is active.

It must be Rust: applying a colour theme means building a `cosmic::theme::Theme` from a
`ThemeBuilder` (the derived palette, the accent shades, the component colours), and
that code lives in libcosmic.

### Icons (pages: *Icons*)

- A **catalogue** shipped in the image, `/usr/share/pengyos/themes/catalogue.toml`
  (icon packs, our colour themes, curated Looks; format in `src/catalogue.rs`): each
  icon pack has a name, version, download URL, `sha256`, license, download and
  installed size, and the theme directories it provides with their path in the
  archive (e.g. `Papirus`, `Papirus-Dark`, `Papirus-Light`). Curated and **complete
  themes only**. First entry: Papirus. The catalogue moves with image updates, like
  any other file in the rootfs. (Previews are the app's job, drawn from the theme.)
- `install` downloads the archive, **refuses it unless the sha256 matches**, and
  unpacks only the declared theme directories into `~/.local/share/icons/<Theme>`. Entries
  that are absolute, contain `..`, or would be written through a symlink are refused
  (the cosmic-files zip lesson, patch 0002). Unpacking goes to a temporary directory
  first and is moved into place, so a failed install leaves nothing behind. Links
  must stay inside the pack's themes (Papirus-Dark links into Papirus); hard links
  and devices refuse; a size and entry budget stops a bomb. Each installed theme
  carries a marker, and a theme without it (not ours) is never replaced or removed.
- `use <theme>` sets `icon_theme` in `com.system76.CosmicTk` (exactly what COSMIC
  Settings writes). `remove` deletes the theme directories, and if the removed theme
  was active, it switches back to `Cosmic` first.
- Installed themes appear in COSMIC Settings → Appearance → *Icons and toolkit
  theming* without any further step (that page scans `~/.local/share/icons`).

### Colours (page: *Colours*)

- Browses **cosmic-themes.org** through its public JSON API (`/api/themes/`:
  `search`, `order=popular|new|name`, `color=#rrggbb`, `limit`, `offset`, RON inline),
  the same source COSMIC Tweaks uses. Nothing is fetched until the page is opened; no
  account, no upload. The API has no single-theme endpoint, so the last answers are
  kept (`site-cache.json`) and `apply <site id>` takes the theme from there.
- A theme is a RON `ThemeBuilder`. It is parsed with libcosmic's types and **rejected
  unless it parses** (data only: nothing in it is executed), with a 64 KB cap.
  Themes in an older format this COSMIC cannot read (Settings' Import refuses them
  too) are left out of search results.
  Previews are drawn from the parsed palette, not from screenshots.
- `apply` does what COSMIC Settings' *Import* does: switch dark/light to match the
  palette, write the builder to `com.system76.CosmicTheme.{Dark|Light}.Builder` and the
  built theme to `com.system76.CosmicTheme.{Dark|Light}`.
- Applied themes are kept in a local library, `~/.local/share/pengyos-themes/colours/`,
  so they can be re-applied offline and still exist if the site goes away.

### Looks (page: *Looks*, the front page)

- A Look is `{ icons: <theme or "Cosmic">, colours: <library entry or "default">,
  mode: dark|light }`. A few curated Looks ship in the catalogue (each pairing a
  catalogue icon theme with a colour theme stored *in the catalogue*, not fetched).
  Users can save the current pairing as their own.
- **Undo is built in.** Before anything changes the desktop (a Look, an icon theme or a
  colour theme), the current `CosmicTk.icon_theme`, both theme builders and the mode are
  snapshotted to `~/.local/share/pengyos-themes/undo/`. `pengyos-themes undo`, or the
  app's *Undo*, restores the last snapshot exactly (20 steps are kept). *Reset*
  restores COSMIC's icons and its shipped colours, dark and light (copied from
  `/usr/share/cosmic`, else libcosmic's own); dark or light stays as the user has it,
  as Settings' *Reset to default*. Reset is undoable.

## CLI

```
pengyos-themes icons list | install <name> | use <theme> | remove <name>
pengyos-themes colours search [TEXT] [--order popular|new|name] [--color HEX]
pengyos-themes colours apply <id|file.ron> | library | forget <id>
pengyos-themes looks list | apply <id> | save <name> | forget <id>
pengyos-themes undo | reset | status
```

Exit codes: `0` done; `1` failed (download, checksum, parse, write); `2` usage.
`status` prints the active icon theme, mode, and colour theme source (`--json` too;
so do the list, library and search commands). Commands that change anything refuse
to run as root.

## Boundaries

- **Per-user only.** Runs as the user, writes only under `~/.local/share` and
  `~/.config/cosmic`. No root, no polkit, no system-wide install, nothing in the rootfs
  changes. The security boundary is not crossed, so no sign-off is needed for the tool
  itself.
- **Downloads.** Icon archives come only from catalogue URLs and only with a matching
  sha256. Colour themes come only from cosmic-themes.org over HTTPS (or a local file),
  and are data. The tool sends nothing about the user or the machine.
- **Licenses.** Nothing third-party ships in the image except the catalogue (metadata).
  Each catalogue entry names its license, shown before install (Papirus: GPL-3.0).
  cosmic-themes.org themes are user-submitted and are fetched by the user, not
  redistributed by us.

## Non-goals (for now)

GTK/Qt widget themes, cursor themes, wallpapers, uploading to cosmic-themes.org, and a
general add-on store. COSMIC Tweaks keeps its dock, panel and layout features; this is
not a Tweaks replacement.

## Acceptance criteria

1. `icons install papirus` on a fresh Desktop: the theme appears in COSMIC Settings'
   picker, `icons use Papirus-Dark` switches every COSMIC app's icons with no restart,
   and `check-mime-icons.py` passes when run against the user's icon directory as well.
2. A catalogue archive with a wrong checksum, a `..` entry, an absolute path or an
   entry through a symlink is refused, and leaves nothing under `~/.local/share/icons`.
3. `colours apply <id>` gives the same `CosmicTheme.*` config as importing the same
   RON in COSMIC Settings (compared key by key in a test).
4. A malformed or oversized RON is refused without touching any config.
5. `undo` after any `apply`/`use` restores the previous config byte for byte; `reset`
   restores COSMIC's defaults.
6. Offline: `looks apply` and `colours apply` from the library work with no network;
   `colours search` fails with a clear message, not a hang (timeouts on every request).
7. An A/B update leaves installed themes, the library, Looks and the active choice
   intact (all of it lives on `data`).
8. The gate runs the CLI's tests (`verify.toml`), with the network stubbed.

## Decisions (operator, 2026-11-22)

- **First catalogue: Papirus only** (with -Dark and -Light). Later themes are added
  one at a time, each passing `check-mime-icons.py` before it is listed.
- **Curated Looks ship at launch** (two or three), alongside "save your own". Their
  colour themes are stored in the catalogue, so applying one needs no cosmic-themes.org
  request beyond the icon download.
- **A standalone *Themes* app in the System folder**, not a COSMIC Settings page.
