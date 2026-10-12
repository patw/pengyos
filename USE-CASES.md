# PengyOS — Use Cases & User Stories

> `SPEC.md` says **what we build**. This file says **for whom, and to what end**.
>
> **The governing rule:** when a spec decision and a user story disagree, *the story
> wins* — stop, flag the conflict, and fix the spec. The spec is our best guess at how to
> serve these stories; the stories are the reason the spec exists.
>
> **How to use this (especially if you are the agent building something):**
> before you build or change anything, name the story it serves and the acceptance
> criterion it must satisfy. Stories are not decoration — **their acceptance criteria
> become our tests.** If you can't point at a story, you probably shouldn't build it yet.

**Story IDs:** `US-<EDITION>-<n>` — e.g. `US-SRV-3`. Acceptance criteria are written to
be testable where the technology allows.

> **Spec catch-up needed (2026-09-29).** These stories were added from the fleet as it is
> actually used and are **ahead of `SPEC.md`**: US-CORE-7, US-SRV-8…11, US-DSK-8…15,
> US-DEV-4, US-ALL-1, US-ALL-2, US-AUG-2 and the "friend" stakeholder. Decisions taken
> with them: personal tools (Syncthing, rclone, …) stay **out of the image** (US-ALL-1);
> a **small, closed set of `systemd-sysext` extensions** we build and sign in lockstep
> with the image carries optional system-level pieces, NVIDIA first (US-ALL-2); Caddy is
> the preferred web front door (US-SRV-8). SPEC §2.5 (lanes) and the edition matrix need
> updating to describe these.

---

## Cross-cutting stakeholders

### The augmented human (curator / maintainer)
- **Who:** Pat — owns the hardware, the repos, the home lab. Not a consumer of PengyOS;
  its *author and curator*.
- **Motivation:** build and keep a computing environment he fully understands and
  controls; make a bespoke OS *sustainable* by pairing his judgement with an agent.
- **Tasks:** choose components, set defaults and taste, ratify changes that cross the
  security boundary, direct the agent, fix the fleet.
- **How:** the repo is the memory; the agent does the legwork; the human signs the edge.
- **What good looks like:** `US-AUG-1` below.

### The maintenance agent (Pengy) — a stakeholder in its own right
The system will be maintained *by* an agent, so the OS must be made *legible* to it.
This is a real design requirement, not an afterthought.
- **Motivation:** keep the system correct and current without breaking it; leave an
  honest trail; be able to tell when it is wrong.
- **Needs:** machine-readable system state; deterministic, idempotent interfaces;
  an append-only attest/mutation log; reversible operations (`snapshot`/`revert`);
  a definition of *done* it can verify by running something.
- **What good looks like:** `US-AGT-1` below.

### The friend trying PengyOS
- **Who:** someone Pat hands a stick to — technical enough to install an OS, not a
  PengyOS insider. Doesn't know what Syncthing, rclone or a sysext is, and shouldn't
  have to.
- **Motivation:** a machine that works well out of the box and lets them use *their*
  habits (their sync, their backups, their apps), not Pat's.
- **What good looks like:** `US-ALL-1` below — the image carries what everyone needs;
  Pat's personal tool choices are one install away, never baked in.

---

## 1. PengyOS **Core** — “the node”

**The story of this edition:** a tiny, headless, unattended machine that must run for
years on cheap hardware and be fixable from a distance.

### Personas

- **The node builder** — deploys small boxes (N100-class, ~8 GB): a sensor gateway, a
  firewall/router, a kiosk's silent heart, a remote-site box, a build node. Wants minimum
  footprint and maximum reliability.
- **The far-end admin** (often Pengy itself) — must reach and repair a box with no screen,
  sometimes no keyboard, from a continent away.

### Stories

**US-CORE-1 — Install and first boot.**
> *As the node builder, I want to flash an image and get a working console in under a
> minute, so that deploying a node is boring.*

How: write the image to USB/NVMe; boot; arrive at a login on the serial console (or
attached keyboard) with the network up via DHCP.
**Acceptance:** image boots on the reference N100 under UEFI; reaches a login prompt in
< 60 s; idle RAM use is small and documented; NIC is up with no manual config.

**US-CORE-2 — Reach it from anywhere.**
> *As the far-end admin, I want to reach a headless node without a screen, so I can fix
> it from home.*

How: minimal `sshd` over the network, or a serial/console fallback; host keys generated
on first boot.
**Acceptance:** `ssh` login works after first boot; console access works with no network;
the box is reachable again after a reboot with no human present.

**US-CORE-3 — Run one job, unattended, forever.**
> *As the node builder, I want to run a single service unattended, so the node does its
> one job and nothing else.*

How: one `systemd` unit; no desktop; no surprises.
**Acceptance:** the unit starts on boot; survives reboot and hard power loss; consumes
only the expected resources; logging is local and bounded.

**US-CORE-4 — Update safely and roll back.**
> *As the far-end admin, I want to apply an update and be able to undo it, so a bad
> update never means a truck roll.*

How: atomic update to a versioned base; boot-counting auto-fallback; snapshot rollback.
**Acceptance:** a failed boot auto-reverts to the previous good entry; an update can be
reverted deliberately; the node is never left unbootable by a bad update.

**US-CORE-5 — Fit *this* machine.**
> *As the node builder, I want the system tailored to the hardware, so it stays small and
> fast.*

How: a generated, version-controlled hardware profile; only the needed kernel modules
and services.
**Acceptance:** the profile exists in the repo, is regenerated on hardware change, and the
resulting image includes no unused drivers/services for the target machine.

**US-CORE-6 — Rebuild it and verify the rebuild.**
> *As the curator, I want to rebuild the exact image and check it, so I know the node is
> what I think it is.*

How: one documented command; reproducible build; SBOM + signature out the other end.
**Acceptance:** rebuilding from the same inputs produces a matching artifact (or a fully
explained difference); an SBOM and signature are emitted; measured boot verifies.

**US-CORE-7 — Take jobs from a remote agent.**
> *As the far-end admin, I want a remote Pengy to push binaries and scripts to a node and
> schedule them, so a mini PC somewhere just does what it is told.*

How: the node runs Core (sshd, Python, `pengy-cli`). Pushed binaries land in writable
places — `~/.local/bin` or `/var/lib/<app>` — because `/usr` (incl. `/usr/local`, `/opt`)
is the read-only image. Scheduling is **systemd timers**, not cron (Core ships no cron
daemon): `systemd-run --user --on-calendar=…` or a user unit; the user is lingered so
jobs run with nobody logged in.
**Acceptance:** from another machine, a pushed binary runs on the node; a job scheduled
by the remote agent fires on time, survives reboot and an image update, and its output
is in the journal; removing it is deleting the unit and the file.

---

## 2. PengyOS **Server** — “the self-hoster”

**The story of this edition:** the home-lab workhorse. Comfortable to administer
remotely, capable enough to self-host the services a household actually relies on.

### Personas

- **The self-hoster** (Pat) — runs web, DNS, VPN, media, databases, backups at home.
  Values freedom, reliability, and not depending on anyone's cloud.
- **The remote admin** — works over SSH for long sessions; wants a *comfortable*
  terminal environment, not a punishing one.
- **The developer** — builds software on the server (it is the natural build
  host, and where US-DEV-3's self-hosting runs). The developer stories
  US-DEV-1/2 (under Desktop) apply here too: the Server layer carries Python +
  pip, zsh, git, curl and the distrobox compiler lane, and Desktop inherits them.

### Stories

**US-SRV-1 — Be productive over SSH in minutes.**
> *As the remote admin, I want a comfortable remote shell, so long sessions aren't
> painful.*

How: `mosh`/`ssh` + `tmux`, a familiar shell, and a modern CLI set (ripgrep, fd, bat,
eza, fzf, zoxide, starship, jq) wired through the comfort profile
(`/etc/pengyos/shell/profile`, sourced by `/etc/zshrc`, `/etc/profile.d` and the
`/etc/skel` rc files) plus the zsh plugins (autosuggestions, syntax highlighting).
**Acceptance:** a fresh login has the expected tools; a session survives disconnection
(`tmux`); scrollback/search are pleasant by default.

**US-SRV-2 — Self-host the services.**
> *As the self-hoster, I want to run my services in containers, so I can move fast without
> polluting the host.*

How: rootless `podman` (+ buildah/skopeo); optional nginx/Caddy, Postgres/Valkey.
**Acceptance:** a container service starts on boot, survives reboot, is reachable on the
LAN, and its data persists across updates.

**US-SRV-3 — Keep it running, and know when it isn't.**
> *As the self-hoster, I want monitoring and alerts, so I hear about problems before they
> bite.*

How: TheWatcher metrics + node_exporter; ntfy/email alerts.
**Acceptance:** metrics are exported; a deliberately induced failure produces an alert.

**US-SRV-4 — Back up, and *prove* the restore.**
> *As the self-hoster, I want tested backups, so recovery isn't a hope.*

How: restic/rustic to local + remote; scheduled; restore-tested.
**Acceptance:** a scheduled backup runs; a documented restore to a clean VM succeeds;
backup failures raise an alert.

**US-SRV-5 — Secure remote access.**
> *As the self-hoster, I want a hardened, reachable-from-outside server, so self-hosting
> isn't reckless.*

How: `nftables` rules; WireGuard for remote access; fail2ban/sshguard.
**Acceptance:** only intended ports are exposed; the WireGuard tunnel works; brute-force
attempts are mitigated.

**US-SRV-6 — Let the agent do the triage.**
> *As the curator, I want the agent to watch upstream changes and propose what matters, so
> maintaining the stack doesn't consume my life.*

How: upstream changelogs + advisories → AI triage (security/relevant/ignore) → propose →
sandbox build/test → witness → human approves the boundary.
**Acceptance:** a real (or simulated) CVE against a pinned package produces a proposal
with justification and a passing sandbox test; nothing ships without human approval.

**US-SRV-7 — Run the weird thing.**
> *As the remote admin, I want to run a service that only ships as a deb/rpm, so I'm not
> blocked by packaging.*

How: Lane 4 — a guest distro (Debian/Ubuntu) in a `podman`/`distrobox` container; install
there, the vendor's intended way.
**Acceptance:** the service runs in the guest, is reachable, and leaves the host
filesystem untouched; its state is declared and reproducible.

**US-SRV-8 — Serve my sites with automatic HTTPS.**
> *As the self-hoster, I want to put sites on the internet with valid certificates and
> little config, so hosting is not a chore.*

How: **Caddy** is the preferred front door (single binary, ACME built in — no certbot);
nginx remains possible. It runs either as a container with host networking or as the
optional web-server **extension** (US-ALL-2) when it must be a root service on 80/443.
Site config in `/etc/caddy` (the persistent `/etc` overlay), state in `/var/lib/caddy`;
the firewall opens 80/443 for it.
**Acceptance:** a new site with a public name gets a valid certificate and renews it
unattended; a reverse-proxied app is reachable over HTTPS; everything survives an image
update.

**US-SRV-9 — Run the media stack headless.**
> *As the self-hoster, I want Jellyfin and the *arr apps on a headless box with an
> external drive, so the household's media just works.*

How: Server edition. Jellyfin, Sonarr, Radarr, Jackett (and anything already on Docker)
run as **podman containers managed by systemd (quadlets)** — started at boot, restarted on
failure, updated independently of the OS; podman's Docker-compatible socket covers
compose files. Jellyfin gets `/dev/dri` for hardware transcoding. The external drive is a
systemd mount unit (or `/etc/fstab` entry) under `/data` or `/srv`.
**Acceptance:** after a reboot with nobody logged in, all services are up and reachable
on the LAN; transcoding uses the GPU; the media drive is mounted before the containers
start; an image update leaves config, data and containers intact.

**US-SRV-10 — Run many small Python apps and sites.**
> *As the self-hoster, I want to run dozens of my own Python apps side by side, so each
> stays isolated and restartable.*

How: one directory per app under `/srv` (on `/data`), a `uv`-managed virtualenv, a
systemd service each, Caddy (US-SRV-8) in front; an app that needs system libraries or
odd dependencies runs in a container instead. Pengy can generate the unit and proxy
entry.
**Acceptance:** adding an app is: code + `uv sync` + one unit + one proxy entry; apps
restart independently; nothing is installed into the image; all survive an update.

**US-SRV-11 — Headless GPU inference.**
> *As the self-hoster, I want behoserver to run llama.cpp on its NVIDIA GPUs with CUDA 13.1,
> headless, so the inference box is a server, not a desktop.*

How: the **Server NVIDIA** edition (`pengyos-server-nvidia`, decided 2026-10-03: a
separate image, like the Desktop NVIDIA, because the extension machinery of US-ALL-2 is
too far out): the open kernel modules, CUDA driver API, NVML/`nvidia-smi`, persistenced
and nvidia-container-toolkit's CDI half — no GL, no Qt. The CUDA **toolkit** (nvcc 13.1) lives in a
container (`nvidia/cuda:13.1-devel-ubuntu24.04`, via distrobox `--nvidia` or CDI); models
from Hugging Face land on `/data`.
**Acceptance:** `nvidia-smi` sees every GPU on a Server install; a CUDA 13.1 llama.cpp
build uses all GPUs; `llama-server` runs as a service across reboots and image updates;
the default Server image stays free of the proprietary driver; behoserver updates from
the `pengyos-server-nvidia` feed.

---

## 3. PengyOS **Desktop** — “the daily driver”

**The story of this edition:** a fast, low-memory, *pleasant* environment good enough to
live in — work, browse, create, play — with real human taste and no telemetry.

### Personas

- **The daily-driver user** — uses it for everything; notices if it's slow, ugly, or
  annoying.
- **The developer** — terminal-heavy; lives in zsh; Python is part of the OS, not an
  add-on; builds C/C++, Rust and Go projects; containers; wants the server's tooling on
  the desktop. Eventually builds PengyOS itself on PengyOS.
- **The gamer** (the household) — Steam/Proton; hardware-hungry; anti-cheat.
- **The creator/curator** — cares about theme, defaults, the *feel* of the thing.

### Stories

**US-DSK-1 — Boot into a working, beautiful session.**
> *As the daily-driver user, I want a login that just works and looks good, so the machine
> feels finished.*

How: the COSMIC (or chosen) session on Wayland; the curated theme and boot experience.
**Acceptance:** boots to the login and a usable desktop; Wi-Fi, audio, Bluetooth work;
suspend/resume works; fractional scaling is correct; no first-run configuration theatre.

**US-DSK-2 — Do everyday things without ceremony.**
> *As the daily-driver user, I want my normal apps to install and run, so the machine is
> useful on day one.*

How: **Flatpak** as the primary app lane (+ AppImage); a curated default set (browser,
terminal, files, editor, media, office, PDF, archive, calculator, screenshots).
**Acceptance:** a Flatpak app installs, launches, integrates (menu, mime, file picker),
and updates atomically; the default apps cover the everyday list out of the box.

**US-DSK-3 — Develop on it.**
> *As the developer, I want a real dev environment, so I don't need a separate machine.*

How: zsh as the installed user's shell, git, the Server tool set (tmux, editors like
helix/Kate), Python (US-DEV-1), containers (`podman`/`distrobox`) and the compiler lane
(US-DEV-2).
**Acceptance:** a container-based dev workflow runs; editors and git are present and
comfortable; `distrobox` can provide a different distro's tooling when needed.

**US-DEV-1 — Python is part of the OS.**
> *As the developer, I want a complete Python in the base image, and to add packages to
> it the normal way, so scripting on this machine is never a setup task.*

How: `python3` with the full standard library that people assume is there (ssl, sqlite3,
lzma/bz2/zlib, readline, curses, decimal, CJK codecs, venv, ensurepip) + `pip`. Packages
go where they belong on an immutable root: `pip install --user`, a venv, or `uv`
(a static binary — `pip install --user uv`, or its standalone installer into
`~/.local/bin`). All of that is on the data partition, so it survives image updates.
Wheels that need a C compiler to build come from US-DEV-2.
**Acceptance:** `python3 -c "import ssl, sqlite3, lzma, bz2, readline, curses, venv"`
succeeds; `python3 -m venv` + `pip install requests` works; `uv` installs and runs; all
of it is still there after an image update.

**US-DEV-2 — Build native code (C/C++, Rust, Go).**
> *As the developer, I want a C/C++ compiler and the Rust and Go toolchains, so I can
> build my own projects here.*

How: Buildroot cannot put a native compiler in the image (it cross-compiles; a target
toolchain is explicitly out of scope). So the compiler lane is a **distrobox**: a
podman container of a mainstream distro (Ubuntu/Fedora) that shares `$HOME`, the user,
the display and the network, and feels native — `distrobox create -i ubuntu:24.04 dev`,
then `apt install build-essential clang`, `rustup`, Go. Build outputs land in `$HOME`
and usually run on the host too (static Go; Rust with glibc — same C library as the
image). Go's official tarball also runs natively (`CGO_ENABLED=0`) without the box.
Later: a PengyOS-built SDK sysext (US-DEV-3) could make the compiler native.
**Acceptance:** from a fresh install, one command gives a shell with `gcc`, `clang`,
`cargo` and `go`; a Rust and a Go project build there and their binaries run on the host;
the box survives image updates (it lives in `/home` and `/var/lib/containers` on the data
partition).

**US-DEV-3 — Build PengyOS on PengyOS (self-hosting).**
> *As the curator, I want to build the next PengyOS image on a PengyOS machine, so the
> project does not depend on another distro to exist.*

How: phase 1 (now) — the build host is Ubuntu (behoserver), the known-good baseline.
Phase 2 — the same build runs in a **pinned builder container** (Buildroot's host
dependencies + the Rust toolchain for the COSMIC apps) on PengyOS via podman, producing
byte-comparable output to the Ubuntu build. Phase 3 — the builder container itself is
built from PengyOS sources.
**Acceptance:** phase 2: `pengyos-build` inside the builder container on a PengyOS
machine produces a bootable installer image, and its package list/hashes match the Ubuntu
build of the same commit (modulo documented non-reproducible parts).

**US-DEV-4 — Let the agent fetch, build and run things.**
> *As the developer, I want Pengy to clone repos, build C++/Go/Rust projects, download
> models and run them (e.g. llama.cpp with CUDA), so experiments are a sentence away.*

How: Pengy's skills (mostly `uv`-operated Python) run builds **in the dev box** —
`pengyos-guest enter dev -- …` (a `cuda` box on NVIDIA machines) — with outputs and
Hugging Face models in `$HOME`/`/data`; results run on the host. PengyOS ships a declared
`dev` (and `cuda`) guest (`pengyos-guest`) so the box exists without setup.
*Status (2026-09-29):* the dev box ships in Server/Desktop (`dev`) and NVIDIA (`cuda`)
images and is created on first `enter`; verified in QEMU (C and Rust built in the box
run on the host). `cuda`'s `--nvidia` path awaits a test on NVIDIA hardware.
**Acceptance:** "build llama.cpp with CUDA and serve model X" goes from a fresh install
to a running `llama-server` without manual steps beyond approval; the host image is
unchanged afterwards.

**US-DSK-4 — Play games.**
> *As the gamer, I want Steam and my library to work, so this can be my main machine.*

How: Steam via **Flatpak — the COSMIC store (Flathub)** — the primary app lane; the
Desktop already ships Flatpak + Flathub + `cosmic-store`, so Steam installs from the
store with no bespoke lane. Proton + Steam Linux Runtime do the rest; `gamescope`
optional. (The certified `.deb`→prefix Lane-2 path and a Lane-4 guest remain *fallbacks*,
not requirements — the `.deb` is only a thin launcher that bootstraps Valve's runtime
from Valve's servers, so it buys no reproducibility over the Flatpak.)
**Acceptance:** Steam installs and launches; a Proton title runs at expected performance;
GPU + audio + controllers are correct; anti-cheat titles are honestly documented as
supported-or-not.

**US-DSK-5 — Make it mine.**
> *As the creator/curator, I want to shape the look and defaults, so it feels like a place,
> not an appliance.*

How: a coherent theme, sensible defaults, keybindings, wallpapers, boot splash — the
**human-led artistry domains** (README Principle 8).
**Acceptance:** theme and defaults are set by human choice; changes are declarative and
reproducible; the system still looks coherent under dark/light and scaling.

**US-DSK-6 — Work with the agent.**
> *As the daily-driver user, I want the agent to help me fix and change the system, so the
> machine stays healthy without me being a sysadmin.*

How: the Pengy GUI; agent proposes system changes; reversible Tier-1 changes happen
freely; Tier-2 changes need my signature (§SPEC 1.5).
**Acceptance:** the agent can diagnose and apply a Tier-1 fix (e.g. a config/bootloader
tweak) which is snapshotted and reversible; a Tier-2 change is blocked pending approval.

**US-DSK-7 — No telemetry, no lock-in.**
> *As the daily-driver user, I want to be sure nothing phones home, so the machine is
> actually mine.*

How: verified absence of telemetry in the base and default apps; documented network
behaviour.
**Acceptance:** a network audit of a default install shows no unexpected egress; data
stays local unless the user directs otherwise.

**US-DSK-8 — Sign into my browser and carry on.**
> *As the daily-driver user, I want my browser with its sync account, so a new machine
> has my bookmarks, passwords and extensions in minutes.*

How: Mozilla's official **Firefox Flatpak** from the COSMIC store (Lane 3); Chrome/Chromium
the same way when needed. No browser in the image.
**Acceptance:** Firefox installs from the store, signs into Sync, and is the default
browser for links from other apps.

**US-DSK-9 — Keep my files in sync across machines.**
> *As the daily-driver user, I want a folder (`~/Personal`) kept identical on all my
> machines, so I can pick up anywhere.*

How: the sync tool is **the user's choice, not the image's** (US-ALL-1). Pat's is
Syncthing: a single upstream binary in `~/.local/bin` (or Homebrew), a **user** systemd
service, lingering on headless boxes, the firewall port opened — a Pengy skill does all
of it. A friend might use a store app (e.g. SyncThingy) or something else entirely.
**Acceptance:** a folder syncs between a Desktop and a Server install with nobody logged
in on the server; removing the tool removes it completely; nothing sync-specific is in
the image.

**US-DSK-10 — Work with USB devices from the browser.**
> *As the developer, I want WebUSB / adb to reach my Meta Quest 3 (and Android phones), so
> device work needs no other machine.*

How: udev rules in the base granting the logged-in user access to common developer USB
devices (Meta/Oculus vendor `2833`, Android/adb); Chrome (Flatpak or in the dev box) with
device access.
**Acceptance:** with the headset plugged in, a WebUSB page in Chrome sees and talks to
it; `adb devices` lists it — without running anything as root.

**US-DSK-11 — Put my own widgets in the panel.**
> *As the developer, I want small panel/desktop widgets for my own services (e.g. my LLM
> proxy's token usage), so my tools are glanceable.*

How: **COSMIC applets** (Rust + libcosmic; `cosmic-applet-template`) replace the GNOME
extensions; personal ones install per user, ones that ship with PengyOS become recipes.
**Acceptance:** a custom applet that polls an HTTP endpoint appears in the panel, updates
live, and survives an image update and a panel restart.

**US-DSK-12 — My editor sees my real toolchains.**
> *As the developer, I want VS Code with the compilers and SDKs of my dev box, so editing,
> building and debugging are one environment.*

How: VS Code (and other `.deb`-only tools) installed **inside the dev box** (US-DEV-2) and
exported to the host launcher with `distrobox-export`; FreeTube, Steam and other plain
apps stay Flatpaks.
**Acceptance:** VS Code launches from the COSMIC app grid, builds and debugs a C++/Rust
project with the box's toolchain, and its extensions persist across image updates.

**US-DSK-13 — Print and scan.**
> *As the daily-driver user, I want to add my printer and scanner and use them from apps,
> so paper still works.*

How: CUPS + drivers and SANE, with a way to *add* a printer from the desktop: the
Printers app (`tools/pengyos-printers`) finds, adds and removes printers, manages the
queue and prints a test page. There is still no scanner UI of our own. Candidate for the
printing **extension** (US-ALL-2) rather than the base.
**Acceptance:** a network printer is discovered and added from the GUI; an app prints a
test page; a scanner is found and scans from a scanning app.

**US-DSK-14 — Install on NVIDIA-only hardware from the same image.**
> *As the gamer (or the self-hoster), I want to install PengyOS on a machine whose only GPU
> is NVIDIA, from the normal stick, and get the real driver.*

How: one Desktop image; the stick carries the NVIDIA **extension** (US-ALL-2) in its
payload. A GRUB entry "with NVIDIA driver" (the opt-in for the proprietary driver)
activates it in the live session; the installer detects an NVIDIA GPU, asks, and puts the
extension on the installed system.
**Acceptance:** on an NVIDIA-only box (e.g. behoserver) the stick boots to an accelerated
live desktop via that entry; the installed system boots accelerated with CUDA available;
the default path never ships the proprietary driver without the opt-in.

**US-DSK-15 — Still usable when the GPU driver isn't.**
> *As the daily-driver user, I want a usable desktop even when my GPU has no working driver,
> so a missing or broken driver never means a dead screen.*

How: nouveau is not used; the firmware framebuffer (simpledrm) stays up and COSMIC renders
in software (Mesa llvmpipe). This is also the recovery path if the NVIDIA extension breaks
after an update. (2026-09-29: verified in QEMU with `-device ramfb` — a pure firmware
framebuffer — COSMIC renders correctly on simpledrm. Corrupted output under `-vga std` is
a QEMU artifact: the emulated VGA's mode registers disagree with the GOP framebuffer, so
test with ramfb. llvmpipe added; only the slow softpipe was built before.)
**Acceptance:** with no GPU driver (QEMU `-vga none -device ramfb`), the live session and
the installer render correctly and are usable, with llvmpipe as the renderer; the same
holds on a real NVIDIA box booted without the extension.

**US-DSK-16 — The agent arrives configured, and knows it has skills.**
> *As the person who just installed PengyOS, I want the built-in assistant to be ready to
> use on first boot, so I am not handed an agent with no endpoint and no documentation.*

How: the installer's **Pengy page** (base URL, API key, model) is part of both front ends
— a screen in the Desktop installer, a page in the console installer — and the engine seeds
`~/.config/pengy/settings.json` for the user it creates (0600, owned by them, key on
stdin). The page is **skippable**: an empty base URL writes nothing and Pengy keeps the
defaults the image ships (local endpoint, no key, no model). The image's `/etc/skel/skills`
gives every home the skills index plus the `pengyos` and `pengy` skills, and the system
message the installer writes names `~/skills/skill_index.md`.
**Acceptance:** installing with the page filled leaves a working `settings.json` (endpoint,
key, model, system message) owned by the user and readable only by them; installing with it
skipped leaves **no** config file and Pengy still starts, pointing at its own defaults; a
fresh home has `~/skills/skill_index.md` with the two skills; a malformed endpoint is
re-prompted with a reason, never accepted.

---

## Cross-cutting stories

**US-AUG-1 — The curator's loop.**
> *As the curator, I want to set direction and ratify the boundary while the agent does the
> work, so a bespoke OS is sustainable by a small team.*

How: philosophy + spec + stories are the durable memory; the agent works the loop
(observe → propose → build → verify → ratify); the human signs the security boundary.
**Acceptance:** a change can travel from observation to shipped-with-signature with a
clear human approval at the boundary and a full audit trail; the human's *taste* is
reflected, not overridden.

**US-AGT-1 — The agent's own requirements.**
> *As the maintenance agent, I want the system to be legible and my actions reversible, so
> I can maintain it without breaking it or lying about it.*

How: machine-readable state; deterministic, idempotent interfaces; an append-only
mutation/attest log; a verifiable definition of *done*.
**Acceptance:** the agent can query system state in a machine-readable form; every
mutation is logged and revertible; for any "done" it claims, there is a command that
proves it.

**US-AGT-2 — The agent on the box, and the way back to the repo.**
> *As the curator, I want the agent running on an installed machine to change its
> configuration as needed, and the changes that prove themselves to flow back into the
> repo, so every machine's fixes become the next image's defaults.*

How: the root is a read-only image (Tier 0); everything machine-specific is on the data
partition (`docs/state-layout.md`): `/etc` is an overlay whose upper layer is
`/data/etc/upper`, `/var` `/home` `/root` are btrfs subvolumes. The agent's Tier-1
mutations (config, services, bootloader cmdline; packages later, e.g. as systemd-sysext
images) land there — **before** each one it takes a read-only btrfs snapshot and logs
the mutation (why, diff, how to verify), so any change is one snapshot away from undone.
A mutation that has held up (verified, survived reboots, the human kept it) is
**converged**: the agent turns the upper-layer diff into a change to the build tree
(`rootfs-overlay`, defconfig, `linux.fragment`, a package) and opens it for review; once
the next image carries it, the upper-layer copy is redundant and is dropped. Tier-2
changes (the image itself, keys, the security boundary) still need the human's signature.
**Acceptance:** the agent changes a Tier-1 setting on a running machine with a snapshot
and a log entry; reverting restores the prior state exactly; a kept change becomes a
reviewable repo commit; after installing the image built from it, the machine's upper
layer no longer holds that file and behaviour is unchanged.

**US-ALL-1 — Bring my own tools without rebuilding the image.**
> *As any user (Pat or a friend), I want to add the tools I use — and not carry the ones I
> don't — without a new image, so PengyOS fits whoever is using it.*

How: the image carries only what every machine of its edition needs (boot, hardware,
network, updates, the desktop, the runtimes of the other lanes, Pengy). Everything else
goes through a lane (SPEC §2.5): **Flatpak** for GUI apps, **user space** (`~/.local/bin`,
user services, optionally Homebrew) for single-binary tools like Syncthing and rclone,
**containers / the dev box** for services and toolchains, and — only for optional
*system-level* pieces — **extensions** (US-ALL-2). Personal choices are never baked in.
**Acceptance:** Syncthing, rclone and a CLI tool of the user's choosing install, run as
services where needed, survive an image update, and uninstall completely — none of them
in the image; a friend's install contains none of Pat's personal tools.

**US-ALL-2 — Optional system-level components as extensions.**
> *As the curator, I want the few system-level things only some machines need to be
> opt-in, so one image per edition serves every machine.*

How: `systemd-sysext` images built **by us, from this tree, signed with the image key,
rebuilt in lockstep with every release**; no third-party extensions, no dependencies
between extensions, no install scripts (files + units + sysusers/tmpfiles only). The
admission test: it must be system-level, optional for a meaningful group, and too big or
opinionated for the base. Candidates: **NVIDIA** (GPU and compute-only; would replace the
separate NVIDIA editions, Server NVIDIA and Desktop NVIDIA, which ship meanwhile), **virtualization host** (libvirt and its management stack; plain QEMU + KVM has been in the Server and Desktop base since 26.10.23), **printing and
scanning**, maybe a **web server**. If the catalogue passes about half a dozen, something
belongs in the base or in user space instead. Kernel modules in an extension need a
module-index (depmod) step at merge.
**Acceptance:** enabling an extension adds it at the next boot (or `systemd-sysext
refresh`) and disabling removes it without touching the image; after an OS update the
extension matches the new release automatically; an extension from another release is
refused.

**US-AUG-2 — Run the update service for my fleet.**
> *As the curator, I want to publish PengyOS releases and have my machines update from
> them in stages, so the fleet stays current without me touching each box.*

How: `pengyos-release` publishes signed images (and the matching extensions) to a feed
served by a Server install (Caddy, static files); `pengyos-update` pulls A/B on each
machine; `pengyos-fleet-rollout` stages (canary → rest) and rolls back. Signing stays a
human step (Tier 2).
**Acceptance:** a signed release reaches a canary machine, then the fleet, on the
schedule; a bad release is halted and rolled back; an unsigned or wrongly signed release
is refused by every machine.
