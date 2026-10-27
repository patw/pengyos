# Browser engine viability — a custom Chromium, a COSMIC wrapper, and Manifest V2

**Status:** research, 2026-10-15. Answers the operator question: *"how viable is a
custom Chromium engine for PengyOS, with a native COSMIC wrapper around it, that can
run Manifest V2 add-ons like uBlock Origin?"*
**Verdict up front: a custom Chromium engine is not viable as a PengyOS component.
The MV2 half of the goal is already satisfied today (Firefox, Lane 3); the native-COSMIC
half is viable, and it is a WebKitGTK project, not a Chromium one.**

Sources are dated 2026-10-15. Web-sourced numbers are marked as such; repo facts are
cited to files.

---

## 1. The three asks are three different problems

The question bundles three separable things, and they do not share an answer:

| Ask | What it really is | Answer |
|---|---|---|
| "custom Chromium engine" | a maintained Chromium fork we build and ship | **No** — Band 5, and the fork economics are hostile (§3) |
| "native COSMIC wrapper" | a Rust/iced shell that owns tabs, chrome and theming | **Yes** — as a shell around an embeddable engine (§4) |
| "run MV2 add-ons like uBlock Origin" | an engine that still implements MV2 + blocking `webRequest` | **Firefox only** — Chromium has *no* MV2 code left (§2) |

The decisive fact is the last one: **MV2 is not an engine property you can choose in
Chromium any more — upstream deleted it.** Everything else is cost.

---

## 2. Manifest V2 in 2026: the Chromium door is closed, the Firefox door is open

Measured timeline (Chrome for Developers MV2 timeline; 9to5Google/Chrome 151 release
reporting; Chrome Release Blog):

| Date | Event |
|---|---|
| 2025-07-24 | Chrome 138: MV2 disabled for **all** users, all channels |
| 2025-10 | Enterprise policy `ExtensionManifestV2Availability` removed in Chrome 139 |
| 2026-06-30 | Chrome 150: last MV2 flag (`ExtensionManifestV2Disabled`) deleted |
| 2026-07-28 | **Chrome 151: the remaining MV2 code is stripped out of Chromium** |
| 2026-08-31 | All remaining MV2 extensions deleted from the Chrome Web Store, uBlock Origin included |

Current stable is Chrome/Chromium **154** (2026-09-22), with the cadence halved to a
release **every two weeks** (153 → 154 → 155 on Oct 6; Dev is already at 157). So the
newest Chromium containing MV2 code is **150**, four versions and ~3 months behind
stable, and every version after it is further away.

Consequences for a custom engine:

- **There is no flag, policy, switch, or patch point in current Chromium that re-enables
  MV2.** It is not "disabled", it is *removed*.
- Keeping MV2 means one of two permanent commitments:
  1. **Freeze at Chromium 150** and backport security fixes forever. Chrome 154 alone
     patched **108 flaws, 11 critical** (ANGLE/GPU/WebGL memory safety). A frozen
     Chromium is a standing CVE debt with no upstream mapping — exactly the failure mode
     the divergence ledger exists to prevent.
  2. **Restore MV2 in modern Chromium** — re-land the removed extension bindings,
     background pages, and blocking `webRequest`, then maintain that patch set against
     a tree that refactors weekly. That is a fork of a subsystem, not a patch.
- **CEF is not an escape hatch.** CEF stable is 143.0.13 (Chromium 143, Dec 2025) and
  its `CefExtension` is a narrow internal API for unpacked test extensions — not the
  WebExtensions/MV2 stack. An embedded-Chromium browser cannot run uBlock Origin.
- **Firefox is the only mainstream engine with full MV2**, including blocking
  `webRequest`; Mozilla has restated that commitment (2024-03 add-ons blog, reaffirmed
  2025-02 and 2026-08). Full uBlock Origin runs on Firefox today. Brave keeps MV2 by
  substituting its own MV3 equivalents. Blanc ships uBO 1.75.0 on an Electron runtime
  *that can still load MV2* — i.e. a pinned engine, with the security trade stated
  openly on their own page.
- **PengyOS Desktop already ships the answer.** `pengyos-browser-setup.sh` installs the
  Firefox Flatpak on first boot, and `mimeapps.list` routes http/https to it. Full
  uBlock Origin (MV2) works on PengyOS Desktop **today**, through Lane 3.

---

## 3. Why "custom Chromium" fails our own admissibility test

`docs/stack-down-replace.md` (2026-10-06) puts **a browser in Band 5 — "not on the
list"**, excluded *by verifiability, not capability*; `docs/principles.md` names a
from-scratch browser a non-goal ("the web platform grows faster than any implementation
can follow"); SPEC §5 keeps it there. The five-part test (§ "The admissibility test")
is the gate. A custom Chromium against each part:

| Test | Custom Chromium | Why |
|---|---|---|
| **1 — Lane 1 recipe** | ✗ | Buildroot 2026.08 has **no Chromium package at all** (measured: `package/` has `webkitgtk`, `wpewebkit`, `libwpe`, `wpebackend-fdo`, `netsurf`). A 2018 `chromium: new package` patch series exists in patchwork and was never merged. Chromium needs host clang, Rust, Node, Python, gn/ninja/siso, and bundles ~200 third-party trees it builds itself — it is a build system, not a package |
| **2 — Provable** | ✗ | `pengyos-qemu-verify` asserts *boot + login + smoke matrix*; the Desktop check is a screenshot. Nothing in our harness can assert "the web platform works". That absence **is** the Band-5 definition |
| **3 — Observed** | ✗ | pkg-stats sees only recipes it reads. A Chromium fork needs its own observe source *and* a CVE mapping against a 2-week upstream cadence (~100 CVEs per release) |
| **4 — Ledgered** | ✓ (formally) | It would be fork #1 of the 10-fork budget, Tier-2 human sign-off, with an expiry condition — and the honest expiry is "upstream restores MV2", which is not happening |
| **5 — Repro survives** | ✗ | Chromium is not reproducible upstream (no source-only tarball, `depot_tools`/`fetch` checkout, bundled prebuilts). Core/Server byte-identity is our strongest claim; the Desktop already lost it to host-built COSMIC (SPEC §6.3). A host-built Chromium would compound the debt |

Scale, for the record (community-measured, 2026): **35–40 GB source checkout, 100+ GB
build tree, 16+ GB RAM, 2–8 h per full build.** behoserver (32 cores, 60 GB RAM, 443 GB
free on `/data`) *can* build Chromium — capability was never the constraint. Verifiability is.

The maintenance shape is the real cost: ungoogled-chromium shows what a Chromium fork
looks like when it is done seriously — a CI farm re-basing onto every upstream release
within days, with distro packagers doing the work. That is a standing team, not a
nightly routine.

---

## 4. The COSMIC wrapper: viable, and it does not need Chromium

PengyOS Desktop's app stack is Rust + `iced` + `libcosmic` (epoch 1.9, pinned in
`scripts/cosmic-components.tsv`). For a native browser shell there are four wrapper
architectures:

| Option | Embedding | Extensions | Buildability here | Verdict |
|---|---|---|---|---|
| **iced/libcosmic shell + Chromium as a separate process** | none — Chromium is its own Wayland client (ozone/wayland is production-grade, text-input-v3 on by default); the shell can launch/coordinate windows, not render content | current Chromium = MV3 only | needs a Chromium source we don't have | shell yes, engine no |
| **CEF off-screen rendering into an iced surface** | possible (push BGRA frames to a GPU texture) | **no extensions** | host-built, Lane 2 at best | rejected: input/IME/a11y/latency work, no uBO |
| **wry + WebKitGTK inside a COSMIC shell** | **yes** — `wry` supports embedding via a GTK container for X11/Wayland (`new_gtk`); `iced_webview` already embeds wry/WebKit views in iced | WebKit's *native* WebProcessExtension API (C library injection) — **not** WebExtensions/MV2 | **Lane 1 today**: Buildroot ships `webkitgtk` 2.52.4 (GTK3 path; Desktop already has `BR2_PACKAGE_LIBGTK3=y` for portals) | **the viable native-browser project** |
| **COSMIC shell around the Firefox Flatpak** | window management, not embedding | full MV2 (Firefox) | Lane 3, already installed | cheap, honest, keeps MV2 |

So the honest decomposition of the operator's goal is:

- **Native COSMIC browsing** → a Band-1 additive COSMIC app: iced shell + `wry` +
  WebKitGTK. Lane 1 engine, no new fork, no new toolchain, boots in the existing smoke
  matrix, and it is the same class of work as `cosmic-files` or the PengyOS libcosmic apps.
- **MV2 / full uBlock Origin** → Firefox (already shipped), **not** a Chromium property.
- **Ad blocking that is engine-independent** → network-level (DNS filtering, hosts,
  proxy). It works in every lane, survives engine churn, and fits the fleet model.
  This is the durable answer to "block ads on PengyOS", not an extension API.

---

## 5. The other engines (for completeness)

| Engine | 2026 state | Fit here |
|---|---|---|
| **Chromium/Blink** | v154 stable, 2-week cadence, no MV2 | Band 5; Flatpak (Lane 3) is the only sane way to carry it |
| **WebKitGTK** | 2.52.4, a Buildroot recipe in 2026.08 | Lane 1, embeddable via wry, GTK3 already in the Desktop |
| **WPE WebKit** | 2.50.5, recipe + `wpebackend-fdo` | Lane 1, headless/compositor path, no GTK |
| **Servo** | Rust, embeddable WebView API, active | research-grade engine, no extension layer |
| **Ladybird** | self-declared **pre-alpha / alpha-2026 target** | not usable |
| **NetSurf** | in Buildroot | far from the modern web |

---

## 6. Recommendation

**Do not build a custom Chromium engine.** Concretely:

1. **Keep Firefox as the ratified default browser** (Lane 3). It is the only mainstream
   engine with full MV2, so it is the only one that answers the uBlock Origin half of
   the request. No change.
2. **Add Chromium as a Flatpak** (`org.chromium.Chromium`, pinned commit in
   `flathub-commits.toml`) if Chromium-specific behaviour is wanted. Lane 3, sandboxed,
   upstream-maintained, zero fork budget. MV3 only — that is upstream's decision, not ours.
3. **Fund the native-browser project as Band 1 on WebKitGTK**: a COSMIC shell (iced +
   libcosmic) with a `wry`-embedded WebKit view, packaged via `pengyos-cosmic-pkg`.
   This is the "native COSMIC wrapper" the question is really asking for, and it is the
   only variant that passes Lane 1 + provable + repro.
4. **Treat MV2 as a Firefox + network-layer story**, not an engine story: DNS/hosts
   blocking fleet-wide; uBO full in Firefox; uBO Lite where MV3 is the only option.
5. **If the operator still wants the Chromium fork**, scope it as a *research track*
   (Band 4 language), not a roadmap item: pin **Chromium 150**, restore nothing, ship
   nothing, and write the security debt down in `SECURITY.md` with the 12-month
   escalation rule from the fork policy. The band-reopen trigger that would change this
   is the one in `stack-down-replace.md`: *upstream abandoning a component we depend on*
   — Google deleting MV2 is precisely that, and it argues for Firefox, not for a fork.

### What would change this answer

- A verification capability we do not have: a web-platform conformance harness
  (webruns/WebKitTestRunner-class) wired into the gate, so "the web works" is checkable.
- An embeddable engine with a real extension layer: Servo's WebView + an extensions
  story, or WPE with a WebExtensions implementation (Epiphany has been building an
  MV2-shaped subset since 2022 — a contribution lane, not a dependency).
- Upstream Chromium re-opening MV2 (no sign of it), or a community fork with a credible
  CVE pipeline (Blanc's pinned-Electron approach is the shape; nobody has published the
  maintenance math).

---

## 7. Side finding while grounding (not fixed here)

The curation data and the shipped overlay disagree on the browser's Flatpak id:
`tools/pengyos-desktop-curation/curated.toml` + `flathub-commits.toml` +
`mime-integration.toml` use **`org.mozilla.FirefoxRepo`**, while
`board/desktop/rootfs-overlay/usr/libexec/pengyos/pengyos-browser-setup.sh` installs
**`org.mozilla.firefox`** and `mimeapps.list` binds
`org.mozilla.firefox.desktop`. Flathub's app id is the lowercase one, so the emitted
mirror manifest and the mime plan cannot resolve against what the image installs.
Tests assert the `FirefoxRepo` spelling, so this is a data+test decision for the
curation owner, not a drive-by edit — recorded here and in BotTalk, left unmodified.
