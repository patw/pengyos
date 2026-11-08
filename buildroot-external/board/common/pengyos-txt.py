#!/usr/bin/env python3
"""Write /usr/share/pengyos.txt, /usr/share/pengyos/packages.txt and the motd nudge.

    python3 board/common/pengyos-txt.py TARGET_DIR [BUILD_DIR]

Run from each edition's post-build.sh, after os-release.sh and the tool stubs, so
the last edition's run describes the finished tree. It is idempotent: a second
post-build pass (release.sh's SBOM embed) writes the same files again.

pengyos.txt is the one page a person or an agent reads to learn how this machine
differs from the Linux they know: an image you do not change, updates by slot, the
dev guest, timers instead of cron, the container lanes, the firewall, the
commands this edition ships and what it leaves out on purpose. Every
edition-specific sentence is chosen by what TARGET_DIR holds, never by the edition
name, so the page cannot claim a tool the image lacks. The command list comes from
the files themselves, and a command with no entry in TOOLS still gets listed.

The motd's first line becomes "PengyOS <edition> <version> -- how this system
works: /usr/share/pengyos.txt". `ssh host cmd` never shows a motd, so the stubs,
/etc/os-release (PENGYOS_README) and the skills carry the same pointer.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

README = "/usr/share/pengyos.txt"
PACKAGES = "/usr/share/pengyos/packages.txt"

# One line per command, for the "Commands on this machine" list. A command that
# the image ships and this table lacks is listed with no description, and
# scripts/tests/test_pengyos_txt.py fails, so the table keeps up. Each line fits
# in 54 columns: with the longest name, the table is still 80 wide.
TOOLS = {
    "pengy-cli": "ask Pengy, the built-in assistant, in this terminal",
    "pengy-web": "Pengy in a browser",
    "pengy": "the Pengy desktop app",
    "pengy-vmm": "the VMs in a browser, on 127.0.0.1:5100",
    "pengy-games": "the games launcher",
    "pengyos-update": "version, slots, updates and rollback",
    "pengyos-services": "what this machine serves, and after a reboot",
    "pengyos-firewall": "status; allow PORT [--from CIDR] (as root)",
    "pengyos-guest": "distrobox guests; `dev` has the compilers",
    "dx": "one command in the dev guest, here (dx --shell)",
    "pengyos-vm": "VMs in ~/vms: create, start, ssh, console, stop",
    "pengyos-shares": "share folders over SMB: add, remove, passwd",
    "pengyos-webui": "serve pengy-web at boot (no login: loopback only)",
    "pengyos-gpu-memory": "how much RAM an AMD GPU may use: status, set",
    "pengyos-ssh-password": "SSH password logins: status, on, off (keys only)",
    "pengyos-postmortem": "why did that session or boot end? (the journal)",
    "pengyos-install": "the install engine the installers drive",
    "pengyos-install-tui": "the console installer",
    "pengyos-install-gui": "the graphical installer",
    "pengyos-autologin": "automatic login to the desktop: on or off",
    "pengyos-appimage": "add an AppImage to the launcher; why won't it start",
    "pengyos-remote": "remote desktop (RDP) of the logged-in session",
    "pengyos-remote-desktop": "the Remote Desktop app over pengyos-remote",
    "pengyos-sharing": "the File Sharing app over pengyos-shares",
    "pengyos-updates": "the Updates app and notifier",
    "pengyos-disks": "the Disks app: partition, format, write images",
    "pengyos-usage": "the Disk Usage app: what is taking the space",
    "pengyos-hwmon": "Hardware Monitor: temps, fans, power",
    "pengyos-fans": "fan profiles: status, detect, profile (as root)",
    "pengyos-firmware": "the Firmware app (fwupd)",
    "pengyos-gamepads": "the Gamepads app: list and test controllers",
    "pengyos-printers": "the Printers app: printers, queue, test page",
    "pengyos-dri-prime": "run an app on the discrete AMD GPU",
    "prime-run": "run an app on the NVIDIA GPU",
}

# Commands people reach for that no edition ships, and what to do instead. Each is
# listed only when the image really lacks it (no file of that name, or only a
# PengyOS stub), so an edition that gains one stops claiming it is absent.
# {elsewhere} becomes where this edition can run it: in the dev guest, in a
# container, or (Core) on another machine.
ABSENT = [
    (("apt", "apt-get", "dnf", "yum", "pacman", "zypper"),
     "no package manager: the system is one signed image"),
    (("cc", "gcc", "make", "cmake", "cargo", "go"), "no compilers: build {elsewhere}"),
    (("crontab", "cron", "at"), "no cron or at: systemd timers do this"),
    (("nginx", "caddy", "httpd", "apache2"), "no web server: run one {container}"),
    (("node", "npm", "dotnet", "java", "php", "ruby"),
     "no runtimes beyond Python: run them {elsewhere}"),
    (("ffmpeg",), "no ffmpeg command: run it {elsewhere}"),
    (("docker",), "no docker{docker}"),
    (("syncthing", "snap"), "not shipped"),
]

STUB_MARK = b"PengyOS stub"
PATH_DIRS = ("usr/bin", "usr/sbin", "bin", "sbin")


def os_release(target: Path) -> dict[str, str]:
    out = {}
    for line in (target / "usr/lib/os-release").read_text().splitlines():
        k, sep, v = line.partition("=")
        if sep:
            out[k] = v.strip().strip('"')
    return out


def has(target: Path, rel: str) -> bool:
    return (target / rel).exists() or (target / rel).is_symlink()


def is_stub(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return STUB_MARK in f.read(512)
    except OSError:
        return False


def command(target: Path, name: str) -> str | None:
    """'real', 'stub' or None for a command name in TARGET_DIR's PATH."""
    for d in PATH_DIRS:
        p = target / d / name
        if p.exists():
            return "stub" if is_stub(p) else "real"
    return None


def real(target: Path, name: str) -> bool:
    return command(target, name) == "real"


def shipped_tools(target: Path) -> list[str]:
    names = set()
    for d in PATH_DIRS:
        dd = target / d
        if not dd.is_dir():
            continue
        for p in dd.iterdir():
            n = p.name
            if (n.startswith(("pengy-", "pengyos-")) or n == "pengy" or n in TOOLS) and not is_stub(p):
                if p.exists():
                    names.add(n)
    order = list(TOOLS)
    return sorted(names, key=lambda n: (order.index(n) if n in order else len(order), n))


def stubs(target: Path) -> list[str]:
    d = target / "usr/bin"
    return sorted(p.name for p in d.iterdir() if p.is_file() and is_stub(p)) if d.is_dir() else []


# --------------------------------------------------------------------------
# Package list: names from Buildroot's packages-file-list.txt, versions from the
# build directory names (NAME-VERSION).
# --------------------------------------------------------------------------

def packages(build_dir: Path | None) -> list[tuple[str, str]]:
    if not build_dir or not (build_dir / "packages-file-list.txt").is_file():
        return []
    names = set()
    with open(build_dir / "packages-file-list.txt", errors="replace") as f:
        for line in f:
            n = line.split(",", 1)[0].strip()
            if n:
                names.add(n)
    dirs = [p.name for p in build_dir.iterdir() if p.is_dir()]
    out = []
    for n in sorted(names):
        # util-linux-2.41.5 belongs to util-linux, util-linux-libs-2.41.5 does not:
        # a directory is n's when what follows "n-" is no longer package's tail.
        longer = [x[len(n) + 1:] for x in names if x.startswith(n + "-")]
        cands = [d[len(n) + 1:] for d in dirs if d.startswith(n + "-")]
        cands = [c for c in cands if not any(c == t or c.startswith(t + "-") for t in longer)]
        ver = [c for c in cands if re.match(r"v?\d", c)] or cands
        if n.startswith("skeleton"):
            continue  # Buildroot's base-layout pseudo-packages: no version, no software
        out.append((n, ver[0] if len(ver) == 1 else "-"))
    return out


def python_version(target: Path) -> str | None:
    """``3.14`` for an image with usr/lib/python3.14/os.py[c], else None."""
    found = []
    for d in (target / "usr/lib").glob("python3.*"):
        m = re.fullmatch(r"python(3\.\d+)", d.name)
        if m and ((d / "os.py").exists() or (d / "os.pyc").exists()):
            found.append(m.group(1))
    return max(found, key=lambda v: int(v.split(".")[1])) if found else None


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

def wrap_list(items: list[str], indent: str = "  ", width: int = 78) -> list[str]:
    lines, cur = [], indent
    for it in items:
        add = it if cur.strip() == "" else " " + it
        if len(cur) + len(add) > width and cur.strip():
            lines.append(cur)
            cur = indent + it
        else:
            cur += add
    if cur.strip():
        lines.append(cur)
    return lines


def render(target: Path, pkgs: list[tuple[str, str]]) -> str:
    rel = os_release(target)
    variant = rel.get("VARIANT", "")
    version = rel.get("IMAGE_VERSION", "")
    image_id = rel.get("IMAGE_ID", "")
    build = rel.get("BUILD_ID", "")
    ab = has(target, "usr/bin/pengyos-update")
    guest = real(target, "pengyos-guest")
    dx = real(target, "dx")
    podman = real(target, "podman")
    quadlet_readme = has(target, "etc/containers/systemd/README")
    flatpak = real(target, "flatpak")
    desktop_fw = has(target, "usr/lib/pengyos/nftables.d/input-desktop.nft")
    router_example = has(target, "usr/share/pengyos/examples/router/README.md")
    nm = real(target, "NetworkManager") or real(target, "nmcli")
    wpa = real(target, "wpa_supplicant")
    uv = real(target, "uv")
    pyver = python_version(target)
    services = real(target, "pengyos-services")
    vm = real(target, "pengyos-vm")
    sudo = real(target, "sudo")
    S = "sudo " if sudo else ""

    L: list[str] = []
    title = f"PengyOS {variant} {version} -- how this system works"
    L += [title, "=" * len(title), ""]
    L += [
        "PengyOS is built from source with Buildroot. It is not Debian, Fedora or Arch,",
        "and several habits from those do not apply here. Read this before installing",
        "software, editing system files or scheduling jobs. It describes this edition",
        "only, and was written when the image was built:",
        f"{image_id} {version}, commit {build}.",
        "",
    ]

    L += ["THE SYSTEM IS AN IMAGE", ""]
    L += [
        "  /           the system image, read-only. Not even root can change /usr.",
        "  /etc        an overlay: the image's /etc below, your changes above (kept",
        "              on the data partition, they survive updates).",
        "  /home /var /root   on the data partition; updates never touch them.",
        "  /usr/local  yours, on data, first on PATH: the place for a stopgap binary",
        "              or library until an image ships the real fix.",
        "",
    ]
    if ab:
        L += [
            "  Updates replace the whole image. There are two system slots: an update",
            "  is written to the one not running, booted once on trial, and kept only",
            "  if the system comes up. If it doesn't, the old slot boots again.",
            "",
        ]
        cmds = [("pengyos-update status", "version, channel, slots"),
                ("pengyos-update check", "is a newer signed release out?"),
                (f"{S}pengyos-update apply", "write it to the other slot"),
                (f"{S}pengyos-update rollback", "make the other slot the default again"),
                (f"{S}pengyos-update channel stable|testing", "")]
        w = max(len(c) for c, d in cmds if d) + 2
        L += [f"    {c.ljust(w)}{d}".rstrip() for c, d in cmds] + [""]
    else:
        L += ["  This edition does not update in place: install a newer image.", ""]

    L += ["INSTALLING SOFTWARE", ""]
    L += ["  There is no apt, dnf or pacman, and /usr is read-only. Instead:", ""]
    if flatpak:
        L += ["  - Desktop apps: Flatpak from Flathub (flatpak install flathub APP).",
              "    An AppImage works too: pengyos-appimage integrate APP.AppImage."]
    if guest:
        L += ["  - A full distribution, compilers, SDKs: the dev guest, an Ubuntu box",
              "    that shares your home directory (pengyos-guest enter dev)."]
    if podman:
        L += ["  - Services and other software: containers (podman, rootless)."]
    pip = "  - Python packages: python3 -m venv, or pip install --user"
    L += [pip + (", or uv." if uv else ".")]
    L += ["  - A program for this machine only: /usr/local/bin.",
          "  - Into the image itself: a recipe in the PengyOS repository and a new image.",
          ""]

    L += ["BUILDING CODE", ""]
    if guest:
        L += ["  The host has no compilers. cc, gcc, make, cargo and the rest print how",
              "  to build in the dev guest instead of \"command not found\":", "",
              "    pengyos-guest apply dev       once: gcc, cmake, ninja, go, rust, uv"]
        if dx:
            L += ["    dx make -j$(nproc)            one command in the guest, here",
                  "    dx --shell                    a shell in the guest"]
        else:
            L += ["    pengyos-guest enter dev       a shell in the guest"]
        L += ["", "  What you build there runs on the host: the guest's glibc is older.", ""]
        if pyver and uv and dx:
            # Proven on behothinkpad (26.11.1, 2026-11-05): netifaces from its sdist
            # and a local C extension, built in the guest, imported on the host.
            L += ["  A Python package that must compile (pip or uv stops at gcc): build its",
                  f"  wheel in the guest for this Python ({pyver}) and install it here:", "",
                  f"    dx uvx --python {pyver} pip wheel --no-deps -w ~/wheels PKG==VERSION",
                  f"    dx uv build --wheel --python {pyver} --out-dir ~/wheels  # your project",
                  "    uv pip install --find-links ~/wheels PKG", ""]
    else:
        L += ["  This edition has no compilers and no dev guest. "
              + ("Build in a container." if podman else "Build on another machine."), ""]

    L += ["SCHEDULED JOBS", ""]
    L += [
        "  systemd timers, not cron (there is no cron daemon and no at). A job is a",
        "  NAME.service plus a NAME.timer with OnCalendar=, in ~/.config/systemd/user/",
        "  (yourself) or /etc/systemd/system/ (root). `crontab` prints a full recipe",
        "  and a cron-to-OnCalendar table. A one-off job:",
        "  systemd-run --user --on-active=2h CMD.",
        "  User timers and services run while you are logged out only after",
        "  `loginctl enable-linger` (no sudo needed).",
        "",
    ]

    if podman:
        L += ["CONTAINERS AND SERVICES", ""]
        L += ["  A container that should start at boot is a Quadlet file, in one of two lanes:", "",
              "    /etc/containers/systemd/NAME.container      system lane: root, at boot",
              "    ~/.config/containers/systemd/NAME.container user lane: rootless; at boot",
              "                                                only with linger on", "",
              "  then `systemctl [--user] daemon-reload` and `start NAME`. A key the",
              "  generator does not know leaves no unit at all, without an error."]
        if quadlet_readme:
            L += ["  /etc/containers/systemd/README has the details."]
        L += [""]
        if real(target, "pengyos-gpu-memory"):
            L += ["  An AMD GPU in a container (ROCm stays in the image you run):",
                  "    podman run --device /dev/kfd --device /dev/dri --group-add keep-groups \\",
                  "        --ulimit memlock=-1:-1 IMAGE",
                  "  An APU (Strix Halo) maps system RAM: pengyos-gpu-memory set max, reboot.", ""]
        if real(target, "nvidia-ctk") or has(target, "etc/cdi/nvidia.yaml"):
            L += ["  An NVIDIA GPU in a container: podman run --device nvidia.com/gpu=all IMAGE", ""]
        if services:
            L += ["  pengyos-services status shows what this machine serves: every unit,",
                  "  container" + (" and VM" if vm else "") + ", whether it starts after a reboot, its ports, whether",
                  "  the firewall lets the LAN reach them, and each problem with its fix.",
                  "  sudo shows every user's services.", ""]
    elif services:
        L += ["SERVICES", "",
              "  pengyos-services status shows every service, whether it starts after a",
              "  reboot, its ports and whether the firewall lets the LAN reach them.", ""]

    L += ["NETWORK AND FIREWALL", ""]
    if real(target, "pengyos-ssh-password"):
        L += ["  The firewall denies inbound connections by default; ssh (22) and mDNS",
              "  are open. SSH takes keys only unless passwords were allowed:",
              "  pengyos-ssh-password shows which, and how to set up a key."]
    else:
        L += ["  The firewall denies inbound connections by default; ssh (22, keys only,",
              "  no passwords) and mDNS are open."]
    if desktop_fw:
        L += ["  This Desktop also opens every port above 1024."]
    L += [f"  Open a port: {S}pengyos-firewall allow PORT [--from CIDR]; see the rules:",
          "  pengyos-firewall status. A container's published port is closed to the",
          "  LAN until you open it."]
    if router_example:
        L += ["  To make this machine a router (NAT, DHCP + DNS with dnsmasq, WireGuard):",
              "  /usr/share/pengyos/examples/router/README.md. Its files go in /etc and",
              "  edit nothing the image owns."]
    if nm:
        L += ["  Networks are NetworkManager's (nmcli, or the panel)."]
    elif wpa:
        L += ["  Wi-Fi is wpa_supplicant + systemd-networkd, not NetworkManager: there",
              "  is no nmcli. Change networks with `sudo wpa_cli -i IFACE`."]
    L += [""]

    tools = shipped_tools(target)
    if tools:
        L += ["COMMANDS ON THIS MACHINE", ""]
        w = max(len(t) for t in tools) + 2
        for t in tools:
            L.append(f"  {t.ljust(w)}{TOOLS.get(t, '')}".rstrip())
        L += [""]

    L += ["DELIBERATELY ABSENT", ""]
    where = {
        "elsewhere": ("in the dev guest or a container" if guest and podman
                      else "in a container" if podman else "on another machine"),
        "container": "in a container" if podman else "on another machine",
        "docker": ": podman is rootless and takes the same commands" if podman else "",
    }
    for names, why in ABSENT:
        missing = [n for n in names if not real(target, n)]
        if missing:
            L += [f"  {', '.join(missing)}", f"      {why.format(**where)}"]
    st = stubs(target)
    if st:
        L += ["", "  These names print what to use instead (and exit 127):"]
        L += wrap_list(st, "    ")
    L += [""]

    L += ["WHAT IS INSTALLED", ""]
    if pkgs:
        L += [f"  {len(pkgs)} packages, one per line with its version: {PACKAGES}"]
    sbom = sorted(p.name for p in (target / "etc/pengyos").glob("*.spdx.json")) if (target / "etc/pengyos").is_dir() else []
    if sbom:
        L += [f"  The full bill of materials (SPDX): /etc/pengyos/{sbom[0]}"]
    L += ["  The build configuration: /etc/pengyos/buildroot.config" if has(target, "etc/pengyos/buildroot.config")
          else "  The edition and version: /etc/os-release"]
    L += ["", "MORE", "",
          "  https://pengyos.catbee.ca  the site and the release notes"]
    if has(target, "etc/skel/skills/pengyos/pengyos_skill.md"):
        L += ["  ~/skills/pengyos/         the longer version Pengy reads (from /etc/skel)"]
    return "\n".join(L) + "\n"


def put(path: Path, text: str) -> None:
    """Write by rename, never in place: a hard-linked tree keeps its other copy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".pengyos-txt.tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def write_motd(target: Path, line: str) -> None:
    motd = target / "etc/motd"
    old = motd.read_text().splitlines() if motd.is_file() else []
    # Drop the overlay's own header and an earlier pass's nudge; keep the rest.
    lead = []
    while old and old[0].strip() == "":
        lead.append(old.pop(0))
    if old and old[0].startswith("PengyOS "):
        old.pop(0)
    put(motd, "\n".join(lead + [line] + old) + "\n")


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    target = Path(argv[1])
    build_dir = Path(argv[2]) if len(argv) == 3 and argv[2] else None
    rel = os_release(target)
    pkgs = packages(build_dir)

    put(target / README.lstrip("/"), render(target, pkgs))
    if pkgs:
        w = max(len(n) for n, _ in pkgs) + 2
        put(target / PACKAGES.lstrip("/"), "".join(f"{n.ljust(w)}{v}\n" for n, v in pkgs))
    nudge = f"PengyOS {rel.get('VARIANT', '')} {rel.get('IMAGE_VERSION', '')} -- how this system works: {README}"
    write_motd(target, nudge)
    print(f"post-build: {README} ({len(pkgs)} packages listed) and the motd nudge")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
