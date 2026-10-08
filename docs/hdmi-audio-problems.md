# behoweather HDMI audio — investigation log (resolved 2026-10-06)

Working notes. Started as "did the 26.10.6 volume-boost fix break behoweather's
audio?" It did not. The box was silent over HDMI. **Read §0 first** — that is
today's result; §1–§13 are the 2026-10-05 log and are kept for the method, but
several of its conclusions are superseded.

---

## 0. RESOLVED 2026-10-06 — the fix, and where the fault actually is

**Working configuration, verified on the box:** enable ACP and pin the card
profile to `output:hdmi-stereo`. COSMIC sound effects and YouTube then play
through the monitor, loud and clear (operator-confirmed), with `pcm3p` RUNNING
S32_LE 2ch/48k, codec `Converter: stream=1`, and PipeWire links
`output_FL/FR > Display:playback_FL/FR [active]`.

In place as a user-level workaround:
`~/.config/wireplumber/wireplumber.conf.d/52-acp-hdmi.conf` (sets
`api.alsa.use-acp = true` for `~alsa_card.pci-.*`), plus the profile pinned to
`output:hdmi-stereo`, which WirePlumber stores in
`~/.local/state/wireplumber/default-routes` and which survived both a
`wireplumber` restart and a full `pipewire pipewire-pulse wireplumber` restart.

**The fault is PipeWire's split-mode ALSA sink node.** Not the monitor, not the
cable, not the codec, not i915, not the kernel, not the gain service.

The two measurements that settled it:

1. **`aplay -D hw:0,3` is audible.** Direct ALSA on the very PCM PipeWire writes
   to silently. So everything from the ALSA layer down is fine. (Also: PipeWire
   does **not** hold `pcm3p` open when idle — `sub0/status` reads `closed` — so
   this bypass test needs no service restart at all. §10 step 1 was cheaper than
   it looked.)
2. **A full state diff between the working (ACP) and broken (split) configs is
   5 lines long**, and all 5 are PipeWire node properties. Every `amixer`
   control, the entire `codec#2` node dump, `eld#2.4`, and every `snd_hda_*` /
   i915 module parameter are **byte-identical**.

The signature of the broken sink:

| | split mode (silent) | ACP `hdmi-stereo` (works) |
|---|---|---|
| `api.alsa.path` | `hw:0,3` | `hdmi:0` |
| `audio.channels` | **64** | 2 |
| `api.alsa.pcm.subclass` | `generic-mix` | — |
| `EnumFormat` | `raw` only | `raw` **+ `iec958` (`PCM`)** |
| active `channelMap` | `[]` | `[FL, FR]` |

**Two candidate mechanisms were tested and are ruled out**, so this is not a
one-line fix:

- Pointing the split sink at the alsa-lib alias — `api.alsa.path = "hdmi:0"` on
  `playback.3.0` while keeping `use-acp=false`: **still silent**. So it is not
  `HDA-Intel.conf`'s `hooks.0` ctl elems (`IEC958 Playback Default` /
  `IEC958 Playback Switch`).
- Forcing `audio.channels = 2` + `audio.position = [FL FR]` on the split sink:
  **still silent**. So the 64-channel advertisement is a symptom, not the cause.

What is left is inside PipeWire's ALSA device setup, and it needs PipeWire-level
instrumentation, not more box probing.

### 0a. The thing that makes this hard to ship

ACP alone reproduces the original 2026-09-29 bug. ACP auto-selects
`output:analog-stereo+input:analog-stereo` (**priority 6565**) over
`output:hdmi-stereo` (**priority 5900**), because the ALC897 line-out has **no
jack-detect sensor**, so the analog profile reports `available=yes` even with
nothing plugged in. With ACP on and no pin, the box has an analog sink and **no
HDMI sink at all** — measured, twice.

`device.profile.priority.rules` is the supported override, but:

- `priorities` must be a **JSON string**, not a WirePlumber array.
  `find-preferred-profile.lua` does `Json.Raw(props["priorities"]):parse()`; an
  array yields nothing and the hook is silently skipped. My first attempt failed
  this way.
- The hook matches profiles **by name only and ignores `available`**, so a
  blanket "prefer hdmi-stereo" rule would force HDMI on a laptop with no monitor
  attached and kill its speakers. Not shippable as-is across the fleet.

So the shipped fix has to be conditional — prefer the HDMI profile only where an
HDMI display with audio is actually present — or split mode has to be fixed
properly in PipeWire.

### 0b. Screen blanking explained

The blanking the operator saw is **not** an audio artifact and **not** COSMIC:
`~/.config/cosmic/com.system76.CosmicIdle/v1/screen_off_time` is `None`.

With `drm.debug` turned on, starting audio while the GPU had runtime-suspended
produced:

```
[CRTC:151:pipe A] fastset requirement not met in has_hdmi_sink (expected no, found yes)
[CRTC:151:pipe A] fastset requirement not met in pipe_bpp (expected 0, found 36)
[CRTC:151:pipe A] fastset requirement not met in port_clock (expected 0, found 222750)
[CRTC:151:pipe A] fastset requirement not met in infoframes.enable (0x00000000 -> 0x00000031)
[CRTC:151:pipe A] fastset requirement not met, forcing full modeset
[skl_compute_wm] active pipes 0x0 -> 0x1
[intel_cdclk_atomic_check] Modeset required for cdclk change
```

i.e. the pipe was **down** and the audio call resumed it with a **full modeset**
— which is a black screen. `plane_mask` in `state` went 29 → 0 → 21, and with
`plane_mask=0` the pipe shows only `background_color` (opaque black). Toggling
ACP also re-probes the connector and blanks. Both are consistent with the
2026-10-05 Ubuntu observation ("heard some, screen blanked").

### 0c. Diagnostic gap worth fixing in the image

**`CONFIG_DYNAMIC_DEBUG` is off** in the PengyOS kernel, so
`/sys/kernel/debug/dynamic_debug/control` does not exist and every
`drm_dbg_kms()` / `dev_dbg()` in i915 is compiled out — the entire audio/cdclk
trace was invisible until it wasn't. The workaround that worked:

```sh
echo 70 > /sys/module/drm/parameters/debug   # 0x46 = KMS|DRIVER|ATOMIC
echo 7  > /proc/sys/kernel/printk            # cmdline has loglevel=4
```

`drm.debug` is independent of dynamic debug. Consider enabling
`CONFIG_DYNAMIC_DEBUG` (or at least `CONFIG_DRM_USE_DYNAMIC_DEBUG`) for Desktop
images; without it, i915 display bugs are very hard to see.

### 0d. Ops gotchas hit today

- **Restarting the audio stack orphans COSMIC, and it does not recover.**
  `cosmic-settings-daemon` dropped off PipeWire and, after `pkill`, **did not
  respawn** — COSMIC is not a systemd user unit here (`cosmic-session` is
  spawned by `greetd` → `dbus-run-session`, pid 657). Volume hotkeys stayed dead
  until a logout/login. The ledger's "restart cosmic-session" advice is right,
  but on this image that means a real session restart, not a service restart.
- `pw-record` has **no duration flag**; `timeout` (SIGTERM or SIGINT) kills it
  before it finalises the WAV and you get a 44-byte header-only file that looks
  like "zero samples". Use `wpctl inspect` / link state instead, or stop it
  cleanly.
- `wpctl status` sink lines start with box-drawing characters — `grep -oE '^ *[0-9]+'`
  finds nothing. Parse `pw-dump` instead.
- `i915_cdclk_info` says `Current CD clock frequency:`, **not** `cdclk` — a
  `grep cdclk` returns nothing and looks like "no data".
- `i915_display_info` / `state` live under `/sys/kernel/debug/dri/1/`, and
  `output_bpc=0` / `tmds_char_rate=0` there are dump artifacts, not real state.
- Baseline facts now recorded (they were missing before): **WirePlumber 0.5.14**,
  PipeWire 1.6.6, kernel 7.2.5, cmdline
  `BOOT_IMAGE=/slot-a/bzImage … ro rootwait init=/usr/lib/pengyos/preinit
  pengyos.data=4 audit=0 splash plymouth.ignore-serial-consoles console=tty0
  loglevel=4 console=ttyS0,115200` — no snd or i915 options.

---

## 1. Bottom line as of 2026-10-05 (superseded by §0)

**The monitor is NOT the fault.** That was my conclusion earlier on 2026-10-05
and it was **wrong** — see §7. Ubuntu live on the *same box, same cable, same
port, same monitor, same ALSA PCM* is audible. So the fault is in PengyOS's
software stack, somewhere between PipeWire/WirePlumber and the kernel, and it is
specific to PengyOS. §0 narrows that to **PipeWire's split-mode sink node**.

**The 26.10.6 unity-gain change is not the cause.** Ruled out with evidence (§6).

**Everything I can measure on the PengyOS side looks correct** — audio verifiably
reaches the HDMI pin, the codec converter is assigned to a stream, the pin is
OUT and unmuted, the pipe has audio enabled. It is silent anyway.

---

## 2. The machine and the display

**behoweather** — Intel N100 mini-PC (Alder Lake-N), back in the N100 chassis
(the ThinkPad L14 Gen 1 detour of Oct 3–5 is over; the disk is home).

- PengyOS Desktop **26.10.8**, kernel **7.2.5**, channel `testing`
- GPU: i915, `Found alderlake_p/alderlake_n (device ID 46d1) integrated display
  version 13.00 stepping D0`
- Audio: `HDA Intel PCH` at 0x6001110000 irq 142; analog codec **ALC897** driven
  by `snd_hda_codec_alc662`; HDMI codec **0x8086281c** reported as
  `Codec: Intel Alderlake-P HDMI`, bound to i915
  (`snd_hda_intel 0000:00:1f.3: bound 0000:00:02.0`)
- Net: `enp1s0` 10.0.23.4 (reserved)
- PipeWire 1.6.6 (from `wpctl status` header). **WirePlumber version not yet
  recorded** — do that.

**The display** (HDMI-A-1): 15.6" 1080p60 portable monitor, physical 350x190 mm.
EDID: mfg `YHI` (0xE509), product 0x2556, 2024 wk 17, descriptors `Display` and
`demose t-1` — a generic copy-paste EDID. CEA extension rev 3, header byte 3 =
**0xf2** → **Basic Audio bit set**; HDMI VSDB present (IEEE 0x000C03); SAD =
LPCM 2ch, 32/44.1/48/88.2/96/176.4/192 kHz. Max bpc 12.

Operator confirmed the monitor's **OSD volume is at 10** (its own slider).

---

## 3. The two prior fixes, for context

1. **2026-09-29 — sinks didn't exist.** WirePlumber defaults every ALSA card to
   ACP (`api.alsa.use-acp=true`), which for this plain HDA card built only
   "Analog Stereo Duplex", so the HDMI PCMs never became sinks. Fix: new
   `package/alsa-ucm-conf/` recipe plus board overlay
   `usr/share/wireplumber/wireplumber.conf.d/51-alsa-ucm.conf` setting
   `api.alsa.use-acp = false` for `device.name = ~alsa_card.pci-.*`.
   **Correction that matters:** `use-acp=false` does *not* select UCM — it
   bypasses ACP **and** UCM and exposes each ALSA PCM directly ("split mode").
   alsa-ucm-conf has no profile for a plain HDA card anyway. That is why PengyOS
   lists **five** sinks while Ubuntu lists **two**.
2. **26.10.6, commit `afcefef8` — the "volume boost".** `pengyos-alsa-unity.service`
   runs after `alsa-restore` at boot and sets each PCI card's `Master` and `PCM`
   to 0 dB, because on the split path PipeWire only does software volume and the
   codec's power-on level (-20 dB on a ThinkPad T14's ALC257, -23 dB on the
   N100's ALC897) made 100% in Settings quiet.

---

## 4. Measurement toolkit (all staged on the box, `/tmp/`)

The key trick: **`pw-record --target <sink id>` records what a sink actually
receives** (PipeWire monitor ports are on by default). That separates "the app is
muted" from "the output is broken" without needing ears.

| Script | Needs root | What it proves |
|---|---|---|
| `/tmp/userprobe.sh` | no | Lists sinks, plays 8 beeps, captures the Display sink and reports sample count/peak, shows which PCM is RUNNING |
| `/tmp/audioprobe.sh` | no | Auto-detects which ALSA PCM has a display with a valid ELD, maps it to the PipeWire sink, retargets default, plays, captures |
| `/tmp/codecstate.sh` | no | Plays a 3 s tone and dumps `codec#2` converter/pin state **while playing** — the `Converter: stream=N` line is the tell |
| `/tmp/i915state.sh` | yes | cdclk, IPC, LPSP, SAGV, runtime PM, plane count, pcm states, codec pin/converter, `/proc/cmdline` |
| `/tmp/modparams.sh` | no | i915 + snd_hda_* module parameters, SOF bound or not |

Handy one-liners:

```sh
# run PipeWire tools as the session user
sudo -u beholder env XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus wpctl status

# what a sink really receives
pw-record --target 51 --format s16 /tmp/cap.wav
ffmpeg -v error -y -i /tmp/cap.wav -f s16le /tmp/cap.raw
od -An -t d2 /tmp/cap.raw | tr ' ' '\n' | awk '$0~/^-?[0-9]+$/{n=$1<0?-$1:$1; if(n>m)m=n; if($1!=0)nz++; t++} END{print t, nz, m}'

# tone
ffmpeg -v error -y -f lavfi -i "sine=frequency=700:duration=0.5" -ar 48000 -ac 2 /tmp/beep.wav

# which PCM has a live display with audio
amixer -c0 contents | awk '/name=.ELD.,device=/{match($0,/device=[0-9]+/);d=substr($0,RSTART+7,RLENGTH-7);next} /: values=0x/{if(d!=""){print d;d=""}}'
```

---

## 5. What was measured on PengyOS 26.10.8 (all verified, several times)

**Gain / mixer** — `pengyos-alsa-unity.service` active, journal:
`alsa-unity: card 0: Master [0.00dB]` / `PCM [0.00dB]`.
`amixer`: Master 87 [100%] **[0.00 dB]** on; Front L/R 87 **[0.00 dB]** on;
PCM 255 **[0.00 dB]**. Headphone switch **off**, volume 0 (latent gap, §10).

**Sinks** (`wpctl status`): 48 `Built-in Audio` (hw:0,0), **51 `Built-in Audio
(Display)` (hw:0,3)**, 52/53/54 `HDMI 1/2/3` (hw:0,7/8/9). Default sink =
`alsa_output.pci-0000_00_1f.3.playback.3.0`.

**PipeWire really carries audio** — `pw-record --target 51` peaks seen: 9616,
15241, 2896 (of 32767), with 141k–569k non-zero samples. Through Flatpak VLC too
(peak 11366). Analog sink 48 also real (peak 14042, `pcm0p` RUNNING).

**ALSA** — `pcm3p/sub0/status` = `state: RUNNING` with advancing hw_ptr;
`hw_params`: `access: MMAP_INTERLEAVED`, `format: S32_LE`, **channels: 2**,
**rate: 48000**, period_size 1024, buffer_size 32768.

**ELD** — `/proc/asound/card0/eld#2.4`: `monitor_present 1`, `eld_valid 1`,
`monitor_name Display`, `connection_type HDMI`, LPCM 2ch, rates 32–192 kHz.
`amixer` shows `ELD,device=3` populated and `Playback Channel Map,device=3` =
`3,4` (FL,FR).

**Codec (`/proc/asound/card0/codec#2`, Intel Alderlake-P HDMI)** — while playing:
- `Node 0x03 [Audio Output]` → **`Converter: stream=1, channel=0`** (assigned!)
- `Node 0x06 [Pin Complex]` → **`Pin-ctls: 0x40: OUT`**, `Amp-Out vals [0x00 0x00]`
  (unmuted), `Connection: 4` with **`0x03*`** selected
- other converters `stream=0`, other pins `Pin-ctls: 0x00` (correct — only one
  connector is live)
- `Power: setting=D0, actual=D0`; `snd_hda_intel power_save=0`

**i915** — `[CONNECTOR:390:HDMI-A-1]: status: connected`, **`audio support: yes`**,
max bpc 12, `[ENCODER:389:DDI B/PHY B]`, `bpp=36`, `port_clock=222750`,
`lane_count=4`, mode `1920x1080 60 148500`. `output_bpc=0`, `tmds_char_rate=0`
in `state` (same as Ubuntu). `infoframes/` directory exists but is **empty** —
i915 doesn't use the DRM infoframe debugfs files, so that file is unavailable on
*both* OSes and is not a discriminator.

**Module parameters** — `snd_hda_core.gpu_bind=-1`;
`snd_hda_codec_hdmi.enable_acomp=Y`, `enable_all_pins=N`, `static_hdmi_pcm=N`;
`snd_hda_codec_intelhdmi.enable_silent_stream=N`;
`snd_hda_intel power_save=0`, `power_save_controller=Y`.
SOF drivers (`sof-audio-pci-intel-cnl/tgl`) are **loaded but bound to nothing** —
harmless here, but worth knowing.

**No modprobe.d options** for i915 or snd on the Desktop board (only
`60-pengyos-amdgpu.conf`, which is amdgpu/nouveau).

---

## 6. Audibility log (operator as the sensor)

| Time | What played | Measured at the sink | Heard |
|---|---|---|---|
| 19:41:50–53 | 3 beeps → Display sink 51 | `pcm3p` RUNNING | **nothing** |
| 19:41:58–42:01 | 3 beeps → analog sink 48 | `pcm0p` RUNNING | **nothing** |
| 20:02:13–25 | 8 loud beeps → Display, volume forced 1.0, unmuted | `pcm3p` RUNNING 2ch/48k | **nothing** |
| ~20:06 | operator paired **Sony ATH-DSR7BT**, switched output | BT sink 131, capture peak 1229 | **works immediately, YouTube fine** |
| 20:15:44–53 | after reboot + cold power-cycled monitor + OSD vol 10 | capture peak 9616, `pcm3p` RUNNING | **nothing** |
| 00:45–00:47 (Ubuntu live) | beeps via Ubuntu | `pcm3p` RUNNING | **works** |
| 00:47:46–55 (Ubuntu live + our split-mode override) | 6 beeps | `pcm3p` RUNNING | **heard some, screen blanked** ("3 times first, 3 times second, could still hear them the second time") |
| 21:17:29–39 (PengyOS, no apps) | 8 beeps | capture 141012 non-zero samples | **nothing** |
| 21:27:09–19 (repeat) | 8 beeps | same | **nothing** |

---

## 7. The Ubuntu A/B — the most valuable result

Ubuntu 26.04 live USB, kernel **7.0.0-14-generic**, PipeWire **1.6.2**,
WirePlumber **0.5.13**. Same box, same cable, same HDMI-A-1 port, same monitor.
Operator: "works 100% perfect."

**Identical to PengyOS** (so all of these are ruled out as the cause):

| | Ubuntu | PengyOS |
|---|---|---|
| PCM written to | `pcm3p` RUNNING, S32_LE, 2ch, 48k | same |
| ELD | `eld#2.4` present/valid, `Display`, LPCM 2ch | same |
| Codec wiring | pin 0x06 `OUT`, converter `0x03*`, amp unmuted | same |
| Encoder / pipe | DDI B/PHY B, bpp=36, port_clock 222750, 4 lanes | same |
| `infoframes/` | empty | empty |
| `Playback Channel Map,device=3` | `3,4` | `3,4` |
| Split mode applied | **applied it — still audible** | already on |
| cdclk while playing | 192000 kHz | **192000 kHz with no apps running — still silent** |
| SAGV | available: no | available: no |
| IPC | yes | yes |

**Actually different:**

| | Ubuntu | PengyOS |
|---|---|---|
| Kernel | 7.0.0-14-generic (Ubuntu-patched) | 7.2.5 (vanilla Buildroot pin) |
| PipeWire / WirePlumber | 1.6.2 / 0.5.13 | 1.6.6 / **unknown** |
| ALSA path used | `api.alsa.path = "hdmi:0"`, ACP profile `hdmi-stereo`, node `…hdmi-stereo` | `api.alsa.path = "hw:0,3"`, split mode, node `…playback.3.0` |
| Sink count | 1 HDMI + 1 analog (ACP knows the live connector) | 5 (every PCM exposed) |
| Visible planes | 1 | 2 (no apps) / 3 (with COSMIC surfaces) |
| GPU runtime PM while playing | `GPU idle: yes`, usage count 7 | `GPU idle: no`, usage count 11 |
| `snd_hda_intel power_save` | **1** | **0** |
| Analog Master volume | 69 → **-13.5 dB** | 87 → **0 dB** |
| cmdline | `BOOT_IMAGE=/casper/vmlinuz --- quiet splash` | **not captured yet** |

Note the last row pair: Ubuntu was audible with the codec at **-13.5 dB** while
PengyOS is silent at **0 dB**. That is the cleanest single proof that the
26.10.6 unity-gain change is not the culprit.

---

## 8. Ruled out (with the evidence)

- **The 26.10.6 unity-gain service.** Mixer verified at 0 dB; Ubuntu audible at
  -13.5 dB. Also the service is doing exactly what it was designed to do.
- **Split mode / `51-alsa-ucm.conf`.** Applied verbatim on Ubuntu
  (`~/.config/wireplumber/wireplumber.conf.d/51-pengyos-split.conf`) → Ubuntu
  showed the same five sinks, streamed to `pcm3p`, and was **still audible**.
- **Wrong PCM / wrong converter.** Both OSes use `pcm3p` → converter 0x03 →
  pin 0x06, the pin of the connected display.
- **ELD / SAD mismatch.** ELD valid, LPCM 2ch, and the PCM is opened at 2ch/48k.
- **Codec muted or unpowered.** Amp-Out `0x00` (unmuted), pin OUT, D0,
  `power_save=0`, converter assigned to stream 1.
- **cdclk / SAGV / plane count.** I had a good theory here: PengyOS at 556800 kHz
  with COSMIC surfaces vs Ubuntu at 192000 kHz (i915's audio floor, 2×96 MHz
  BCLK), and i915 only reprograms `AUD_TS_CDCLK` on a cdclk change, not the
  per-transcoder N/CTS. Tested with **no apps running**: cdclk fell to
  **192000**, SAGV unavailable, 2 visible planes — matching Ubuntu — and it was
  **still silent**. Theory dead as stated. (A weaker version survives: cdclk
  *transitions* during a session could still matter — untested.)
- **Monitor / cable / port.** Untouched between the PengyOS failure and the
  Ubuntu success.
- **Monitor OSD volume.** Confirmed at 10.
- **App-level mute as the whole story.** See §11 — it was real at one point, but
  `pw-play` of a generated tone is equally silent.
- **Bluetooth / PipeWire core / Flatpak portal.** The Sony ATH-DSR7BT works
  instantly on the same session, same PipeWire, same Firefox.

---

## 9. Open hypotheses, ranked

1. **PipeWire/WirePlumber version or behaviour difference (1.6.6 vs 1.6.2 /
   0.5.13).** PengyOS opens the PCM as `hw:0,3` with `audio.channels: 64` on the
   sink node (odd — worth a look) and `generic-mix` subclass; Ubuntu opens
   `hdmi:0` through an ACP profile. Something in how the stream is set up could
   be producing a signal the sink won't lock. **Cheapest test, do first.**
2. **Kernel 7.2.5 vs 7.0.** I diffed `sound/hda/codecs/hdmi/intelhdmi.c` and
   `hdmi.c` between v7.0 and master: only keep-alive plumbing (`i915_hsw_prepare`
   added, `snd_hda_codec_write_sync`), a Loongson jack-detect quirk, a new HP
   quirk, `acomp_requested_resume`. Nothing obviously fatal — **but I only
   diffed two files**, not the whole HDA tree, `eld.c`, or i915's audio path.
   Caveat: **tag `v7.2.5` does not exist on the GitHub mirror** (nor any 7.2.x
   point release), so my diffs bracket the change rather than matching 7.2.5
   exactly. Use kernel.org tarballs for a real bisect.
3. **A cdclk *transition* during the session** (not the absolute value) leaving
   the audio N/CTS stale. Untested.
4. **`snd_hda_intel power_save=0` vs `1`**, and GPU runtime PM staying active
   (`GPU idle: no`, usage count 11) vs Ubuntu idling the GPU. Unlikely to *cause*
   silence, but it's a concrete difference and trivially testable.
5. **Something COSMIC/session-side** — the only remaining structural difference
   is that PengyOS runs a compositor with extra planes and Ubuntu live runs a
   plainer session.

---

## 10. Next actions, cheap first

1. **Bypass PipeWire entirely.** `systemctl --user stop pipewire pipewire-pulse
   wireplumber`, then `aplay -D hw:0,3 /tmp/beep.wav` and `aplay -D hdmi:0
   /tmp/beep.wav`. If either is audible, the kernel and the monitor are both
   fine and this is a **PipeWire/WirePlumber stream-setup bug**. This is the
   single most informative test left and takes one minute.
2. **Record PengyOS's WirePlumber version** (`wireplumber --version`) and
   `/proc/cmdline`, then diff both against Ubuntu's.
3. **Try ACP on PengyOS** via user config (no root, no read-only-root fight):
   `~/.config/wireplumber/wireplumber.conf.d/52-try-acp.conf` with
   `api.alsa.use-acp = true`, then `systemctl --user restart wireplumber`.
   ⚠️ Toggling ACP re-probes the connector and **blanked the screen** on Ubuntu;
   expect that. ⚠️ Restarting the audio stack mid-session orphans COSMIC's audio
   clients (Sound settings shows "no devices") — restart `cosmic-session` after.
   **This file was NOT written** — the command was cancelled. The box is still in
   its original split-mode state.
4. **Try format/mmap variations** on the sink: `api.alsa.disable-mmap = true`,
   or force S16/S24, or `monitor.alsa.autodetect-hdmi-channels = true` (the
   `audio.channels: 64` on the sink node deserves an experiment).
5. **Try Ubuntu's module options:** `options snd_hda_intel power_save=1` and see
   whether GPU runtime PM behaviour changes anything.
6. **Enable i915/DRM debug logging** and look for `Enable audio codec on
   [CRTC:…], N bytes ELD`, `aud_ts_cdclk set to M=…, N=…`, and any
   `Not valid for port` from `sync_audio_rate`. That would show whether the
   audio clock ratio is ever programmed.
7. **Kernel bisect** if 1–6 are clean: pin `BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE`
   in `buildroot-external/configs/layers/core.config` to 7.0 (Ubuntu's line) and
   build a Desktop image. That's a one-line change and a clean experiment.
8. **Latent gap, separate from this bug:** `alsa-unity` only touches `Master` and
   `PCM`. On this ALC897 the front headphone path (codec node 0x1b, `[Jack] HP
   Out at Ext Front`) sits **muted at 0**, so headphones on the front jack are
   silent until something unmutes them. Extending the script to
   `Front`/`Speaker`/`Headphone` is a small, safe change — but note Ubuntu leaves
   `Headphone` off too, so it is not this bug.

---

## 11. Side findings worth keeping

- **Firefox (Flatpak 157) can carry digital silence.** `pw-record --target 51`
  returned 190464 frames of **exact zeros** while `pw-top` showed the stream
  `[active]` at volume 1.0, mute false, `media.name` = the YouTube title. A muted
  player/tab looks healthy in every tool except a capture. Check the app's own
  mute before blaming the OS.
- **debugfs connector force is a one-way door on this kernel.**
  `/sys/kernel/debug/dri/1/HDMI-A-1/force` accepts only `on` / `off` — `unspecified`,
  `probe`, `clear`, `unset` are all rejected — so anything written there survives
  until a reboot. I set `off` during diagnosis; the operator's reboot cleared it
  (verified back to `unspecified`). Don't leave it set.
- **Toggling ACP re-probes the connector** and blanked the screen on Ubuntu.
- **Restarting the audio stack ≠ enough**: `systemctl --user restart pipewire
  pipewire-pulse wireplumber` orphans COSMIC's audio clients; restart
  `cosmic-session`.
- **`pw-top -b -n 1` prints all zeros** — take the last block, or use `-n 3`.
- **`pw-record` needs the tone playing at the same time** — start the player
  first, sleep ~0.5 s, then record.

---

## 12. Corrections owed to BotTalk (done 2026-10-06)

- Post `1e2236a2ceb0a5df1e010000` ("Fixed: PengyOS Desktop no HDMI audio…") —
  **updated 2026-10-06**. It also carries a stale `human_annotation` from
  2026-09-29 ("RESOLVED: on a clean REINSTALL HDMI audio PLAYS") which sat *below*
  the disproval and is easy to act on by mistake; the body now states the actual
  resolution and flags that annotation as superseded.
- Post `64753f335d02ca5d1b010000` (in-place mutation ledger) — **updated
  2026-10-06** with the ACP workaround now applied in place on the box.
- Post `9bd0660d2cfee32d70010000` (the layer-by-layer debug recipe) — **updated
  2026-10-06**. The method worked and did finish the job, but it needs a step 0:
  **prove the ALSA layer alone with `aplay -D hw:0,N` before anything else.**
  That is one minute, needs no service restart, and collapses the search space
  immediately — the 2026-10-05 pass spent a full day on layers 1–5 before doing
  it.

---

## 13. State left on the box (2026-10-06, end of session)

**Audio is working on the box right now**, via the ACP + `output:hdmi-stereo`
configuration.

- **User WirePlumber override present:**
  `~/.config/wireplumber/wireplumber.conf.d/52-acp-hdmi.conf` (mode 644) —
  `api.alsa.use-acp = true` for `~alsa_card.pci-.*`. This **overrides the shipped
  `51-alsa-ucm.conf` split-mode rule**, so the box is *not* in shipped
  configuration any more. Delete that one file to go back.
- **Card profile pinned** to `output:hdmi-stereo`, stored in
  `~/.local/state/wireplumber/default-routes`
  (`alsa_card.pci-0000_00_1f.3:profile:output:hdmi-stereo=["hdmi-output-0"]`).
  Survives `wireplumber` restart and full audio-stack restart. **Not yet proven
  across a reboot or a fresh login.**
- **`cosmic-settings-daemon` is dead and will not respawn** (killed during
  diagnosis; COSMIC is not a systemd user unit here). Volume hotkeys are dead
  until a **logout/login**. Everything else in the session is alive.
- `/` is still remounted read-write from earlier sessions (dev-box convention).
- No debugfs flags set. `drm.debug` restored to `0`, `printk` restored to `4`.
- Probe scripts staged in `/tmp` (reusable): `snapshot.sh`, `portcfg.py`,
  `profinfo.py`, `sinkinfo.py`, `routes.py`, `meter.sh`, `drmdbg.sh`,
  `audiotrace.sh`, `cdclkwatch.sh`, `idiag.sh`, plus tone files
  `p_slow.wav` (6 slow beeps, 11.4 s — the pattern that works best with a human
  verifier), `p_three.wav`, `p_long.wav`, `p_rapid.wav`, `tone.wav`,
  `t{440,880,1760,3520}.{s16,s32}`.
- Test-pattern design note: **this operator cannot identify pitches.** Use
  *rhythm* (three beeps / one long tone / machine-gun / slow spread) and a long
  window, and announce the window in advance. Pitch-based A/B matrices produced
  unusable results twice.
- Earlier 2026-10-05 notes still stand: Ubuntu live session is gone;
  `/tmp/live-known_hosts` on behoserver holds a stale key for 10.0.23.4;
  `behoweather`'s real key is in `~/.ssh/known_hosts`. If the live USB is booted
  again, sshd is not started by default and the host key changes every session.

## 14. What is still open

1. **Why the split-mode sink is silent.** Localised to PipeWire's ALSA sink
   setup; the alsa-lib alias and the sink channel count are both ruled out. Needs
   PipeWire-side instrumentation (`PIPEWIRE_DEBUG`, `impl_alsa` buffer inspection)
   or an upstream report with the working/broken diff in §0 as the reproducer.
2. **The shipped fix.** Two live options, neither proven:
   - ACP + a *conditional* HDMI profile preference (conditional because
     `find-preferred-profile` ignores `available`, so a blanket rule would break
     laptops). Needs a condition that keys on a connected HDMI display with audio.
   - Keep split mode and fix PipeWire's HDMI sink. Better outcome, more work.
   Either way the fix belongs in
   `buildroot-external/board/desktop/usr/share/wireplumber/wireplumber.conf.d/`,
   and per the ledger it must be proven from a **flashed image** (QEMU first via
   `scripts/run-qemu-disk.sh`), not in place.
3. **`CONFIG_DYNAMIC_DEBUG` off** in the Desktop kernel — worth reconsidering;
   see §0c.
4. **Latent gap, separate from this bug:** `alsa-unity` only touches `Master` and
   `PCM`; the ALC897 front headphone path (node 0x1b) stays muted at 0. Ubuntu
   leaves it off too, so it is not this bug.
5. **Reboot / fresh-login durability** of the pinned profile is untested.

---

## 15. Fleet sweep 2026-10-06, and the fix I'd actually ship

Probed every reachable box (OS, whether `51-alsa-ucm.conf` is present, PCI vs USB
cards, ELD presence, PipeWire sinks with `api.alsa.path` + profile).

| Box | OS | Audio hardware | Active sink(s) |
|---|---|---|---|
| **behoweather** | **PengyOS Desktop 26.10.8** | ALC897 + Intel HDMI on **one** HDA card | split mode → silent; now ACP `hdmi-stereo` → works |
| behopc | Ubuntu 26.04.1 | 2 cards: HDA analog + NVIDIA HDMI | `analog-stereo` (`front:0`) **and** `hdmi-stereo-extra1` (`hdmi:1,1`) |
| behotv | Ubuntu 26.04.1 | 3 PCI cards (RTX 3070) | `hdmi-stereo` (`hdmi:0`) — HDMI audio working |
| behobasement | Ubuntu 26.04.1 | 2 USB + PCI | `iec958-stereo` (USB) |
| behoserver | Ubuntu 26.04.1 | 3 PCI, no display | `iec958-stereo`, no ELD |
| miniserv | Ubuntu 26.04.1 | 2 PCI + platform | no PipeWire session |
| beholaptop / behocraptop / behothinkpad | offline | — | — |

**Only behoweather runs PengyOS Desktop.** The split-mode rule currently affects
exactly one machine, so removing it is near-zero fleet risk; it is a
product-correctness fix for future installs.

### 15a. The structural insight

Analog-vs-HDMI profile exclusivity is **not** a general ACP limitation. It exists
only when analog and digital outputs share **one** codec — laptops, N100-class
mini-PCs. On multi-card boxes (behopc, behotv, behoserver) stock ACP exposes
analog and HDMI **simultaneously as separate sinks**, no profile switching at all.
behopc is the live proof: `analog-stereo` active on the HDA card *and*
`hdmi-stereo-extra1` present on the NVIDIA card, both under stock ACP.

So split mode was solving a single-codec problem in a way that broke every case,
and the cases it claimed to help already work upstream.

### 15b. The fix I'd ship

> **Landed in the repo 2026-10-06:** the override was deleted and WirePlumber
> now inherits stock ACP (gate fast 25/25). Still to prove from a *flashed*
> image — QEMU then a stick — at both ends of the variety range (§14.2).

**Delete `buildroot-external/board/desktop/rootfs-overlay/usr/share/wireplumber/wireplumber.conf.d/51-alsa-ucm.conf`**
and inherit stock WirePlumber (`api.alsa.use-acp = true`) — what Ubuntu, Fedora
and Pop!_OS ship.

- **Cannot break laptops or analog desktops:** ACP's default profile is
  `output:analog-stereo+input:analog-stereo` (priority 6565 > hdmi 5900). Nothing
  is ever forced to HDMI; HDMI is only ever a selectable profile. Operator accepts
  switching it manually — Ubuntu requires the same.
- **Proven on two GPU vendors via the same path:** Intel i915 (behoweather) and
  NVIDIA (behotv), both `hdmi:0` + `hdmi-stereo`.
- **Keep `BR2_PACKAGE_ALSA_UCM_CONF=y`** — under ACP it becomes actually used.
  Today it is packaged for a path the image never takes.
- **Keep `pengyos-alsa-unity.service` for now.** It is a split-mode artifact, but
  it fixed real quietness on a T14 ALC257 (−20 dB). Re-measure on a laptop as a
  separate change.
- **Fix the stale comments** in `configs/layers/desktop.config` (~line 273) and
  both defconfigs (~line 631): they describe `use-acp=false` as "the UCM path",
  which is wrong — it bypasses ACP **and** UCM. The board overlay already says so.

Verification before shipping: QEMU (`scripts/run-qemu-disk.sh`) then a flashed
image, tested at both ends of the variety range — behoweather (single-codec,
HDMI-only output) and a real laptop (analog default). The laptop end is still
**inferred from behopc, not measured**.

BotTalk: `b87c92d051676183d2010000` (fleet inventory + decision),
`1e2236a2ceb0a5df1e010000` (root cause), `64753f335d02ca5d1b010000` (ledger),
`9bd0660d2cfee32d70010000` (debug recipe).

---

## 16. A *second*, unrelated "no audio" — PipeWire never started (behotv, 2026-10-08)

Do not conflate this with §0–§15. There the sound server ran and its split-mode sink
was silent. Here **there was no sound server at all**, so nothing could be measured
the usual way (`wpctl status` had no devices, COSMIC Sound showed no device profiles).

Symptom: on behotv (10.0.23.22, RTX 3070, PengyOS **Desktop NVIDIA** 26.10.15, the
live test image — not Ubuntu) HDMI audio to the TV was dead. Same box/cable/TV are
fine under Ubuntu.

First checks that localise it fast:
- `systemctl --user is-active pipewire pipewire-pulse wireplumber` → all `inactive`.
- `journalctl --user -u pipewire` → `PipeWire Multimedia Service skipped, no trigger
  condition checks were met.` and `wireplumber.service: Bound to unit pipewire.service,
  but unit isn't active.`
- The condition came from `/etc/systemd/user/pipewire*.d/10-pengyos-live-root.conf`,
  which gated root's PipeWire on `ConditionPathExists=|/dev/disk/by-label/PENGYOS-PAYLOAD`.
  That label only exists on the **installer stick**; the plain live image
  (`pengyos-desktop-x86_64.img`) has no payload partition, and its session is **root**,
  so the condition never held.

Proof it was only the guard: `setsid pipewire & setsid wireplumber &` (or a fixed
drop-in) brought up `GA104 High Definition Audio Controller … Digital Stereo (HDMI)`
with `api.alsa.use-acp = true`, and a generated 700 Hz tone reached `card0/pcm3p`
(captured on the sink monitor, peak 20000). The driver, codec, ELD (`eld#0.4`,
monitor `65S423CA`) and ACP profile were all correct.

This is the **same wrong proxy** as the login-screen defect found on the same boot
(greetd-config gated autologin on the payload label too). Fixed by using the boot:
`ConditionKernelCommandLine=|!pengyos.data` (an installed system boots with
`pengyos.data=` on the kernel line). Gotcha: `ConditionKernelCommandLine=` matches a
**whole word or a `word=value` pair**, so the argument must be `pengyos.data`, not
`pengyos.data=` (verified: `loglevel=` does *not* match `loglevel=4`, but `loglevel`
does). Repo: CHANGELOG "Fixed: the same live image had no audio"; guard
`scripts/tests/test_pipewire_live_root.py`.

Lesson (same as the greeter): a guard that requires a feature the artifact lacks
silently excludes the artifact — "the installer is available" and "this boot is live"
are different facts, and only the kernel line can tell them apart.
