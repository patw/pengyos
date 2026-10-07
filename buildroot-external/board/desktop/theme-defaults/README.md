# PengyOS desktop theme defaults

Laid over COSMIC's system theme defaults (`/usr/share/cosmic`, from
cosmic-settings) by `post-build.sh`: **light mode**, the **green accent**, and
**slightly round** corners (radius 8) in both light and dark.

These are the files COSMIC Settings itself wrote (on the N100, 2026-09-28) when
those options were picked in Settings → Appearance — the builder choices
(`*.Builder`) *and* the computed themes (`CosmicTheme.Light` / `.Dark`), which
are what apps actually read. To change the defaults: pick them in Settings on a
live stick, then copy `~/.config/cosmic/com.system76.CosmicTheme.*/v2` (and
`CosmicTheme.Mode/v1`) back here.
