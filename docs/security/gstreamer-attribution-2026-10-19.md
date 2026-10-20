# gstreamer attribution pass — 2026-10-19

Operator authorised this before any gstreamer version decision, on the grounds that
the 24 P0/P1 matches reported against `gstreamer1` are known to be inflated by NVD's
project-wide CPE. They are. **None of the 24 belongs to gstreamer core.**

Method: for each CVE, take the component its fix touches. Where NVD names a commit,
read the patch's paths (the planner's `_attribution` / `edits.patch_components`).
Where NVD names no commit, read upstream's own advisory — GStreamer publishes one per
CVE and its "Affected Versions" row names the component. Where there is no advisory,
read NVD's description and locate the plugin named in it in the monorepo tree.

Sources: `plan.json` attribution, 96 advisories fetched from
`gstreamer/gstreamer@main:security-advisories/`, NVD's CVE API, and the monorepo
directory tree via the freedesktop GitLab API.

## The 24, by where the fix actually lives

| CVE | CVSS | evidence | component | we ship it? |
|---|---|---|---|---|
| CVE-2025-47183 | 6.6 | sa-2025-0005: `gst-plugins-good < 1.26.2` | **good** | yes, 1.24.13 |
| CVE-2025-47219 | 8.1 | sa-2025-0004: `gst-plugins-good < 1.26.2` | **good** | yes, 1.24.13 |
| CVE-2026-1940 | 7.5 | sa-2026-0001: `gst-plugins-good < 1.28.1` | **good** | yes, 1.24.13 |
| CVE-2026-73433 | 6.6 | NVD: "gst-plugins-good (avidemux)"; fix commit touches `gst/avi/` | **good** | yes — patch carried |
| CVE-2026-73434 | 6.1 | NVD: "gst-plugins-good (avidemux)"; fix commit touches `gst/avi/` | **good** | yes — patch carried |
| CVE-2026-18298 | 7.8 | NVD: PNG file parsing → `gst-plugins-good/ext/libpng` | **good** | yes, 1.24.13 |
| CVE-2026-18299 | 7.8 | NVD: `rtpsbcdepay` → `gst-plugins-good/gst/rtp` | **good** | yes, 1.24.13 |
| CVE-2025-47806 | 5.6 | sa-2025-0006: `gst-plugins-base < 1.26.2` | **base** | yes, 1.24.13 |
| CVE-2025-47807 | 5.5 | sa-2025-0002: `gst-plugins-base < 1.26.2` | **base** | yes, 1.24.13 |
| CVE-2025-47808 | 5.6 | sa-2025-0003: `gst-plugins-base < 1.26.2` | **base** | yes, 1.24.13 |
| CVE-2026-18297 | 7.8 | NVD: OGG file parsing → `gst-plugins-base/ext/ogg` | **base** | yes, 1.24.13 |
| CVE-2025-3887 | 8.8 | sa-2025-0001: `gst-plugins-bad 1.x < 1.26.1` | **bad** | yes, 1.24.13 |
| CVE-2026-2923 | 7.8 | sa-2026-0007: `gst-plugins-bad < 1.28.1`; planner attributes the fix commit to `gst-plugins-bad/` | **bad** | yes, 1.24.13 |
| CVE-2026-2920 | 7.8 | fix commit touches `gst-plugins-ugly/` (asfdemux) | **ugly** | **no — we ship no ugly package** |
| CVE-2026-2921 | 7.8 | named "fix" is `security-advisories/sa-2026-0004.md` | — | no code fix published |
| CVE-2026-2922 | 7.8 | named "fix" is `sa-2026-0005.md` | — | no code fix published |
| CVE-2026-3081 | 7.8 | named "fix" is `sa-2026-0010.md` | — | no code fix published |
| CVE-2026-3082 | 7.8 | named "fix" is `sa-2026-0003.md` | — | no code fix published |
| CVE-2026-3083 | 8.8 | named "fix" is `sa-2026-0008.md` | — | no code fix published |
| CVE-2026-3084 | 7.8 | named "fix" is `sa-2026-0011.md` | — | no code fix published |
| CVE-2026-3085 | 8.8 | named "fix" is `sa-2026-0008.md` (same commit as -3083) | — | no code fix published |
| CVE-2026-3086 | 7.8 | named "fix" is `sa-2026-0009.md` | — | no code fix published |
| CVE-2026-18295 | 7.8 | NVD: "MRF File Parsing" — no advisory, no commit, **no mrf plugin in the monorepo plugin dirs** | **unknown** | unattributed |
| CVE-2026-18296 | 7.8 | NVD: "MRF File Parsing" — as above | **unknown** | unattributed |

## What the numbers are

Reported against `gstreamer1`: **24 P0/P1**. Attributed:

- **0** belong to gstreamer core.
- **7** belong to `gst1-plugins-good` (we ship it, 1.24.13).
- **4** belong to `gst1-plugins-base` (we ship it, 1.24.13).
- **2** belong to `gst1-plugins-bad` (we ship it, 1.24.13).
- **1** belongs to `gst-plugins-ugly` — we ship no ugly package, so it is not ours to fix.
- **8** have no upstream code fix at all: NVD's "fix commit" is the advisory markdown file.
- **2** (the MRF pair) cannot be attributed: no advisory, no commit, and no MRF plugin
  exists in the monorepo's plugin directories.

So the headline "24 P0/P1 in gstreamer1" is a CPE artifact end to end. The real,
shipped backlog is **13 CVEs across good/base/bad**, of which 2 are already carried as
patches (the avidemux pair).

## What this says about the version decision

The advisories' own bounds say a version bump is the fix, and they name the component,
not the core:

- `gst-plugins-base` CVEs are fixed at **1.26.2**
- `gst-plugins-good` CVEs are fixed at **1.26.2** and **1.28.1**
- `gst-plugins-bad` CVEs are fixed at **1.26.1** and **1.28.1**
- the ZDI-pair CVEs (18295-18299) name **1.28.5** in NVD's range

A bump that closes the whole attributed set is therefore **1.28.5+**, and it must move
the monorepo in lockstep: base, good and bad are one release train, and our three
packages are all pinned at the same 1.24.13 today. Moving `gstreamer1` alone closes
nothing, because nothing is in `gstreamer1`.

Two things a bump does **not** buy:

- the 8 advisory-only CVEs have no code fix to buy at any version;
- the 1 ugly CVE is not ours.

And one thing a bump costs that the ffmpeg fork already proved: `gst1-libav` cannot be
built against ffmpeg 9 at any gst-libav release (1.24.13, 1.26.0 and 1.28.8 all name
`AV_CODEC_ID_V308/V408/V410`, which ffmpeg 9 removed). A gstreamer 1.28 move and the
ffmpeg 9 fork interact through that plugin, and today we carry a guard patch for it.

## Unresolved

- **CVE-2026-18295 / CVE-2026-18296 (MRF, CVSS 7.8, file-reachable).** NVD's range is
  `gstreamer:* < 1.28.5` with no commit and no advisory, and no MRF plugin appears in
  `gst-plugins-{base,good,bad}`. Either the plugin is named something else upstream, or
  these are being reported against a component we do not ship. Left unattributed rather
  than guessed: the next reader should not treat "1.28.5 closes it" as established.
