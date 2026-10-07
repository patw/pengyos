# PengyOS wallpapers — candidate set

Twenty candidate desktop backgrounds drawn 2026-10-06. All twenty were accepted
and are installed in the Desktop overlay alongside the original Cosmic Pengy, so
the shipped set is 21. This directory keeps the provenance and the means to
redraw any of them.

The shipped wallpapers live in
`buildroot-external/board/desktop/rootfs-overlay/usr/share/backgrounds/pengyos/`.

## Layout

| Path | What |
|---|---|
| `prompts.toml` | The provenance record: every scene prompt, its palette anchors, its caption. This is the thing that makes the art reproducible. |
| `generate.sh` | Re-runs the set. Prepends one shared mascot+style block to each scene prompt so the whole family keeps one Pengy. `./generate.sh` = all, `./generate.sh autumn-maple ski-slope` = just those. Skips names that already exist. |
| `candidates/` | Raw generator output, 2752×1536. Not exact 16:9 — see the crop note. |
| `cropped/` | Centred to exact 16:9, 2720×1530, PNG. Intermediate. |
| `jpeg/` | The shipped encoding: q92, 4:4:4, 2720×1530. Copied into the overlay. |
| `contact/` | Contact sheet, zooms, and the JPEG subsampling comparison. |
| `generate.log` | Generator stdout, including the PengyShare URL captured per image into `candidates/<name>.url`. |

`candidates/`, `cropped/`, `contact/` and `.prompts/` are gitignored: ~90 MB of
PNGs does not belong in a repo that syncs over Syncthing. Only the chosen
wallpapers get promoted into the overlay, which is where the one tracked PNG
already lives.

## Palette

Every candidate is anchored on COSMIC's own accent swatches — the Appearance →
Accent colour row — read out of libcosmic
`cosmic-theme/src/model/light.ron` rather than eyeballed:

```
accent_blue #00525A   accent_indigo #2E496D   accent_purple #68217C
accent_pink #86043A   accent_red    #78292E   accent_orange #624000
accent_yellow #534800 accent_green  #185529   accent_warm_grey #554742
ext_blue #6ACAD8  ext_purple #D58CFF  ext_pink #FF9CDD  ext_indigo #95C4FC
ext_orange #FBB86C  ext_yellow #F7E062  ext_warm_grey #9B8E8A
```

`accent_green #185529` is the PengyOS default accent (light mode); dark mode
derives a teal around `#63D0DF`.

## Two things worth knowing before re-running this

**Gemini's `--ar 16:9 --res 2K` does not return 16:9.** It returns 2752×1536,
which is 1.7917 — about 0.8 % wider than 16:9. The crop to exact 16:9 is done
with PIL in a centred, deterministic step, never by asking the model to resize:
a generative model asked to crop redraws the picture instead of cutting it.

**"No text" needs to be argued, not asserted.** The first `city-rainfall`
rendered legible shopfronts (ARCADE, BOOKSHOP, RAMEN BAR). The fix was not
"no text" but a positive instruction about what the signs *are* — abstract
neon bars, dots and geometric glyphs, no alphabet. Terminal screens are the
exception worth allowing: `terminal-den` renders plausible log output, which
reads as texture rather than as a caption.

## Install path

Copy `cropped/<name>-16x9.png` into
`buildroot-external/board/desktop/rootfs-overlay/usr/share/backgrounds/pengyos/`.
That is the whole install. **There is no config edit to make.**

`.../usr/share/cosmic/com.system76.CosmicBackground/v1/all` is *not* a list of
wallpapers. It is one `cosmic_bg_config::Entry` struct — `output`, `source`,
`filter_by_theme`, `rotation_frequency` — and it names only the currently
**active** wallpaper. Adding a wallpaper does not touch it.

What actually populates Settings → Wallpaper is a directory walk. In
cosmic-settings, `Config::default_folder()` resolves `$XDG_DATA_DIRS/backgrounds`
(falling back to `/usr/share/backgrounds`), and
`cosmic_settings_wallpaper::load_each_from_path` runs
`WalkDir::new(path).max_depth(3).follow_links(true)` over it. Files found are
the picker. So:

- drop the file in, it appears;
- the walk is depth-limited to 3, so `backgrounds/pengyos/<file>.png` is fine
  but deeper nesting is not;
- thumbnails are generated on first view into `~/.cache/cosmic-settings/wallpapers`,
  not shipped.

Verified against the pinned libcosmic/cosmic-settings checkout in `~/cosmic-src`,
not from documentation.

## Format: JPEG q92, 4:4:4

The shipped wallpapers are JPEG, not PNG: 20 files at 18 MB instead of 93 MB.

COSMIC handles JPEG on both sides — `cosmic-bg` enables the image crate's
`jpeg` feature and decodes through `ImageReader::open(..).with_guessed_format()`,
and the cosmic-settings picker gates on an extension list that includes
`jpg`/`jpeg`/`png`/`avif`/`webp`/`heif`.

**Subsampling is the part that matters here.** These are line-art illustrations:
thin bright outlines over dark fills. Standard 4:2:0 chroma subsampling visibly
softens those edges — visible side by side on `terminal-den` and `autumn-maple`,
and measurable (4:2:0 40.8–42.5 dB PSNR vs 4:4:4 42.8–44.0 dB). 4:4:4 costs
3.8 MB more across the set and is worth it for this art. The comparison crops
are in `contact/jpgcmp-*.png`.

`cosmic-pengy-16x9.png` was **redrawn** in this same family style and reissued
as `cosmic-pengy-16x9.jpg`. The original was 1344×768 — 1.7500, i.e. 7:4, not
16:9 despite the name — and it is the one wallpaper in the set that predates
this style. It survives in git history before commit `dea3faa5` if it is ever
wanted back.

## The one dependency that is easy to break

`.../usr/share/cosmic/com.system76.CosmicBackground/v1/all` names the **active**
wallpaper by absolute path. Renaming or reformatting that file without editing
this record does not fail the build — it yields a blank desktop at first boot.
`pengyos-gate` now checks it (`backgrounds:` and `backgrounds-aspect:`), which
catches both a stale path and a `*-16x9` file that is not actually 16:9. Both
were verified with negative controls, because a check that cannot fail is not a
check.

## The compat symlink, and why it is there

Deleting `cosmic-pengy-16x9.png` does not only change the default — it breaks
**existing** installs. cosmic-config resolves
`~/.config/cosmic/com.system76.CosmicBackground/v1/all` (user) over
`/usr/share/cosmic/...` (system default), and any Desktop machine where someone
has opened Settings → Wallpaper has a user record pinning the old absolute path.
Checked on the fleet: behoweather does, behobasement does not. After an upgrade
that path no longer exists, `source.canonicalize()` fails, `load_images()`
queues nothing, and cosmic-bg logs "wallpaper could not be drawn" — a blank
desktop, not a build error.

So the overlay keeps `cosmic-pengy-16x9.png` as a **relative symlink** to
`cosmic-pengy-16x9.jpg`. Buildroot applies overlays with `rsync -a`
(`system/system.mk`, `SYSTEM_RSYNC`), which copies symlinks as symlinks, so it
reaches the target intact. Existing installs then resolve the old path and land
on the new art automatically.

Cost, accepted: the picker walks `/usr/share/backgrounds` with
`follow_links(true)`, so the link shows as a second thumbnail of the same image.
It also passes `backgrounds-aspect` — the header is read *through* the link, so
it reports 2720×1530, not the 7:4 the name used to lie about.
