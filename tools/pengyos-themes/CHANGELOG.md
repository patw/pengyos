# Changelog — pengyos-themes

## [Unreleased]

## [0.2.0] - 2026-11-22

### Added
- `follow`: in each session (Desktop autostart), swaps a pack's dark and light
  variants (`follow = { dark, light }` in the catalogue: Papirus-Dark and
  Papirus-Light) whenever `CosmicTheme.Mode` changes, and once at login. Only a
  declared pair, only on a mode change; not an undo step.
- `system-defaults DIR`: writes COSMIC's default theme files for the catalogue's
  new `[defaults]` (the Look for each mode, and the mode), with the same
  `write_entry` calls as Settings. The Desktop's `board/desktop/theme-defaults`
  is this output, and a test fails when the two disagree.
- A pack that comes with the system (`/usr/share/icons`, as Papirus does on the
  Desktop) shows as "comes with PengyOS": `install` has nothing to do and
  `remove` refuses.
- In the Desktop image: `/usr/bin/pengyos-themes`, the catalogue in
  `/usr/share/pengyos/themes`, and the `follow` autostart entry.

## [0.1.1] - 2026-11-22

### Added
- Glacier Light: a light Look as complete as Glacier (frosted off-white surfaces,
  brighter cards, a slate-blue neutral tint, a deep glacier-blue accent readable on
  white, Papirus Light), after the operator found the light side the weaker one.

### Fixed
- A catalogue that exists but does not load (say, a bad edit) was silently treated
  as no catalogue, so `looks apply` said "no Look" instead of why. It is now an error.
- Catalogue ids are unique per kind, so a Look can share its colour theme's id
  (`glacier-light`).

## [0.1.0] - 2026-11-22

### Added
- The `pengyos-themes` CLI (spec.md): `icons list|install|use|remove`,
  `colours search|apply|library|forget`, `looks list|apply|save|forget`, `undo`,
  `reset`, `status` (`--json` on the read-only ones).
- Icon packs install from the catalogue only, refused unless the download's
  sha256 matches; only the declared theme directories are unpacked, into staging,
  and an absolute or `..` path, a link leaving the themes, a write through a
  symlink, a hard link or device, or an archive past its size budget refuses the
  whole pack and leaves nothing behind. Themes this tool did not install are never
  replaced or removed.
- Colour themes are written with libcosmic's own `write_entry`, exactly as COSMIC
  Settings' *Import* writes them (a test compares the two key by key).
- Every change is snapshotted first; `undo` restores it byte for byte (20 steps).
- The catalogue: Papirus 20260801 (793 of 793 MIME types have an icon), three
  colour themes of our own (Glacier, Meadow, Ember) and a Look for each.
