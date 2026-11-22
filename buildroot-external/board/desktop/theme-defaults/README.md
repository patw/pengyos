# PengyOS desktop theme defaults

Laid over COSMIC's system theme defaults (`/usr/share/cosmic`, from
cosmic-settings) by `post-build.sh`: what a new user sees. Since 2026-11-22 that
is the **Glacier** pair: dark mode with Glacier, light mode with Glacier Light,
Papirus Dark / Papirus Light icons (the `papirus-icon-theme` package), and dark
by default. The installer's Dark/Light question writes the user's own choice.

**Generated, do not edit by hand.** These files are written by
`tools/pengyos-themes` from its catalogue's `[defaults]`:

    cd tools/pengyos-themes
    PENGYOS_THEMES_CATALOGUE=catalogue/catalogue.toml \
      cargo run -q -- system-defaults ../../buildroot-external/board/desktop/theme-defaults

It uses the same libcosmic `write_entry` calls as COSMIC Settings, pinned to
the Desktop's libcosmic rev, so every key `Theme` and `ThemeBuilder` read is
present (the 2026-09 hand capture lacked `list_button` and
`frosted_maximized_apps`, and its dark builder palette did not parse). A test in
`tools/pengyos-themes` fails when these files and the catalogue disagree; after a
libcosmic bump or a colour change, rerun the command above and commit the result.

Only the theme builders and themes, `CosmicTheme.Mode` `is_dark` and
`CosmicTk` `icon_theme` are here; every other COSMIC default is upstream's.
