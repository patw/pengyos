# SteamVR games, wirelessly, to a Meta Quest 3S

**Question (2026-10-15):** *how viable is it to play SteamVR games from a PengyOS
machine on the household Quest 3S, wirelessly, with the OS setup we have now?*

**Verdict up front: the software half is viable and mostly already present. The OS
half is not viable today, for two structural reasons — no VR-capable machine in the
fleet runs PengyOS, and the Desktop's Steam lane (Flatpak) is the one lane Valve
explicitly does not support for SteamVR. The network is a third problem that exists
independently of the OS and is currently the hardest of the three.**

Sources are dated 2026-10-15. Repo facts are cited to files; web-sourced numbers are
marked. Fleet facts were measured on the live machines the same day. Prior
investigations are cited by BotTalk post id.

---

## 1. The question has three separable parts

They do not share an answer, and conflating them is how this topic stalls.

| Part | What it really asks | Answer |
|---|---|---|
| "does the streaming software work on Linux" | is there a maintained PCVR streamer for Quest on Linux | **Yes** — WiVRn, ALVR, and Valve's own Steam Link VR |
| "does *our OS* support it" | can a PengyOS image be the host | **Not today** — the app lane blocks it (§5, §6) |
| "is our Wi-Fi good enough" | can the house network carry 150–300 Mbit/s of latency-sensitive UDP | **No, as built today** — and this is true of Ubuntu too (§4) |

The third is the one that already bit us: the 2026-09-19 Beat Saber investigation
(BotTalk `c7cb9f01bf05651ca3000000`) found the TP-Link Deco mesh — mid-play stream
resets and node roaming — as the root cause, not the OS, not the GPU, not the
encoder. Nothing in this document changes that finding.

---

## 2. Where the fleet actually stands (measured 2026-10-15)

| Machine | OS today | GPU | VR-capable? |
|---|---|---|:--:|
| behotv (10.0.23.22) | Ubuntu 26.04.1, 7.0.0-38 | RTX 3070 (Ampere, 8 GB) | ✅ — the current VR host |
| behopc (10.0.23.23) | Ubuntu 26.04 (off at audit) | RTX 3060 Ti (8 GB) | ✅ |
| beholaptop (10.0.23.30) | Ubuntu (laptop, off) | RTX 3070 Ti Laptop | ✅ |
| behoserver (10.0.23.6) | Ubuntu 26.04.1, 7.0.0-38 | 2× RTX 5060 Ti (16 GB) | ✅ — best GPU in the house, no display role today |
| **behoweather (10.0.23.4)** | **PengyOS Desktop 26.10, 7.2.5** | Intel Alder Lake-N iGPU | ❌ — no discrete GPU |
| behobasement, behocraptop, behothinkpad | mixed (2 run PengyOS Desktop) | Intel iGPU / none | ❌ |

**The blunt fact: the only machines running PengyOS are the ones that cannot do VR,
and every machine that can do VR runs Ubuntu.** "The OS setup we have now" and "a VR
host" are currently disjoint sets. Any answer about PengyOS VR is therefore a
statement about *what PengyOS Desktop NVIDIA would do on behotv/behopc/behoserver*,
not about what runs today.

That is not a defect — the NVIDIA editions are young. Desktop NVIDIA's real-hardware
record is two machines: an RTX 3060 Ti (first metal boot, 26.10.7) and behotv's
RTX 3070 with driver 610.57.04 (2026-10-08, `CHANGELOG.md` — modules loaded,
`nvidia-drm` driving the displays, COSMIC processes on the GPU). Both are exactly the
class of card the Quest streaming stack wants.

---

## 3. What the Quest 3S puts on the table

The 3S is the cheap end of the current generation, and for this use it is *easier*
than a Quest 3, not harder.

| Property | Quest 3S | Consequence |
|---|---|---|
| Panel | 958×1200 per eye (vs Quest 3's 1832×1920) | ~3.5× less pixels to encode → far less GPU and bitrate |
| Refresh | 72 / 90 / 120 Hz (120 supported; 90 is the sane default) | 120 Hz doubles the encode budget; treat it as optional |
| SoC | Snapdragon XR2 Gen 2 (same as Quest 3) | HEVC and AV1 **decode** in hardware — both are WiVRn codecs |
| Wi-Fi | Wi-Fi 5 (802.11ac), 2×2, 2.4 + 5 GHz — **no Wi-Fi 6/6E** | 80 MHz channel, ~866 Mbit/s PHY ceiling; a clean 5 GHz channel is the whole game |
| Client apps | WiVRn, ALVR and Steam Link VR are all on the Meta Quest Store | no sideloading needed for any of the three paths |

Web-sourced, marked as such: the Quest 3S is Wi-Fi 5 2×2 per Meta's published
specifications; a handful of third-party pages claim Wi-Fi 6/6E for it, which does
not match Meta's own spec sheet and should be treated as wrong. Practical reading:
**the headset is the weakest link in the radio path, so buying a Wi-Fi 7 router buys
nothing for this use.** A clean 5 GHz channel near the play area is what matters.

Bandwidth, from the tools themselves: WiVRn's encoder targets ~200 Mbit/s by default
(the 2026-09 diagnosis read Steam Link's `targetBandwidth 200`), cable-oriented guides
quote 400–500 Mbit/s at high quality, and the 2.4 GHz AP experiment that
*fixed the resets* delivered a 130 Mbit/s PHY link and produced "visual mush"
(BotTalk `c7cb9f01bf05651ca3000000`). **Rule of thumb: a good 90 Hz picture needs
roughly 150–300 Mbit/s of clean, interference-free link, sustained, with low jitter.**

---

## 4. The network is the hard part, and it is not an OS problem

Measured, this house, twice:

- Deco mesh, Wi-Fi client → server: **81 Mbit/s down / 64 up**, against an
  **826 Mbit/s** wired baseline; three parallel streams gained nothing (shared
  medium saturated). BotTalk `23a38a800a0a23e959050000`.
- Deco mesh under a VR stream: 40× "Timed out waiting for another accepted video
  packet, force the stream reset", 46 stream resets, and 5 mid-play "network change"
  events = the headset roaming between mesh nodes. A direct AP removed **all**
  mid-play resets. BotTalk `c7cb9f01bf05651ca3000000`.
- The built-in Intel AC 9x6x on behotv **cannot beacon 5 GHz** as an AP (iwlwifi's
  self-managed regdb marks 5 GHz PASSIVE-SCAN); the 2.4 GHz AP that worked is too
  narrow for VR. BotTalk `7214d66ead64ab87a9000000`.

So the prerequisite, unchanged since September and independent of which OS the host
runs:

1. **A dedicated 5 GHz access point**, wired (gigabit) to the VR host, in AP mode,
   with the headset as its only client. A USB adapter (MT7921AU / MT7612U / RTL8812AU)
   or a small travel router in AP mode. Not the mesh. Not a wireless hotspot.
2. **Wired ethernet from the host to that AP.** Every streamer requires it; behotv and
   behoserver are already wired.
3. Encoder tuning: pin the bitrate instead of letting it auto-negotiate, drop 10-bit
   if the link is marginal, and stop supersampling at 150% (the 2026-09 session was
   rendering 3111×3328 per eye for a 958×1200 panel).

PengyOS helps here in one small, real way: the Desktop firewall already allows what
VR needs (§5.4). It does not help with the radio.

---

## 5. What PengyOS Desktop already gets right

Checked in the tree and on the live PengyOS box (behoweather, 26.10, kernel 7.2.5).

| Requirement | PengyOS status | Where |
|---|---|---|
| Kernel new enough for `ntsync` (needs ≥ 6.14) | ✅ 7.2.5, and `CONFIG_NTSYNC=m` is in the Desktop fragment | `board/desktop/linux.fragment:376` |
| `/dev/uinput` for Steam Input's virtual pads | ✅ built as a module **and auto-loaded** (`modules-load.d/steam-uinput.conf`); present on the live box | `package/steam-devices/steam-devices.mk` |
| Valve's controller **and VR** udev rules | ✅ `60-steam-input.rules` + `60-steam-vr.rules` installed; present on the live box | same recipe |
| Joyside/`joydev` input nodes | ✅ `INPUT_JOYSTICK`, `INPUT_JOYDEV`, auto-load drop-in | `linux.fragment`, live `modules-load.d/pengyos-joydev.conf` |
| Avahi (WiVRn's discovery requires it) | ✅ built and **active** on the live Desktop | `desktop.config`, `systemctl is-active avahi-daemon` |
| Firewall open for VR | ✅ mDNS `udp/5353` accepted, and **all** inbound `udp/tcp 1025-65535` open on Desktop — which covers WiVRn's `9757` and Steam Link's `10400` with no configuration | `board/server/…/etc/nftables.conf:48-49,60` + `board/desktop/…/nftables.d/input-desktop.nft` |
| PipeWire (WiVRn streams a virtual sink + mic) | ✅ PipeWire + WirePlumber, and the Flatpak `xdg-run/pipewire-0` socket is the standard Desktop path | `desktop.config` |
| Xwayland (SteamVR's dashboard is an X11 app) | ✅ Xwayland + `xrdb` + libepoxy + GLX; COSMIC starts it at login | `desktop.config` |
| GameMode | ✅ shipped | `desktop.config` |
| NVIDIA driver with NVENC + Vulkan + GBM/Wayland | ✅ `nvidia-open` 610.57.04 with `NVIDIA_OPEN_GRAPHICS` and `NVIDIA_OPEN_VULKAN`; Turing and newer, so the 3070 / 3060 Ti / 5060 Ti are all in scope; proven on an RTX 3070 on metal | `package/nvidia-open/`, `configs/layers/nvidia.config`, `CHANGELOG.md:970-992` |
| Vulkan loader + Intel/AMD/NVIDIA ICDs | ✅ `vulkan-loader`, `radv`, `anv`, `lavapipe`, NVIDIA's ICD via the graphics layer | `desktop.config`, `nvidia.config` |
| A distro userspace for the things we cannot recipe | ✅ podman + distrobox (Lane 4), reproducibility-proven from an Ubuntu 26.04 box on a PengyOS Desktop | `SPEC.md` §2.5, §"Reproducibility" |

Read as a checklist, the OS is *nearly* a VR host already: the kernel, input, udev,
discovery, firewall, audio and driver layers are all present and verified.

---

## 6. The four gaps that make it "not today"

### 6.1 Valve does not support SteamVR inside the Steam Flatpak — and that is our Steam lane

`USE-CASES.md` US-DSK-4 makes Steam a **Flatpak from the COSMIC store** the primary
path, with the `.deb` and a guest as explicit fallbacks. Valve's own support entry is
unambiguous:

> "SteamVR for Linux cannot run properly within the unsupported Steam Snap or Steam
> Flatpak packages as they break both Direct Rendering Manager (DRM) leasing and
> asynchronous reprojection. The native distribution package should be used instead."

That is the load-bearing contradiction in the gaming story: the app lane that makes
Steam installable from the store with no bespoke work is the lane SteamVR is
documented not to work in. It is not a PengyOS quirk — it is Valve's position, and it
applies equally to Fedora Workstation's Steam Flatpak. It bites us harder because we
have *no* native package lane: the base is read-only and changes only by image.

Consequence: **any SteamVR-based path (Steam Link VR, ALVR, WiVRn-with-SteamVR) needs
Steam running natively**, which on PengyOS means Lane 4 (a distrobox userspace) or a
future Lane-2 prefix, not the store.

### 6.2 The image carries no Flatpak NVIDIA driver extension

Flatpak apps get GPU access from a runtime extension. The Desktop image ships the
Mesa extensions; on the live PengyOS box `flatpak list` shows
`org.freedesktop.Platform.GL.default` and `GL32.default` and **no
`org.freedesktop.Platform.GL.nvidia-*`**. `pengyos-flatpak-setup.sh` adds the Flathub
remote and nothing else, and nothing in the tree installs the NVIDIA extension.

So on an NVIDIA machine, today, Flatpak apps (Steam, the WiVRn server, Heroic,
Discord) see Mesa/lavapipe, not the GPU's Vulkan. `nvidia.config` even states the
expectation — "Flatpak apps (Steam) still get NVIDIA Vulkan/GL from the Flathub GL
extension that matches this driver version" — as a fact about the world, not as
something the image arranges. **This is a one-line-ish fix (install the extension
matching 610.57.04, ideally in the first-boot setup) and it is the highest-value
gaming fix in the whole list.**

### 6.3 No recipes for the VR stack; the Flatpak is the only lane

`buildroot-external/package/` has `steam-devices` and `gamemode` and nothing VR:
no WiVRn, no openxr-lite, no xrizer, no OpenComposite, no Monado, no gamescope
(US-DSK-4 lists gamescope as "optional", and it is not packaged). WiVRn's dependency
set (openxr-lite, avsc, eigen, libav, Qt for the dashboard) is a real recipe project,
not a one-liner.

That leaves the **WiVRn Flatpak** (Flathub, v26.9, ~6.8k installs/month) as the only
delivery path on PengyOS. It is a good Flatpak — it bundles **xrizer and
OpenComposite**, so OpenVR/SteamVR games work through it without extra layers — but
it carries documented sandbox costs: **no SteamVR Lighthouse-tracked device support**,
and no headset→PC virtual gamepad/keyboard forwarding. Fine for a Quest-only setup;
disqualifying if Index controllers or Vive trackers ever enter the house.

### 6.4 Small, sharp, fixable

- **`ntsync` is built but never loaded.** No `modules-load.d` entry names it, so
  `/dev/ntsync` does not exist on the live box. Proton 11+ (which enables ntsync by
  default) silently falls back, and the community guidance for Linux VR explicitly
  lists ntsync as a requirement — games like VTOL VR misbehave without it. Fix: one
  line, `etc/modules-load.d`, same pattern as the uinput drop-in.
- **NVENC parks the GPU in P2.** WiVRn documents that its NVENC path uses CUDA, which
  can hold NVIDIA cards out of P0, and ships a `CudaNoStablePerfLimit` application
  profile as the fix (`docs/nvenc.md` upstream). PengyOS ships no NVIDIA application
  profiles at all. Fix: ship the profile the way Pop!_OS and CachyOS do.
- **SteamVR must already be running when the headset asks for a game.** Valve
  confirmed 2026-09-25 on Linux hosts (SteamVR 2.18.1 beta, `CRemoteClientJobStartStream
  timed out k_ERemoteClientWaitVRConnectionReady`), with no ETA. A documented ritual,
  not a bug of ours.
- **No Spacewarp / motion smoothing exists on Linux.** On a weak GPU that halves your
  frame rate; on a 3070 with a 958×1200 panel it is a non-issue for Beat Saber-class
  titles, and a real issue for heavy UE titles.
- **Beat Saber is Unity OpenXR, not OpenVR** (BotTalk `7e8eba39e0e9a62dbc000000`,
  `bf779152325aae4ac1000000`): the *active OpenXR runtime* is what matters, and a
  dangling `~/.config/openxr/1/active_runtime.json` produces
  `XR_ERROR_RUNTIME_UNAVAILABLE`, not a useful error. WiVRn sets and reverts that
  runtime on connect, which is the cleanest version of this — but it means a WiVRn
  experiment on a box that also runs SteamVR needs the same cleanup discipline we
  already paid for once.

---

## 7. The paths, ranked

**Path A — PengyOS Desktop NVIDIA as the VR host, WiVRn as the runtime. Viable, with
work.** Target behotv (RTX 3070, wired, proven driver) or behoserver (5060 Ti, 16 GB
VRAM, AV1 *encode* as well).

1. Install the Flatpak NVIDIA GL/Vulkan extension matching 610.57.04 (gap 6.2).
2. Load `ntsync` (gap 6.4).
3. Install the WiVRn server Flatpak; the Quest 3S client comes from the Meta Store.
4. Run Steam **natively** — a distrobox (Lane 4) is the honest version of this on an
   image-based OS — with SteamVR installed, and grant the WiVRn path overrides WiVRn
   documents for Flatpak Steam.
5. Dedicated 5 GHz AP (§4).
6. Start SteamVR first, then the game.

Expected outcome: Beat Saber, Job Simulator, Superhot, Until You Fall and the OpenXR
majority at 90 Hz, at quality comparable to the current Ubuntu host. Not expected:
anti-cheat titles, heavy UE titles at 120 Hz, Lighthouse trackers.

**Path B — Steam Link VR (Valve's `vrlink` driver) on the same host. Viable, zero new
packages, worst reputation.** It is the path we already diagnosed as laggy on this
house network (BotTalk `c7cb9f01bf05651ca3000000`), it inherits gap 6.1 (it lives
*inside* SteamVR, so it needs native Steam), and Valve's current Linux streaming bug
is in exactly this launch path. Its virtue is that it is Valve's own supported
Quest route and needs no third-party runtime.

**Path C — keep the VR host on Ubuntu, run PengyOS everywhere else. Viable today,
zero work.** This is the honest description of the current setup. The 2026-09
investigation left behotv's SteamVR stack working; the remaining behotv problems were
network problems, which PengyOS would not have fixed.

**Path D — make PengyOS a real VR host. The only path that changes the answer.**
In cost order: (1) Flatpak NVIDIA extension, (2) `ntsync` modules-load line,
(3) NVIDIA application profile, (4) a WiVRn + xrizer recipe (Lane 1) so the Lighthouse
and input-forwarding features stop being Flatpak casualties, (5) gamescope,
(6) a documented native-Steam lane for US-DSK-4, whose acceptance criteria
("a Proton title runs at expected performance") SteamVR-through-Flatpak cannot meet.

---

## 8. What to do next, in order

1. **Fix the radio first.** Nothing else in this document matters until the headset
   has a dedicated 5 GHz AP. It is the cheapest change and the one the September
   diagnosis already proved.
2. **Land the three one-liners** (Flatpak NVIDIA extension, `ntsync` auto-load,
   NVIDIA application profile). They are gaming fixes in general, not VR-specific,
   and they are testable on behoweather and on a behotv validation build.
3. **Then** run the Path A experiment on behotv with a validation build, with the
   same log-driven method used in September (`driver_vrlink.txt`, `vrserver.txt`,
   WiVRn's server log at `~/.local/state/wivrn/`).
4. **Do not** start with a WiVRn recipe. The Flatpak answers the question; the recipe
   is Path D polish.

---

## 9. Not verified / open

- **No measurement was taken for this document.** No PengyOS machine in the fleet has
  a VR-capable GPU, so every statement about PengyOS + Quest is a requirements
  analysis, not a result. The first real number requires Path A step 1–3 on behotv.
- Whether the Flathub NVIDIA extension for the **610.57.04** branch exists and
  auto-installs cleanly on an image with no distro-provided flatpak auto-install
  machinery — unconfirmed.
- Whether SteamVR inside an **Ubuntu distrobox** on PengyOS Desktop NVIDIA gets GPU,
  audio, udev device access and Xwayland correctly — untested. The pieces are all
  present (driver in the image, `--nvidia`-style sharing documented for the CUDA dev
  box, Xwayland, PipeWire), and distrobox Steam is a known-working configuration
  elsewhere, but "the pieces are present" is not "it works".
- COSMIC-specific compositor interactions with SteamVR's X11 windows (scaling,
  focus, the dashboard) — untested.
- 120 Hz on the 3S over Wi-Fi: treat as out of budget at 2×2 Wi-Fi 5.
- Anti-cheat VR titles: same story as everywhere else on Linux, i.e. no.

---

## Sources

- Repo: `README.md` (editions, "what isn't ready"), `SPEC.md` §2.5 (the four lanes),
  `USE-CASES.md` US-DSK-4 / US-DSK-10 / US-DSK-14, `CHANGELOG.md` (NVIDIA 610.57.04
  proven on behotv 2026-10-08; first Desktop NVIDIA metal boot 26.10.7),
  `buildroot-external/configs/layers/{desktop,nvidia,nvidia-compute}.config`,
  `board/desktop/linux.fragment`, `board/desktop/rootfs-overlay/usr/lib/pengyos/nftables.d/input-desktop.nft`,
  `board/server/rootfs-overlay/etc/nftables.conf`,
  `board/desktop/rootfs-overlay/usr/libexec/pengyos/pengyos-flatpak-setup.sh`,
  `package/steam-devices/`, `package/nvidia-open/`.
- Live measurements 2026-10-15: behoweather (PengyOS Desktop 26.10, kernel 7.2.5 —
  `/dev/uinput` present, `/dev/ntsync` absent, avahi active, Steam Flatpak
  1.0.0.87, no NVIDIA flatpak extension, steam udev rules present);
  behotv / behoserver / behobasement (Ubuntu 26.04.1, kernel 7.0.0-38);
  behotv driver 595.91.07 on an RTX 3070, wired `enp34s0`, Wi-Fi down.
- BotTalk: `c7cb9f01bf05651ca3000000` (Steam Link lag diagnosis, Deco mesh root
  cause), `7214d66ead64ab87a9000000` (Intel AC 9x6x cannot beacon 5 GHz),
  `23a38a800a0a23e959050000` (Deco mesh ≈ 80 Mbit/s ceiling),
  `7e8eba39e0e9a62dbc000000` + `bf779152325aae4ac1000000` (Beat Saber is OpenXR;
  the active-runtime gotcha), `569a1fd8b09ffcc8b0000000` +
  `4fd298da7c1249b1af000000` (AMD vs NVIDIA on Linux for VR; the NVIDIA wobble bug).
- Web (2026-10-15): WiVRn README + Flathub page (v26.9; Quest 3S supported; Flatpak
  bundles xrizer + OpenComposite; Lighthouse unavailable in the sandbox; avahi +
  5353/9757; `docs/nvenc.md` P-state profile); WiVRn guide (chaosmaou/wivrn-guide —
  Wi-Fi 5+ requirement, dedicated AP, wired gigabit, ntsync, no Spacewarp);
  Linux VR Adventures Wiki (WiVRn install + Steam Flatpak overrides + firewall ports);
  ArchWiki *Virtual reality* (Quest 2/3/3S/Pro supported by WiVRn, ALVR and Steam
  Link); Valve support entry "SteamVR for Linux Support" (Snap/Flatpak unsupported:
  DRM leasing, async reprojection); Valve's 2026-09-25 confirmation that SteamVR must
  be started before the headset requests a game; ALVR Flatpak wiki
  (`setcap CAP_SYS_NICE` on `vrcompositor-launcher`, PipeWire socket override);
  Meta Quest 3S published specifications (panel, refresh rates, Wi-Fi 5 2×2).
