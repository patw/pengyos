# TODO (temporary — delete once these land)

Everything from the 2026-09-28/29 hardware survey has landed (see
`CHANGELOG.md`), and the built images are proven on real hardware — the
bare-metal checks below are done. What is left is the NVIDIA side, boot-time
measurement, the update-publish steps, and a few leads.

> **2026-10-02 — consolidated.** This file now folds in every PengyOS idea from
> the persistent todo list (roadmap umbrella #97 and its sub-items #98–#121, plus
> the still-open follow-ups from #85 and #89), so it is the single backlog. Still
> temporary — the plan is to fold it into GitHub issues before the repo goes
> public (see *C. Polish*).

## Next-phase roadmap (consolidated 2026-10-02)

The PengyOS roadmap — desktop app features, docs, build tooling, image testing,
and release hygiene — collected from the persistent todo list. Grouped by theme;
todo-list IDs kept for traceability.

### Desktop app features (COSMIC Settings)
- [ ] **Printer section** — list connected printers, print a test page, view the
      print queue, cancel jobs. (#98)
- [ ] **Controller section** — test controllers; show which are connected over
      USB vs Bluetooth-synced. (#99)
- [ ] **RGB control** — fully custom RGB control in Settings. (#120)
- [ ] **Fan control** — fan curves and manual control in Settings. (#121)

### Desktop bugs / polish
- [x] **Wi-Fi hotspot / "share this connection"** — NetworkManager's shared
      mode runs `dnsmasq`, which the image doesn't ship (found 2026-10-03 while
      pinning NM's build-time dnsmasq path). `BR2_PACKAGE_DNSMASQ=y` landed in the
      desktop layer (2026-10-05) and both Desktop editions now build
      `/usr/sbin/dnsmasq`. Left: test a hotspot on a laptop on real hardware.
- [ ] **Boot logo bounces back to the BIOS screen** before the desktop loads —
      find the cause. (#100)

### Build tooling
- [ ] **Gate releases on a second-host build.** 26.10.3's Core and Server were
      byte-identical between behoserver and behoweather (distrobox), checked by
      hand (2026-10-03). Make it routine: a build on behoweather after each
      release (or nightly, `pengyos-nightly` phase 2) plus a compare step.
      behoweather's distrobox has no KVM, so boot checks stay on behoserver.
- [ ] **A slot that can't start the desktop should roll back by itself.**
      26.10.3's Desktop booted to multi-user, so `pengyos-update-confirm` kept
      the new slot even though cosmic-comp died at every start; behoweather
      needed a manual `pengyos-update rollback`. On Desktop editions, confirm
      only after the graphical session is up (cosmic-comp running for N
      seconds), else leave the trial unconfirmed so the next boot falls back.
      This changes update acceptance: needs Pat's sign-off.
- [x] **Core/Server smoke matrices now prove the autologin shell works**
      (2026-10-05): `autologin-shell` requires the shell prompt that follows the
      login banner and `no-login-denied` forbids the 26.10.3 `Permission denied`
      loop. The harness keeps reading the serial log for `--settle` seconds after
      readiness so the prompt is captured at all (it used to stop at `login:`,
      which is why the old check could never catch it). Re-running the matrices on
      the 26.10.7 images passes.
- [x] **`scripts/qemu-desktop-check.py` is now `pengyos-qemu-verify desktop`**
      (2026-10-05): same behaviour, but the argv, the guest environment and the
      console-report parsing are pure functions with unit tests against real
      captures plus the two mutations that matter (a session with no compositor, a
      unit the guest reports failed). `release.sh` calls the subcommand and the
      script is deleted. Re-run on both 26.10.7 Desktop sticks: 5 checks, 0 failed
      each.
- [x] **`pengyos-qemu-verify` matcher flake fixed** (2026-10-05): `network-target`
      matches across a console line that a kernel message split (systemd writes
      the status line in pieces; a printk can land between them). Every gap is
      bounded and written as `[\s\S]`; tests prove `Preparation for Network`,
      `Host and Network Name Lookups` and `Network is Online` do not satisfy it,
      and that deleting the real line still fails the check.
- [x] **26.10.7's skipped boot checks paid retroactively** (2026-10-05): the
      release ran without them (`WARNING: no writable /dev/kvm` — SVM was disabled
      in BIOS that day, so `release.sh` skipped them and the operator signed off).
      KVM is back (`grep -cw svm /proc/cpuinfo` = 32, kvm_amd loaded); all five
      images now pass the same checks `release.sh` would have run — Core 44,
      Server 50, Server NVIDIA 50 checks, both Desktops through the COSMIC session
      check with `systemctl --failed` empty. Reports: `~/build/pengyos/verify-26.10.7/`.
- [x] **Keep the shipped PengyR current.** `buildroot-external/package/pengy/pengy.mk`
      was pinned at 1.10.0 while behoweather confirmed it at runtime
      (`pengy-cli --version` → `Pengy v1.10.0` on a 26.10.7 Desktop). Now at
      **1.11.2** in 26.10.8, verified inside a booted guest.
      What a bump touches: the version in `pengy.mk`, and `pengy.hash` — the hash
      covers the cargo-**vendored** tarball Buildroot produces at download time
      (`dl/pengy/pengy-1.11.2-cargo6.tar.gz`, 76 MB), so it has to be recomputed
      from the new download, not carried over. All five editions carry it: Core
      (`pengy-cli`), Server (+`pengy-web`), Desktop (+ the Qt6/CMake `pengy` GUI
      built against the `pengy_core` staticlib). `Cargo.toml` is byte-identical
      between 1.11.1 and 1.11.2 apart from the version line, so the Qt GUI path
      does not move.
      **The loop now watches it.** `pengyos-nightly` 0.2.0 has a `pengyr` finding
      (`mk_value` on `pengy.mk` + `git ls-remote --tags` on patw/PengyR): a patch
      bump is routine, a new minor is major. It exists because pkg-stats cannot see
      an authored recipe with its own upstream. Timeline that proves the point:
      26.10.8 was built with 1.11.1 at 12:15; 1.11.2 was published at 13:38. The pin
      was not stale when it was set — it went stale *during the day*, which is the
      case a nightly observe step is for. Run against the live repo it reports
      "routine pengyr 1.11.1 → 1.11.2", and nothing once the pin is current.
      cargo-built on the host, and post-build copies the libraries the COSMIC
      Store/Player/App Library link that the image lacks, plus six for
      cosmic-comp. Flatpak is done (2026-10-02: `package/flatpak` +
      `appstream` + `libfyaml`); next, COSMIC recipes (`tools/pengyos-cosmic-*`).
      An existing build tree switching to a recipe hits "Destination … already
      exists and is not a symlink" for each library a host copy left in
      `target/usr/lib`: delete that file and rebuild (fresh trees are fine).
- [x] **THE LOOP — phase 2 (the act lane) landed 2026-10-05.**
      `tools/pengyos-nightly` 0.4.0: `plan` (deterministic, evidence-backed, records
      every refusal with the guard that caused it) and `act` (branch → edit → gate →
      dirclean/patch pre-flight → build → boot-check → sign → publish to **testing** →
      merge → record). Cron: 19:00 observe/triage/report/plan, 21:00 act. First real
      run published **26.10.8 to testing** (libxml2 fix commits); behoweather sees it.
      `update-promote` stays human-only *in code*, not just by policy. Left, in order:
      - [ ] **Phase 3, the P0 fast path** — testing machines auto-apply a P0 release,
            wait for a confirmed boot on a testing machine, then one-tap "promote?"
            through the `propose` gate. (#110, #118)
      - [ ] **Major-upgrade proposals** (new kernel series, COSMIC epoch, NVIDIA branch,
            new Buildroot series) through the `propose` gate — these are
            `denied_component` / `major_never` in the act lane today, which is correct
            but means the kernel's 2 CVE matches and gstreamer's 27 have no path at all
            yet. A kernel lane needs its own design: a kernel bump invalidates the
            NVIDIA module build and every package stamp chain in the tree.
      - [ ] **Calibrate before widening.** `max_actions = 1`, `cooldown_days = 7`,
            `lanes = [security, routine]`. Watch a couple of weeks of `plan.json` +
            `status` before raising the cap or adding lanes.
      - [ ] **`pengyos-release verify` needs the site's signed index.** It reported
            `missing signed index: <site>/update/index.json` while the feed itself
            (feed.xml + per-release `manifest.json` + `.sig`) is published and
            behoweather consumes it. Either `release.sh` writes that index or `verify`
            stops requiring it — decide which, then make the act lane run `verify` as a
            gate on its own output.
      - [ ] **Patch shape on a package with a stock patch set needs a real build
            proof, per package.** podman's upstream commit applied to
            `libpod/container_internal_common.go` and failed on
            `test/e2e/run_working_dir_test.go`; the `<pkg>-patch` pre-flight now stops
            it before any build, but the loop has no way to *adapt* a patch (strip the
            test hunks) and should not invent one. Report those CVEs until a human
            decides.
      - [ ] **Second-host build gate** (see *Build tooling*): an act-published release
            is built on one host only; the reproducibility claim wants behoweather.
- [x] **SSH-based checks in `pengyos-qemu-verify`** (#114) — landed 2026-10-05
      (tool 0.5.0): `boot --ssh` runs the default in-guest checks
      (`systemctl --failed`, `sshd` active) plus any `--ssh-command`, between
      readiness and teardown, and reports them as ordinary checks. Images take a
      key at build time (`PENGYOS_AUTHORIZED_KEYS_FILE`); the hostfwd into port 22
      is added for you. Left: the same checks as matrix entries
      (`kind = "command"` + a per-variant `ssh = true`), which changes grounded
      matrix data and lands on its own.

### Image capabilities
- [ ] **Dynamic login MOTD (Server, Core; after 26.10.3)** — the useful part of
      Ubuntu's SSH welcome: `Welcome to PengyOS Server <version>`, the docs URL,
      load / memory / disk (`/` and `data`) / IPs / uptime, update state from
      `pengyos-update status` (newer image on the feed, applied and waiting for
      a reboot, last update rolled back — no network at login), and failed units
      only when there are any. Upstream `pam_motd` doesn't run scripts (that's
      an Ubuntu patch) but reads `/run/motd.d/`: a service + timer writes a
      fragment there (and after each update check). Core's sshd has
      `UsePAM no` + `PrintMotd` (reads only `/etc/motd`): enable PAM or point
      `/etc/motd` at the generated file. Check what `pengyos-update status`
      exposes first. (2026-10-03)
- [ ] **VM host capability** in the desktop/server image. (#108)
- [ ] **SecureBoot + TPM** — deferred with the decision written down; see
      *Secure Boot — deferred* near the end of this file. (#112)
- [ ] **Remote desktop (VNC).** (#115)
- [ ] **Custom Flathub repo** + the ability to push to it. (#113)

### Feedback loop
- [ ] **PengyOS Feedback app** — submit text + image feedback, bundled with a
      hardware dump. (#116)
- [ ] **Feedback REST API** on miniserv — moofile-backed, with anti-spam. (#117)
- [ ] **Idea harvester** — periodically pull good ideas from other distros,
      queued for approval. (#119)

### Pengy integration
- [ ] **Pre-configured Pengy** — ship a `.skills/` directory in the user's home,
      pre-populated with skills about Pengy itself, about PengyOS (how to do
      things with it), a default `user_profile` skill that asks the user who they
      are and self-updates with the answer, plus basics like charts and PDFs.
      (new idea, 2026-10-02)

### Release hygiene
- [ ] **Test the NVIDIA image boot** — see *Still to check on metal (NVIDIA)*. (#101)
- [ ] **Put the project on GitHub** — audit the git history, clean out anything
      sketchy; see *Before the repo goes public*. (#105) The audit is written up
      in `docs/github-publish-audit.md` (2026-10-05): no secrets, no large blobs,
      97 machine-name mentions, one personal email in a `pyproject.toml`, 310
      commits authored as the personal address, no remote configured. Left: the
      four operator decisions at the end of that file, then the push.

## Before the repo goes public (planned 2026-10-02)

Goal: a clean, professional GitHub repo (github.com/patw/pengyos). Done
2026-10-03 and shipped in 26.10.3: one project URL (`pengyos.catbee.ca`),
PengyOS scripts out of `/usr/local/bin`, the parked UKI/sysupdate tools
retired, the duplicate `buildroot-external/targets.toml` dropped,
xdg-desktop-portal's subprojects as hash-checked tarballs, `SECURITY.md`, CI
(the full gate on every push), GitHub links pointed at patw/pengyos, and the
generated tool-README opener trimmed. The website docs (VM install, Pengy,
llama.cpp, "How PengyOS was built", the build guide) are live. Left:

- [ ] Remove `pengyos-install plan/install` (the parked UKI installer path).
      It ships in every installer, so test an install in QEMU afterwards.
- [ ] Decide what to do with this TODO.md itself (fold into issues).

## Bare-metal checks — done (2026-10-01)

Operator booted the built images on the fleet: **Desktop** on beholaptop (AMD
Ryzen 7 6800H + Radeon 680M), behocraptop (Intel i3-1215U), behothinkpad (Intel
i7-7600U), behobasement and behoweather (Intel N100); **Server** on behocraptop.
Every non-NVIDIA check behaved; nothing further to do.

| Check | Where | Result |
|---|---|---|
| Boot splash handoff (simpledrm -> i915/amdgpu) | Intel boxes + beholaptop (AMD) | ✅ |
| I2C touchpads (DesignWare + pinctrl were missing) | behocraptop, behothinkpad, beholaptop | ✅ |
| AMD ACP digital mic | beholaptop | ✅ |
| Wi-Fi firmware (8265 / rtw88) | behothinkpad, behocraptop, behobasement | ✅ |
| Sensors / EDAC / thermal | Intel boxes | ✅ |
| Logitech Unifying / PlayStation pads | any | ✅ |
| Login screen + auto-login | behoweather | ✅ |
| Intel SOF audio | behocraptop, behothinkpad | ✅ |
| ipp-usb (driverless USB printer/scanner) | any | ✅ |
| Bluetooth codecs (AAC / aptX / LDAC) | any | ✅ |
| Steam controllers | a gaming box | ✅ |

## Still to check on metal (NVIDIA)

beholaptop ran the **plain Desktop image**, so the NVIDIA rows are still open.

2026-10-04, first metal boot of Desktop NVIDIA (live stick, ASUS PRIME
Z790-P + RTX 3060 Ti, two 1440p monitors): on 26.10.6 the driver never loaded,
because the Desktop board's modprobe drop-in stubbed out `nvidia*`. On the
26.10.7 build `nvidia-smi` works, COSMIC runs on nvidia-drm on both monitors,
and 144/180 Hz modes switch fine. Still open: a CUDA job, Vulkan/Steam, the
laptop PRIME rows and behoserver.

| Check | Where | What to look at |
|---|---|---|
| NVIDIA edition (`nvidia-open` 595.104.02) | behoserver (2× RTX 5060 Ti), behopc, behotv, beholaptop | `nvidia-smi`; COSMIC on the dGPU; a CUDA job; laptop PRIME (`prime-run`) |
| NVIDIA Vulkan + X11 GL (2026-09-30) | behopc, behotv, beholaptop | `vulkaninfo --summary` in a Flatpak or `WGPU_BACKEND=vulkan cosmic-monitor`; COSMIC apps on the GPU, not lavapipe; Steam (Flatpak + GL extension 595.104.02) |
| Discrete-GPU launch | beholaptop | right-click an app → "Launch using discrete graphics" (switcheroo-control) |
| nvidia-drm splash handoff | an NVIDIA box | Pengy stays up across simpledrm -> nvidia-drm, then COSMIC with no black gap |
| Sensors / EDAC / thermal on the AMD server | behoserver | `ls /sys/class/hwmon/*/name` (k10temp, nct6775), `ls /sys/devices/system/edac/mc` |

## Boot time — measure before changing

**Measured 2026-10-04 (ASUS PRIME Z790-P, Desktop NVIDIA live stick):** the
kernel took 15 s before simpledrm; Pengy showed at 20.4 s for ~2.5 s. Of that,
~6 s was a failing built-in `mei_me` (now a module, 26.10.7), and ~5 s was
every kernel message going out at 115200 baud: the board has a COM header, so
GRUB's UART check adds `console=ttyS0` (now with `loglevel=4` off QEMU,
26.10.7). On 26.10.7 there: kernel 6.3 s, Plymouth (now `UseSimpledrm=1`) on
simpledrm at 8.8 s, blanked at 9.3 s by nvidia-drm, back at ~11.4 s, quit at
13.9 s, so two short flashes after ~9 s of black. A steady early splash needs
an initramfs with Plymouth + the GPU modules (early KMS, as Fedora/Pop do).
Check the simpledrm -> i915/amdgpu handoff on an Intel/AMD box with
`UseSimpledrm=1`.

`systemd-analyze` is in the image now. In QEMU the session is up ~4.9 s after
the kernel starts; on real hardware run `systemd-analyze blame` and
`critical-chain greetd.service` first. Suspects:

- `greetd.service` (was `cosmic-session.service`) pulls in the deprecated
  `systemd-udev-settle.service` and its `wait-for-seat.conf` also runs
  `udevadm settle --timeout=20` + `sleep 1`
  — both are workarounds for real N100 races (see the drop-in), so replace them
  only with measurements in hand.

## Updates — next steps

- Publish every edition with each release: `update-publish` per A/B edition
  (Desktop, Desktop NVIDIA, Server) and one `download-publish` with all four.
- COSMIC notifications open the app on click but show no action buttons, so
  "remind me later" lives in the app.
- A broken GRUB from `update.d/50-grub-efi` would stop both slots booting
  (firmware only runs EFI/BOOT/BOOTX64.EFI; the old one is kept as
  bootx64.efi.prev). Consider a second firmware boot entry for the .prev copy.
- Core does not use the A/B layout yet, so it cannot update.
- `/etc` divergence: an upper-layer file silently shadows newer image versions;
  `pengyos-etc-diff` (docs/state-layout.md) would let `apply` warn about it.
- **Downgrade floor — design (2026-10-05), before any code.** The hole is not the
  feed: `pengyos-update` already refuses a downgrade when choosing from the feed
  (`src/pengyos_update/cli.py:325`). The hole is that the *slot choice* comes from
  `grubenv` on a plaintext ESP, and `SECURITY.md` already admits stale updates are
  not detected. Proposal, in two rungs so the cheap one lands first:
  1. **Detect.** Keep a monotonic floor on `data` (the highest image version ever
     *confirmed*), written by update-confirm. Add `pengyos-update floor` and a
     check that reports when the booted slot's version is below the floor. The
     verifier and `pengyos-fleet-inventory` read it the way they read everything
     else. Detection changes nothing and needs no sign-off.
  2. **Refuse.** Make confirm/apply treat "below the floor" as a failure rather
     than a choice. That changes update acceptance, so it goes through the propose
     gate with your sign-off, and it needs a deliberate answer to the recovery
     case: a floor that cannot be lowered is also a floor that cannot recover from
     a bad image except by reinstall. Say which escape hatch exists (an explicit
     `--allow-downgrade` that logs loudly) before writing the refusal.
  Secure Boot fixes neither of these, which is why it is not the answer here.

## Secure Boot — deferred, with the decision written down (2026-10-04)

Lower priority than everything above. Measured on the built 26.10 images
(behoserver, `~/build/pengyos/output/pengyos-desktop`): `sbverify --list` reports
"No signature table present" on both `EFI/BOOT/bootx64.efi` and `/bzImage`; the
kernel has `CONFIG_EFI_STUB=y` + `CONFIG_EFI_MIXED=y` (the bzImage is already a
signable PE) but `# CONFIG_MODULE_SIG is not set`,
`# CONFIG_SECURITY_LOCKDOWN_LSM is not set` and
`# CONFIG_SIGNED_PE_FILE_VERIFICATION is not set`, while the Desktop rootfs ships
395 `.ko` (399 in Desktop NVIDIA). `pengyos-secureboot-keys` and
`pengyos-secureboot-enroll` are green but mock-grade: `sign-chain` signs a
`signatures.json` sidecar with a stdlib Ed25519 (`signer.py:StdlibSigner`) and
`lint` checks that sidecar, not the binary; `keys.py` emits three self-signed
EC P-256 certs with no `codeSigning` EKU and no PK→KEK→db chain, so `db`
rotation is not possible today. No commit in history ever used `sbkeysync`, and
every QEMU fixture prints `Secure boot disabled` — #112's "it used to work" is
the swtpm measured-boot harness, not a signed chain.

**Decision (SPEC §6 #2, answered 2026-10-04): not now.** Secure Boot covers one
threat: someone with physical access editing the ESP while the box is off. The
update path is already covered by ed25519 manifests, hash-checks on every byte
and auto-rollback, and the fleet is eleven machines under one person's roof. It
is a lock, not an alarm, and the alarm half (the TPM) works with Secure Boot off.

Triggers that would reopen it: a PengyOS machine that leaves physical control
(travels, is given away, is colocated), a requirement from someone, or wanting
the claim on the tin. If it reopens, do it as the UKI + systemd-boot design, not
signed GRUB — the UKI also closes the `grubenv` downgrade hole, and the recipe is
already in git history
(`git show 6cd095c5^:tools/pengyos-bootchain/pengyos_bootchain/uki.py`).

The parts worth doing anyway, in order:

- [ ] **Kernel lockdown + module signing** — no ceremony, real value, and a
      prerequisite for any later signing. Add to a kernel fragment:
      `SECURITY_LOCKDOWN_LSM`, `MODULE_SIG`, `MODULE_SIG_ALL`,
      `MODULE_SIG_FORCE`, `MODULE_SIG_SHA256`, `SIGNED_PE_FILE_VERIFICATION`.
      Today Tier-0 immutability is enforced by mount options and file permissions
      only: root can build a module, drop it on `data` (which updates never
      touch) and `insmod` it, surviving every future image update. Test first in
      QEMU with `CONFIG_LOCK_DOWN_KERNEL_FORCE_INTEGRITY=y`, which shows what
      breaks without touching any firmware. Pin the signing key —
      `CONFIG_MODULE_SIG_KEY` defaults to a `certs/signing_key.pem` generated at
      build time, which breaks `pengyos-repro` byte-identity. Check that
      `regulatory.db.p7s` still loads once the keyring is populated
      (`CFG80211_REQUIRE_SIGNED_REGDB=y` is already on), and that `CONFIG_KEXEC=y`
      is acceptable under lockdown.
- [ ] **Measured boot as the alarm** — no ceremony. A PCR7 report per boot into
      `pengyos-fleet-inventory`, checked by the verifier the way it checks
      everything else. `pengyos-measured-boot` exists; what is missing is a policy
      and a place to look. Works with Secure Boot off, needs no key custody.
- [ ] **A downgrade floor instead of signatures.** The A/B slot choice comes from
      `grubenv` on a plaintext ESP, and `SECURITY.md` already admits stale updates
      are not detected. Secure Boot fixes neither: an older *signed* image is
      still valid. A version floor in `pengyos-update` is the cheaper answer.
- [ ] **Say the real thing on the download page.** `downloads.py:179` lists
      "Secure Boot turned off" as a checklist item; it is a compatibility
      requirement — unsigned images will not load on firmware that insists on
      signatures. Worth stating what that costs the tester too: `dbx` is ignored
      entirely while Secure Boot is off (June 2026 sweep), and some testers cannot
      turn it off — Windows 11 dual-boot with Vanguard-class anti-cheat needs
      Secure Boot + TPM + VBS/HVCI on the Windows side, BitLocker machines can ask
      for a recovery key when the state changes, and managed or Surface-class
      firmware sometimes has no off switch. Desktop ships Steam, so this is not
      hypothetical.
- [ ] **Make the unsigned failure legible.** Under a VM the symptom is a static
      98%-black screenshot with no message; on real firmware it is usually a
      firmware error or a silent fall back to the old boot entry. Neither says
      what happened.

Not planned: the Microsoft-signed shim path for public images (an EV code-signing
cert, Microsoft's shim review, and a third-party binary in the boot chain, bought
only for the convenience of booting without touching firmware). Note for later:
`update.d/50-grub-efi` copies `bootx64.efi` from the new image onto the shared
boot partition — if signing ever lands, that step becomes security-critical and
must carry signed binaries.

## Leads

- **Why the nightly looked dead (2026-10-05).** The loop was running fine — cron
  fired, observe reached pkg-stats, the NVD clone refreshed — but a development run
  on 2026-10-03 used `--no-email`, and the old code marked everything as reported in
  that same branch. 184 CVE matches and 7 findings were consumed without anyone
  being told, so every cron run since correctly said "nothing new". Fixed in
  `pengyos-nightly` 0.3.0 (`--no-email` no longer consumes the queue; `--mark-reported`
  is the explicit opt-in). The first digest the operator actually received was
  today's: 67 P0/P1 open of 184 tracked, 5 upgrades tracked.
- **Quiet stays silent — decided 2026-10-05.** A night with nothing new sends
  nothing; the Monday digest is the heartbeat. To keep "no email" from being
  ambiguous again, `pengyos-nightly status` is now the way to ask whether the loop is
  alive: read-only, no email, exit 1 when the last observation is >30 h old, a source
  failed, or CVE matches are triaged but never reported. If you ever want an alert
  without a daily email, the hook is there — a cron line that runs `status` and
  notifies on a non-zero exit.
- **PengyR 1.11.2 adds settings PengyOS does not set.** Its recovery behaviour is
  configurable (recovery/output-token budgets), and PengyOS ships **no** PengyR
  config file at all — the only `/etc/pengyos` files are `backup.conf`,
  `monitor.conf`, `update.conf`. So every image runs PengyR on upstream defaults.
  Worth a decision rather than an accident: on a 16 GB Desktop with a long-lived
  chat, are upstream's defaults the ones we want, and would a shipped
  `/etc/pengyos/pengy.conf` (or a documented per-user one) be the right place?

- **The Desktop session screenshot is 44% guest.** `pengyos-qemu-verify desktop`
  runs Xvfb at 1920x1200 and captures the whole screen, but the guest renders
  **1280x800 anchored at (640, 360)** — measured from the content bounding box of
  the saved PNGs (26.10.7 and 26.10.8 both). So 56% of every session screenshot is
  black padding, and the guest is flush against the frame's right and bottom
  edges. Two consequences: a "is the screen black?" style check is misleading, and
  any pixel comparison between runs must crop to the guest region first. Related to
  the known virtio-gpu preferred-mode problem.
- **The COSMIC panel's left item is clipped at 1280x800.** In both the 26.10.7 and
  26.10.8 session captures, the panel's leftmost element (the COSMIC logo, before
  "Workspaces") is cut by the screen edge. Pre-existing, not caused by the epoch
  move — the two captures differ in 131 pixels out of 1,024,000, all inside a
  28x10 box at (635,11), which is the clock text.

- **The Desktop NVIDIA target writes its images under the Desktop names.**
  `output/pengyos-desktop-nvidia/images/` holds
  `pengyos-desktop-x86_64.img` and `pengyos-desktop-x86_64-installer.img`, not
  `pengyos-desktop-nvidia-*`. Harmless while each edition's output stays in its own
  directory, but any script that globs by edition name will not find the NVIDIA
  stick (it cost a failed `pengyos-qemu-verify desktop` run on 2026-10-05).
- **`pengyos-update status` needs the installed A/B layout.** Run against a raw
  disk image in QEMU it exits non-zero with "partition 3 of /dev/vda not found —
  this disk does not have the PengyOS A/B layout" (seen 2026-10-05 through the new
  `--ssh` lane). Anything that reads update state at login (the dynamic MOTD) has
  to handle that case, not just the "no network at login" one.
- **behoweather's ssh host key changed** when it was installed with PengyOS, which
  silently broke every automated check against it until it was re-enrolled
  (2026-10-05). Every box reinstalled with a PengyOS image regenerates its key:
  fleet checks against them need the key refreshed.

- **pengy-agent AppArmor follow-ups** (`tools/pengyos-apparmor`, not yet in an
  image): pin the local model server's port in the agent's network grant once
  PengyOS defines it (`port-pin-status` / `port-pin-apply`), and pin the
  agent's workspace file-write surface. Remove this item when done.
- **"Workspaces" vanished from the top panel** on behoweather's first boot
  after install, back after a reboot. Its applet did not crash (it still
  logged at 77-83 s, after Firefox's install at 53 s); cause unknown. If it
  recurs: screenshot + `journalctl -b --user` and cosmic-panel's lines.
- cosmic-files-applet (the desktop) panicked once at startup in tiny-skia
  ("Create pixel map": a zero-sized surface) and was restarted within 10 ms by
  cosmic-session; upstream bug, no visible effect.
- **COSMIC epoch-1.9: decided, in progress.** Chosen 2026-10-05: pin one libcosmic
  first, then move the whole set, into **26.10.8**. `docs/cosmic-epoch-1.9.md` has
  the survey and the reasoning. The facts that changed the plan:
  - The tag is not the pin that matters. Each component's own `Cargo.lock` carried
    its own libcosmic rev — **14 distinct commits** across the image (2025-10-13 to
    2026-09-21). Moving to epoch-1.9 would give 13. So the epoch move alone does
    not cohere anything; the libcosmic pin is the work.
  - An `epoch-*` tag is a Pop *release snapshot*: 10 of our 26 tagged rows already
    point at the commit epoch-1.9.0 tags (idle and randr unchanged since
    epoch-1.0.10). Only **16 rows carry new code**.
  - Six components ship no `Cargo.lock` at all (session, randr, screenshot,
    pop-launcher, icons, sound-theme) — resolution happens on the build host.
    cosmic-session, the session orchestrator, is one of them.
  - The battery patch is *not* proven obsolete: at the 1.9 applets commit the
    applet code is unchanged and the patch still applies. Keep it, re-test on
    hardware, delete it then.
  - `tools/pengyos-cosmic-recipes/cosmic.toml` (22 components at epoch-1.8.0 SHAs)
    has to move too, or the shipping path and the recipes lane diverge.
- COSMIC Settings has no automatic-login switch (upstream);
  `pengyos-autologin` is the way for now.
- cosmic-session can't put apps in systemd scopes ("Failed to spawn scope"),
  and leaves XDG autostart to a `cosmic-session.target` we don't install: the
  session bus comes from dbus-run-session, not the systemd user bus.
  `session-autostart` covers autostart (26.09.4); moving the session onto the
  user bus (Pop's layout) would fix both properly.
- `xrdb` isn't in the image: cosmic-comp can't pass the cursor theme to X11
  apps through Xresources (they still get the default theme).
- **Not yet in the Desktop** (checked against Pop!_OS/Fedora, 2026-09-30):
  input methods (ibus/fcitx5) for typing CJK; a screen reader (orca +
  speech-dispatcher); fingerprint login (cosmic-greeter doesn't use fprintd
  yet); geoclue and iio-sensor-proxy (COSMIC doesn't use them yet).
- `pengyos-build configure` missed that gstreamer1 was built before
  gobject-introspection was enabled (gst1-plugins-base then failed on a missing
  `Gst-1.0.gir`); fixed by hand with `gstreamer1-dirclean`.
- glibc's parallel `make install` can race on `link-defines.h.d` (seen once on
  a `glibc-reinstall`); a retry succeeds.
- `oo7` is on 0.7.0-beta (0.6.0 can't create the keyring without a prompter);
  move to 0.7.0 when released.
- *(candidate)* `CONFIG_IT87_WDT` for behorouter — confirm its Super-I/O first.
- *(candidate)* `CONFIG_I2C_I801_MUX` on Intel boards.
- COSMIC's Bluetooth applet asks D-Bus to activate `org.bluez` every 25 s on a
  machine with no adapter (`bluetooth.service` is correctly skipped); log noise
  only.
- Lock screen on the live stick: the session there is root, so unlocking needs
  root's password (the placeholder in core.config). Consider no idle lock live.
- `xdg-utils` and `cutecosmic` patches need a `Signed-off-by:` if they ever go upstream.
- `cutecosmic` uses private Qt APIs: `pengyos-build`'s stale detection keys on
  options, not versions — after a Qt version bump, `make cutecosmic-dirclean`.
- **COSMIC file-open dialogs (todo #85):** `xdg-desktop-portal-cosmic` panics
  (exit 101) when D-Bus-activated because the session-bus activation env has no
  `WAYLAND_DISPLAY`. Root: cosmic-comp's own "import `WAYLAND_DISPLAY` into
  systemd" fails (Operation not permitted / i/o error), and there are two session
  buses — a vestigial `dbus-run-session` private bus (0 connections) and the
  systemd user bus actually in use. An `ExecStartPost` hook was tried and
  **reverted**: in QEMU the session died at ~32 s and cosmic-comp never started.
  Fix properly — decouple into a separate oneshot unit that runs *after* the
  session, or fix cosmic-comp's import, or drop the vestigial `dbus-run-session`.
  Verify on real hardware.
- **Kernel coverage gaps — fleet hardware survey (todo #89, 2026-09-29):** all 10
  machines probed. CONFIG adds: `SPI_INTEL_SPI_PCI` + `MTD_SPI_NOR` (Intel
  SPI-NOR); `INTEL_VSEC`; `AMD64_EDAC` + `EDAC_IGEN6` (ADL-N);
  `SND_SOC_AMD_ACP3X` (Cezanne) + `SND_SOC_AMD_ACP6X` (Rembrandt/beholaptop);
  `INT340X_THERMAL` (ADL-P) + `INTEL_PCH_THERMAL` (Kaby Lake);
  `INTEL_POWERCLAMP` + `INTEL_UNCORE_FREQ_CONTROL`; re-verify
  k10temp/nct6775/i2c_piix4/ccp from the earlier behoserver pass. FIRMWARE:
  `BR2_PACKAGE_LINUX_FIRMWARE_IWLWIFI_8265` and the **rtw88** files are confirmed
  present in 26.10.7 (checked on behoweather over ssh, 2026-10-05:
  `/lib/firmware/rtw88/` carries all 11 blobs including `rtw8821c_fw.bin`, and
  `iwlwifi-8265-34/-36.ucode` are there), so the "rtw88 gap" from the 2026-09-29
  survey is closed. Re-check on the RTL8821CE boxes themselves: behocraptop was
  unreachable and behobasement is back on Ubuntu, not PengyOS.
