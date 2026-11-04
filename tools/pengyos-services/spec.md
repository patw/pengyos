# pengyos-services — what this machine serves, and whether it will after a reboot

## Summary
A read-only Python tool, shipped in the Core overlay (so every edition has it), that
answers in one command what today takes eight: for every service on the machine,
whether it runs, whether it starts at boot (and why, or why not), which ports it
listens on, whether the firewall lets the LAN reach them, how much memory it really
uses, and where its data lives. It works across the three ways a PengyOS Server runs
things: systemd units (system and user), Quadlet containers (system and user lane),
and `pengyos-vm` virtual machines. It changes nothing.

## Purpose
Pengy's Server field review (2026-11-03, `~/Personal/pengyos-server-review-2026-11-03.md`)
found that "the service layer is real, but it is not observable". The four costliest
findings are one gap:

- **F2:** `systemctl is-enabled` answers `generated` for Quadlet units, and user
  units do not show up in `--state=enabled` even when they autostart. No standard
  command answers "will this still run after a reboot?" for the lane the docs
  recommend.
- **F4:** `pengyos-firewall status` lists open ports, and `ss` lists listeners, but
  nothing joins them. Port 8080 was open and listening, yet unreachable: the listener
  was a VM's forward on 127.0.0.1, not the container. Behothinkpad's pengy-web was
  "active, enabled at boot" and unreachable from the LAN for a week (BotTalk 83fd6828).
- **F5:** a VM forward listens on loopback by default, and a container publish
  and a VM forward on the same port collide silently.
- **F11:** `MemoryCurrent` charged syncthing 9.8 GB of page cache before a reboot
  and 148 MB after. A capacity decision made from it is wrong.

A migration's "definition of done" for each service (runs, survives a reboot,
reachable, data located) is this command. Owning it gives every host move a
machine-checked answer instead of a checklist typed out each time.

## Interface
```
pengyos-services status [NAME] [--all] [--json] [--check] [--probe-from HOST]
pengyos-services --help
pengyos-services --root DIR …     # test hook: read fixtures under DIR, run DIR's fake tools
```
- `status` lists services. `NAME` (a unit, container, or VM name) shows one in full.
- Which services are listed by default: anything the machine's owner added
  (units under `/etc/systemd/system` or `~/.config/systemd/user`, every Quadlet,
  every VM), anything with a listening socket (so `sshd` and pengy-web appear), and
  anything failed. PengyOS mounts `/etc` as an overlay, and the image ships units of
  its own in `/etc/systemd/system`. So when `/etc` is an overlay, a unit there counts
  as the owner's only if it is in the upper layer (`upperdir=` in
  `/proc/self/mountinfo`, `/data/etc/upper`). Alias links (`dbus-org.….service`) are
  not units. Units that are part of the image and do not listen are left out;
  `--all` shows them too.
- Without root it reports system services plus the calling user's. Run under `sudo`,
  it covers every user with a running manager or linger. Anything it could not see
  is marked as such ("owner unknown: run with sudo"), never omitted.
- `--json` writes one document (schema below). `--check` exits 1 if an error-level
  problem is found (see Problems), so a fleet script or a migration step can gate on
  it. Warnings do not fail it.
- `--probe-from HOST` also tests reachability for real: over `ssh HOST`, it connects
  to each port that is served and meant to be reachable, at this machine's LAN
  address. Without it, reachability is worked out from the bind address and the
  firewall rules, and labelled `by rules`, never `tested`. (Connecting to yourself
  proves nothing: traffic to your own address arrives on `lo`, which the firewall
  accepts.)
- Misuse exits 2. A source that fails (podman missing on Core, no VMs) gives an
  empty section, not an error.

## What it reports, per service
| Field | Source | Notes |
|---|---|---|
| `name`, `kind` | unit name; `unit`, `quadlet`, `vm` | a Quadlet's container name and its `.container` file |
| `scope` | `system` or `user:<name>` | |
| `state` | `ActiveState`, `SubState`, `Result` | `failed` names the result (`exit-code`, `oom-kill`, …) |
| `since`, `restarts` | `ActiveEnterTimestamp`, `NRestarts` | restarts more than 3 is a problem |
| `autostart` | yes / no / `at-login-only` / `on-demand` / `via-timer`, plus `why` | see Autostart |
| `memory` | cgroup `memory.peak`, and `anon` from `memory.stat` | never `MemoryCurrent`; page cache is shown apart as `file` |
| `ports` | see Ports | each one with owner, bind, firewall, reachability |
| `linger` | `/var/lib/systemd/linger/NAME` (what `loginctl show-user -p Linger` reads) | user scope only |
| `data` | Quadlet `Volume=` (a host path, or a named volume's `…/volumes/NAME/_data`), the unit's `WorkingDirectory=` (not a home directory)/`StateDirectory=`/`ReadWritePaths=`, a VM's `~/vms/NAME` | where state lives, for backups and host moves. Not `podman inspect`: it is slow per container, needs each user's podman, and a Quadlet's `Volume=` says the same |
| `source` | the unit file, or `.container`, or `vm.json` | |

### Autostart
This is the answer F2 asks for. It is worked out from what systemd will actually do,
not from `is-enabled`:
1. Follow `WantedBy`/`RequiredBy` (`systemctl show`) up from the unit. If it reaches
   `multi-user.target`/`graphical.target` (system) or `default.target` (user), the
   answer is yes. `why` names the link, for example
   `quadlet [Install] WantedBy=default.target` for a generated unit (the generator's
   symlink lives in `/run/systemd/generator*`), or `enabled: multi-user.target.wants`.
2. A user unit that answers yes, for a user without linger, is `at-login-only`:
   it will not start at boot with nobody logged in. That is a problem (`user-no-linger`).
3. A unit that only a `.socket` or `.timer` pulls in is `on-demand (NAME.socket)` or
   `via-timer (NAME.timer, next run …)`.
4. A VM's autostart is `pengyos-vm@NAME.service` under the user manager, so rules 1–2
   apply to it.
5. If none of these apply, the answer is no, and `why` says what would change it
   (`systemctl enable …`, `add [Install] WantedBy=… to the .container`).

### Ports
Listeners come from `ss -Htulpn` (TCP listen, UDP bound). Each socket's PID is mapped
to its unit through `/proc/PID/cgroup`. That one mechanism covers a plain service, a
rootless container (pasta/rootlessport run in the container's unit), and a VM's
`hostfwd` (qemu runs in `pengyos-vm@NAME.service`). A container started with `podman
run` from inside a unit runs in its own `libpod-ID.scope`; its conmon (`conmon … -c
ID`) stays in the unit's cgroup, so the socket is traced to the unit through that. A
socket no cgroup explains, but whose port exactly one service declares (pasta outside
the unit, or a socket not ours when not root), goes to that service. Declared ports are read as well:
Quadlet `PublishPort=` and `vm.json` forwards. So a stopped service still shows the
ports it would take, and a collision can be found before it happens.

Each port gets:
- `bind`: `loopback`, `all`, or the address.
- `firewall`: `always-open` (22, mDNS), `image-rule` (the Desktop range),
  `opened <cidr>` (a `pengyos-firewall` rule; the CIDR actually in force), or `blocked`.
  Read from `pengyos-firewall status --json`.
- `reachable` from the LAN: `yes`, `no — bound to loopback`, `no — blocked by the
  firewall`, `no — not listening`, `no — bound to ADDR, not a LAN address`, each with
  `by rules` or `tested from HOST`.
- A **rootful** Quadlet's `PublishPort=` is DNATed by netavark, and the ruleset's
  forward chain accepts DNATed traffic (`ct status dnat accept`). The input rules,
  and so `pengyos-firewall`, do not apply to it, and there is no socket for `ss` to
  see. It is reported as `firewall: not filtered (rootful publish: DNAT …)` and as
  listening whenever the unit is active. A rootless publish (pasta, rootlessport) is
  a host socket, and the firewall applies as usual.

Also reported, apart from any service:
- Firewall rules with nothing listening behind them (`open-not-served`).
- Listeners that no unit owns (a process started by hand, `nohup`, tmux).

## Problems
| Code | When |
|---|---|
| `failed` | the unit is failed, or restarted more than 3 times since boot |
| `quadlet-no-unit` | a `.container`/`.kube`/… file with no generated unit; the generator's error (`podman-system-generator --dryrun`, or the user generator) is quoted with the file and the key (F1) |
| `user-no-linger` | a user service that should autostart, for a user without linger |
| `port-collision` | two owners declare or bind the same port and protocol (container publish against VM forward, two VMs, unit against container) |
| `served-not-reachable` | an owner-added service listens on all addresses (or a LAN one), but the firewall blocks it. A **warning**, because it may be meant. Image services are left out: systemd-resolved's LLMNR on a closed 5355 is by design. With `--probe-from`, a port the rules call reachable that the probe could not reach is this code at error level |
| `loopback-only-forward` | a VM forward or a container publish bound to 127.0.0.1 while a firewall rule opens the same port: someone meant the LAN to reach it (F4/F5) |
| `open-not-served` | a `pengyos-firewall` rule, and nothing listens on that port (a **warning**; the detail names a stopped service that declares it) |

Every other code is an error. Each problem carries the command that fixes it: `loginctl enable-linger NAME`,
`pengyos-vm set NAME --unforward 8080 --forward 0.0.0.0:8080:80`, `sudo pengyos-firewall
allow 8080 --from 10.0.23.0/24`, and so on.

## JSON
```json
{"tool": "pengyos-services", "version": 1, "host": "behothinkpad",
 "edition": "server", "release": "26.11.0", "generated_at": "2026-11-04T10:00:00Z",
 "privileged": true, "lan": ["10.0.23.0/24"],
 "services": [{"name": "staging-jellyfin", "kind": "quadlet", "scope": "user:pengy",
   "source": "/home/pengy/.config/containers/systemd/staging-jellyfin.container",
   "state": "active", "sub": "running", "since": "…", "restarts": 0,
   "autostart": "yes", "why": "quadlet [Install] WantedBy=default.target; linger on",
   "memory": {"peak": 412000000, "anon": 180000000, "file": 230000000},
   "ports": [{"port": 8096, "proto": "tcp", "bind": "all", "owner": "container publish",
     "firewall": "opened 10.0.23.0/24", "reachable": "yes", "basis": "by rules"}],
   "linger": true, "data": ["/data/media", "/home/pengy/.local/share/jellyfin"]}],
 "unowned_listeners": [], "open_not_served": [],
 "problems": [{"code": "user-no-linger", "service": "…", "detail": "…", "fix": "…"}]}
```
Also present: each service's `unit`, `result` and `ports[].listening`; each
problem's `severity` (`error` or `warning`) and, for `quadlet-no-unit`, `key`
(`Container/Env`); and at the top `lan_addresses`, `firewall_active`, `not_seen`
(users not inspected without root) and `notes` (sources that did not answer).
`version` bumps on any breaking change. Sizes are bytes and times are ISO 8601 UTC.
`lan` is the machine's own subnets (from its addresses); it is what `reachable`
reasons against.

## Deliverable
| File | Ships at |
|---|---|
| `buildroot-external/board/core/rootfs-overlay/usr/bin/pengyos-services` | `/usr/bin/pengyos-services`, every edition (Core has Python; podman and VM sections are empty there) |
| `tools/pengyos-services/` | spec, README, CHANGELOG, tests, `verify.toml` (not in the image) |
| `buildroot-external/board/server/features-required.txt` | an `exists /usr/bin/pengyos-services` line |

## Acceptance criteria
- The suite (`python3 -m pytest -q`) passes against fixtures under `--root`: fake
  `systemctl`, `ss`, `podman`, `loginctl`, `pengyos-firewall`, `pengyos-vm` outputs,
  plus `/proc/*/cgroup` and cgroup memory files. Fixtures cover each Problem code,
  each Autostart answer, a non-root run, and JSON that matches the schema.
- On a booted Server image (qemu-verify), set up as Pengy's review had it, `status
  --json` reports correctly: a system Quadlet publishing 8282 (autostart yes, no
  linger needed); a user Quadlet with no linger (`at-login-only`, `user-no-linger`); a
  Quadlet with an unsupported key (`quadlet-no-unit`, naming the key); a VM forward
  on 127.0.0.1:8080 with `pengyos-firewall allow 8080` (`loopback-only-forward`); a
  firewall rule on 9999 with nothing listening (`open-not-served`); `sshd` (always-open).
- Pengy's re-test 1 (review §4): on behothinkpad with 6 containers, 1 venv service,
  1 VM, 2 system units, 1 user unit and 8 firewall rules, every one is described
  correctly. Pengy runs this.
- It runs in under 3 seconds on that machine, and never writes anything.

## Out of scope
- Changing anything: starting, enabling, opening ports. It prints the command
  instead. (`pengyos-timer`, `pengyos-vm` and `pengyos-firewall` do the changing.)
- Linting Quadlet files before they are written (`pengyos-containers lint`, a separate
  tool). This one reports the generator's error after the fact.
- Fleet aggregation and diffing two hosts. `--json` is stable so that a script, or
  Pengy, can do it.
- Services inside a VM or a guest. It reports the VM and its forwards, not what runs in it.
