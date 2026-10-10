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

Settings at the 2026-09-28 capture did not write two keys that the pinned
libcosmic's `Theme` reads, and cosmic-settings' own `default_schema/v2` lacks them
too. Without them, xdg-desktop-portal-cosmic logged "error loading system light
theme" at every session start. They are added here by hand:
`frosted_maximized_apps` (`false`, also in the `*.Builder` dirs), and
`list_button`, computed with the pinned libcosmic's `ThemeBuilder::build()` from
the same builder input (default schema plus these overrides). On a recapture,
check that `ls v2` has every field of `Theme` in
`libcosmic/cosmic-theme/src/model/theme.rs`.
