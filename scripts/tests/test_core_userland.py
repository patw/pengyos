"""Tests for the Core/Server userland wiring (SPEC §2.1: Rust coreutils, real tools).

Why these checks exist. Buildroot resolves the BusyBox overlap by *install order*:
BUSYBOX_DEPENDENCIES lists coreutils, grep, tar, ... so those packages build after
BusyBox and their files win. Nothing in Buildroot knows about a Rust multicall
coreutils, so `ls`/`cp`/`cat` can come out of the image as BusyBox applets
depending on the build graph — and every applet no package claims stays a cut-down
applet (find without -printf, grep without -P/--include, sed without \b, awk
without gensub, xargs without -d, diff without -r, time -v, unzip -l, killall -q,
losetup -f, last -x, su -l). That is the "preboot environment" flavour an agent
hits on a normal install.

Up to 26.10.57 that overlap was resolved in the BusyBox config (fragments compiling
out every applet a real package owned). From 26.11.0 BusyBox is not built at all,
and this test is what keeps the userland whole without it:

1. the Core layer selects uutils (and not GNU coreutils), in every edition;
2. the recipe is wired (Config.in sourced, .mk, .hash) and its utility set covers
   every name GNU coreutils put in Core today;
3. BusyBox is off in every defconfig, and every command it ever answered is owned
   by a package the layers select, or is listed as deliberately dropped with why;
4. every symbol a layer sets exists in Buildroot 2026.08.1, or is declared by this
   external tree;
5. /bin/sh is bash.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "buildroot-external"
LAYER_CORE = EXT / "configs/layers/core.config"
LAYER_SERVER = EXT / "configs/layers/server.config"
LAYER_DESKTOP = EXT / "configs/layers/desktop.config"
CONFIG_IN = EXT / "Config.in"
CORE_BOARD = EXT / "board/core"
SERVER_BOARD = EXT / "board/server"
RECIPE = EXT / "package/uutils-coreutils"
POST_BUILD_CORE = CORE_BOARD / "post-build.sh"

DEFCONFIGS = ("pengyos_core_defconfig", "pengyos_server_defconfig",
              "pengyos_desktop_defconfig", "pengyos_server_nvidia_defconfig",
              "pengyos_desktop_nvidia_defconfig")

BR_SYMBOLS = Path(__file__).resolve().parent / "fixtures" / "buildroot-2026.08.1-symbols.txt"

# What GNU coreutils put in Core before the switch. uutils must ship every one of
# them, or the image loses a command it has always had.
GNU_CORE_IN_CORE = (
    "arch", "base32", "base64", "basename", "basenc", "cat", "chgrp", "chmod",
    "chown", "chroot", "cksum", "comm", "cp", "csplit", "cut", "date", "dd", "df",
    "dir", "dircolors", "dirname", "du", "echo", "env", "expand", "expr", "factor",
    "false", "fmt", "fold", "groups", "head", "hostid", "hostname", "id", "install",
    "join", "kill", "link", "ln", "logname", "ls", "md5sum", "mkdir", "mkfifo",
    "mknod", "mktemp", "more", "mv", "nice", "nl", "nohup", "nproc", "numfmt", "od",
    "paste", "pathchk", "pinky", "pr", "printenv", "printf", "ptx", "pwd",
    "readlink", "realpath", "rm", "rmdir", "seq", "sha1sum", "sha224sum",
    "sha256sum", "sha384sum", "sha512sum", "shred", "shuf", "sleep", "stat",
    "stdbuf", "stty", "sum", "sync", "tac", "tail", "tee", "test", "timeout",
    "touch", "tr", "true", "truncate", "tsort", "tty", "uname", "unexpand", "uniq",
    "unlink", "uptime", "users", "vdir", "wc", "who", "whoami", "yes",
)

# The BusyBox applets compiled out up to 26.10.57 (by their kconfig name) -> the
# package that owns the command. Kept as the record of what BusyBox once answered:
# each owner must still be selected, or a command has vanished.
APPLET_OWNER = {
    # uutils/coreutils (package/uutils-coreutils)
    "CAT": "BR2_PACKAGE_UUTILS_COREUTILS", "LS": "BR2_PACKAGE_UUTILS_COREUTILS",
    "CP": "BR2_PACKAGE_UUTILS_COREUTILS", "MV": "BR2_PACKAGE_UUTILS_COREUTILS",
    "RM": "BR2_PACKAGE_UUTILS_COREUTILS", "DD": "BR2_PACKAGE_UUTILS_COREUTILS",
    "DF": "BR2_PACKAGE_UUTILS_COREUTILS", "DU": "BR2_PACKAGE_UUTILS_COREUTILS",
    "SORT": "BR2_PACKAGE_UUTILS_COREUTILS", "TAIL": "BR2_PACKAGE_UUTILS_COREUTILS",
    "HEAD": "BR2_PACKAGE_UUTILS_COREUTILS", "TR": "BR2_PACKAGE_UUTILS_COREUTILS",
    "WC": "BR2_PACKAGE_UUTILS_COREUTILS", "SEQ": "BR2_PACKAGE_UUTILS_COREUTILS",
    "TOUCH": "BR2_PACKAGE_UUTILS_COREUTILS", "LN": "BR2_PACKAGE_UUTILS_COREUTILS",
    "MKDIR": "BR2_PACKAGE_UUTILS_COREUTILS", "RMDIR": "BR2_PACKAGE_UUTILS_COREUTILS",
    "ECHO": "BR2_PACKAGE_UUTILS_COREUTILS", "PRINTF": "BR2_PACKAGE_UUTILS_COREUTILS",
    "TEST": "BR2_PACKAGE_UUTILS_COREUTILS", "TRUE": "BR2_PACKAGE_UUTILS_COREUTILS",
    "FALSE": "BR2_PACKAGE_UUTILS_COREUTILS", "SLEEP": "BR2_PACKAGE_UUTILS_COREUTILS",
    "DATE": "BR2_PACKAGE_UUTILS_COREUTILS", "ENV": "BR2_PACKAGE_UUTILS_COREUTILS",
    "EXPR": "BR2_PACKAGE_UUTILS_COREUTILS", "UNIQ": "BR2_PACKAGE_UUTILS_COREUTILS",
    "UNLINK": "BR2_PACKAGE_UUTILS_COREUTILS", "TSORT": "BR2_PACKAGE_UUTILS_COREUTILS",
    "TRUNCATE": "BR2_PACKAGE_UUTILS_COREUTILS", "TTY": "BR2_PACKAGE_UUTILS_COREUTILS",
    "BB_ARCH": "BR2_PACKAGE_UUTILS_COREUTILS", "HOSTNAME": "BR2_PACKAGE_UUTILS_COREUTILS",
    "MORE": "BR2_PACKAGE_UUTILS_COREUTILS", "KILL": "BR2_PACKAGE_UUTILS_COREUTILS",
    "ID": "BR2_PACKAGE_UUTILS_COREUTILS", "WHO": "BR2_PACKAGE_UUTILS_COREUTILS",
    "UPTIME": "BR2_PACKAGE_UUTILS_COREUTILS", "WHOAMI": "BR2_PACKAGE_UUTILS_COREUTILS",
    "HOSTID": "BR2_PACKAGE_UUTILS_COREUTILS", "NICE": "BR2_PACKAGE_UUTILS_COREUTILS",
    "NOHUP": "BR2_PACKAGE_UUTILS_COREUTILS", "NPROC": "BR2_PACKAGE_UUTILS_COREUTILS",
    "CHGRP": "BR2_PACKAGE_UUTILS_COREUTILS", "CHMOD": "BR2_PACKAGE_UUTILS_COREUTILS",
    "CHOWN": "BR2_PACKAGE_UUTILS_COREUTILS", "CHROOT": "BR2_PACKAGE_UUTILS_COREUTILS",
    "INSTALL": "BR2_PACKAGE_UUTILS_COREUTILS", "MKFIFO": "BR2_PACKAGE_UUTILS_COREUTILS",
    "MKNOD": "BR2_PACKAGE_UUTILS_COREUTILS", "STTY": "BR2_PACKAGE_UUTILS_COREUTILS",
    "SYNC": "BR2_PACKAGE_UUTILS_COREUTILS", "LOGNAME": "BR2_PACKAGE_UUTILS_COREUTILS",
    "CKSUM": "BR2_PACKAGE_UUTILS_COREUTILS", "MD5SUM": "BR2_PACKAGE_UUTILS_COREUTILS",
    "SHA1SUM": "BR2_PACKAGE_UUTILS_COREUTILS", "SHA256SUM": "BR2_PACKAGE_UUTILS_COREUTILS",
    "SHA384SUM": "BR2_PACKAGE_UUTILS_COREUTILS", "SHA512SUM": "BR2_PACKAGE_UUTILS_COREUTILS",
    "SHRED": "BR2_PACKAGE_UUTILS_COREUTILS", "OD": "BR2_PACKAGE_UUTILS_COREUTILS",
    "PASTE": "BR2_PACKAGE_UUTILS_COREUTILS", "CUT": "BR2_PACKAGE_UUTILS_COREUTILS",
    "NL": "BR2_PACKAGE_UUTILS_COREUTILS", "FOLD": "BR2_PACKAGE_UUTILS_COREUTILS",
    "FACTOR": "BR2_PACKAGE_UUTILS_COREUTILS", "BASE32": "BR2_PACKAGE_UUTILS_COREUTILS",
    "BASE64": "BR2_PACKAGE_UUTILS_COREUTILS", "BASENAME": "BR2_PACKAGE_UUTILS_COREUTILS",
    "DIRNAME": "BR2_PACKAGE_UUTILS_COREUTILS", "READLINK": "BR2_PACKAGE_UUTILS_COREUTILS",
    "REALPATH": "BR2_PACKAGE_UUTILS_COREUTILS", "MKTEMP": "BR2_PACKAGE_UUTILS_COREUTILS",
    "PWD": "BR2_PACKAGE_UUTILS_COREUTILS", "PRINTENV": "BR2_PACKAGE_UUTILS_COREUTILS",
    "YES": "BR2_PACKAGE_UUTILS_COREUTILS", "LINK": "BR2_PACKAGE_UUTILS_COREUTILS",
    "TEE": "BR2_PACKAGE_UUTILS_COREUTILS",
    # findutils / grep / sed / gawk / time / which / diffutils / patch
    "FIND": "BR2_PACKAGE_UUTILS_FINDUTILS", "XARGS": "BR2_PACKAGE_UUTILS_FINDUTILS",
    "GREP": "BR2_PACKAGE_GREP", "EGREP": "BR2_PACKAGE_GREP", "FGREP": "BR2_PACKAGE_GREP",
    "SED": "BR2_PACKAGE_SED", "AWK": "BR2_PACKAGE_GAWK", "TIME": "BR2_PACKAGE_TIME",
    "WHICH": "BR2_PACKAGE_WHICH", "DIFF": "BR2_PACKAGE_DIFFUTILS",
    "CMP": "BR2_PACKAGE_DIFFUTILS", "PATCH": "BR2_PACKAGE_PATCH",
    # archives
    "UNZIP": "BR2_PACKAGE_UNZIP", "TAR": "BR2_PACKAGE_TAR", "GZIP": "BR2_PACKAGE_GZIP",
    "GUNZIP": "BR2_PACKAGE_GZIP", "ZCAT": "BR2_PACKAGE_GZIP",
    "BUNZIP2": "BR2_PACKAGE_GZIP", "BZCAT": "BR2_PACKAGE_GZIP",
    # viewers / inspection
    "LSOF": "BR2_PACKAGE_LSOF", "TREE": "BR2_PACKAGE_TREE", "LESS": "BR2_PACKAGE_LESS",
    "GETFATTR": "BR2_PACKAGE_ATTR", "SETFATTR": "BR2_PACKAGE_ATTR",
    "CHATTR": "BR2_PACKAGE_E2FSPROGS", "LSATTR": "BR2_PACKAGE_E2FSPROGS",
    "KILLALL": "BR2_PACKAGE_PSMISC", "KILLALL5": "BR2_PACKAGE_PSMISC",
    "FUSER": "BR2_PACKAGE_PSMISC",
    # util-linux
    "MOUNT": "BR2_PACKAGE_UTIL_LINUX_MOUNT", "UMOUNT": "BR2_PACKAGE_UTIL_LINUX_MOUNT",
    "FSCK": "BR2_PACKAGE_UTIL_LINUX_FSCK", "LOSETUP": "BR2_PACKAGE_UTIL_LINUX_LOSETUP",
    "LAST": "BR2_PACKAGE_UTIL_LINUX_LAST", "EJECT": "BR2_PACKAGE_UTIL_LINUX_EJECT",
    "SU": "BR2_PACKAGE_UTIL_LINUX_SU", "MESG": "BR2_PACKAGE_UTIL_LINUX_MESG",
    "HWCLOCK": "BR2_PACKAGE_UTIL_LINUX_HWCLOCK", "CHRT": "BR2_PACKAGE_UTIL_LINUX_SCHEDUTILS",
    "FALLOCATE": "BR2_PACKAGE_UTIL_LINUX_FALLOCATE",
    "MOUNTPOINT": "BR2_PACKAGE_UTIL_LINUX_MOUNTPOINT",
    "LOGGER": "BR2_PACKAGE_UTIL_LINUX_LOGGER",
    "DMESG": "BR2_PACKAGE_UTIL_LINUX_BINARIES", "RENICE": "BR2_PACKAGE_UTIL_LINUX_BINARIES",
    "SETSID": "BR2_PACKAGE_UTIL_LINUX_BINARIES", "GETOPT": "BR2_PACKAGE_UTIL_LINUX_BINARIES",
    "FDISK": "BR2_PACKAGE_UTIL_LINUX_BINARIES", "BLKID": "BR2_PACKAGE_UTIL_LINUX_BINARIES",
    "LSBLK": "BR2_PACKAGE_UTIL_LINUX_BINARIES", "FLOCK": "BR2_PACKAGE_UTIL_LINUX_BINARIES",
    "FSFREEZE": "BR2_PACKAGE_UTIL_LINUX_BINARIES", "FSTRIM": "BR2_PACKAGE_UTIL_LINUX_BINARIES",
    "IPCRM": "BR2_PACKAGE_UTIL_LINUX_BINARIES", "IPCS": "BR2_PACKAGE_UTIL_LINUX_BINARIES",
    "SETARCH": "BR2_PACKAGE_UTIL_LINUX_BINARIES", "UUIDGEN": "BR2_PACKAGE_UTIL_LINUX_BINARIES",
    "READPROFILE": "BR2_PACKAGE_UTIL_LINUX_BINARIES",
    "VOLUMEID": "BR2_PACKAGE_UTIL_LINUX_BINARIES",
    "MKSWAP": "BR2_PACKAGE_UTIL_LINUX_BINARIES", "SWAPON": "BR2_PACKAGE_UTIL_LINUX_BINARIES",
    "SWAPOFF": "BR2_PACKAGE_UTIL_LINUX_BINARIES",
    # procps-ng / psmisc / kmod
    "PS": "BR2_PACKAGE_PROCPS_NG", "TOP": "BR2_PACKAGE_PROCPS_NG",
    "FREE": "BR2_PACKAGE_PROCPS_NG", "PIDOF": "BR2_PACKAGE_PROCPS_NG",
    "VMSTAT": "BR2_PACKAGE_PROCPS_NG", "WATCH": "BR2_PACKAGE_PROCPS_NG",
    "BB_SYSCTL": "BR2_PACKAGE_PROCPS_NG",
    "MODPROBE": "BR2_PACKAGE_KMOD", "INSMOD": "BR2_PACKAGE_KMOD",
    "LSMOD": "BR2_PACKAGE_KMOD", "RMMOD": "BR2_PACKAGE_KMOD",
    # Server: net-tools / iputils / traceroute / pciutils / usbutils / hdparm
    "NETSTAT": "BR2_PACKAGE_NET_TOOLS", "IFCONFIG": "BR2_PACKAGE_NET_TOOLS",
    
    "ROUTE": "BR2_PACKAGE_NET_TOOLS", "ARP": "BR2_PACKAGE_NET_TOOLS",
    "ARPING": "BR2_PACKAGE_IPUTILS", "PING": "BR2_PACKAGE_IPUTILS",
    "TRACEROUTE": "BR2_PACKAGE_TRACEROUTE", "LSPCI": "BR2_PACKAGE_PCIUTILS",
    "LSUSB": "BR2_PACKAGE_USBUTILS", "HDPARM": "BR2_PACKAGE_HDPARM",
    "IPADDR": "BR2_PACKAGE_IPROUTE2", "IPLINK": "BR2_PACKAGE_IPROUTE2",
    "IPROUTE": "BR2_PACKAGE_IPROUTE2", "IPRULE": "BR2_PACKAGE_IPROUTE2",
    "IPNEIGH": "BR2_PACKAGE_IPROUTE2", "IPTUNNEL": "BR2_PACKAGE_IPROUTE2",
    # 2026-11-03: Vim, and the applets a real tool already shadowed in every edition
    "VI": "BR2_PACKAGE_VIM", "XXD": "BR2_PACKAGE_VIM", "UNAME": "BR2_PACKAGE_UUTILS_COREUTILS",
    "HALT": "BR2_INIT_SYSTEMD", "POWEROFF": "BR2_INIT_SYSTEMD",
    "REBOOT": "BR2_INIT_SYSTEMD", "INIT": "BR2_INIT_SYSTEMD",
    "SULOGIN": "BR2_PACKAGE_UTIL_LINUX_BINARIES", "HEXDUMP": "BR2_PACKAGE_UTIL_LINUX_BINARIES",
    "LINUX32": "BR2_PACKAGE_UTIL_LINUX_BINARIES", "LINUX64": "BR2_PACKAGE_UTIL_LINUX_BINARIES",
    "CHVT": "BR2_PACKAGE_KBD", "OPENVT": "BR2_PACKAGE_KBD", "DEALLOCVT": "BR2_PACKAGE_KBD",
    "W": "BR2_PACKAGE_PROCPS_NG", "IP": "BR2_PACKAGE_IPROUTE2",
    "MKE2FS": "BR2_PACKAGE_E2FSPROGS", "MKDOSFS": "BR2_PACKAGE_DOSFSTOOLS_MKFS_FAT",
    "XZ": "BR2_PACKAGE_XZ", "XZCAT": "BR2_PACKAGE_XZ", "UNXZ": "BR2_PACKAGE_XZ",
    "LZMA": "BR2_PACKAGE_XZ", "LZCAT": "BR2_PACKAGE_XZ", "UNLZMA": "BR2_PACKAGE_XZ",
}


def _fragment_names(path: Path) -> list[tuple[str, str]]:
    """Every CONFIG_ symbol a fragment names, as ``(name, "on"|"off")``."""
    out = []
    for line in path.read_text().splitlines():
        m = re.match(r"#\s*CONFIG_([A-Za-z0-9_]+) is not set", line)
        if m:
            out.append((m.group(1), "off"))
            continue
        m = re.match(r"CONFIG_([A-Za-z0-9_]+)=y", line)
        if m:
            out.append((m.group(1), "on"))
    return out


def _layer_symbols(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^(BR2_[A-Z0-9_]+)\s*=", text, re.M)]


def _external_symbols() -> set[str]:
    """Symbols this external tree declares itself (its own Config.in files).

    `menuconfig` counts as well as `config`: a package whose main symbol opens a
    submenu is declared with `menuconfig`, and Buildroot defines it exactly the
    same way. Missing it makes the layer-symbol test fail a legitimate fork -
    package/ffmpeg-pengyos declares `menuconfig BR2_PACKAGE_FFMPEG_PENGYOS`, the
    same shape Buildroot's own package/ffmpeg/Config.in uses.
    """
    out: set[str] = set()
    for cin in EXT.rglob("Config.in*"):
        for m in re.finditer(r"^\s*(?:menu)?config\s+(BR2_[A-Z0-9_]+)\s*$", cin.read_text(), re.M):
            out.add(m.group(1))
    return out


# --- 1. the Core layer's coreutils is Rust -----------------------------------
def test_core_layer_selects_uutils_and_not_gnu_coreutils():
    text = LAYER_CORE.read_text()
    assert "BR2_PACKAGE_UUTILS_COREUTILS=y" in text, "Core must select the Rust coreutils"
    assert not re.search(r"^\s*BR2_PACKAGE_COREUTILS=y", text, re.M), (
        "GNU coreutils and uutils both claim ls/cp/cat; exactly one may be selected"
    )


def test_every_defconfig_carries_the_rust_coreutils():
    for cfg in DEFCONFIGS:
        text = (EXT / "configs" / cfg).read_text()
        assert "BR2_PACKAGE_UUTILS_COREUTILS=y" in text, f"{cfg} lost the Rust coreutils"
        assert "BR2_PACKAGE_COREUTILS=y" not in text, f"{cfg} selects GNU coreutils too"


def test_recipe_is_wired_into_the_external_tree():
    for name in ("uutils-coreutils.mk", "Config.in", "uutils-coreutils.hash"):
        assert (RECIPE / name).is_file(), f"missing recipe file {name}"
    assert "package/uutils-coreutils/Config.in" in CONFIG_IN.read_text(), (
        "Config.in must source the recipe or its symbol does not exist"
    )
    mk = (RECIPE / "uutils-coreutils.mk").read_text()
    assert re.search(r"^UUTILS_COREUTILS_VERSION\s*=\s*\d+\.\d+\.\d+$", mk, re.M), (
        "the recipe must pin a release"
    )
    digest = [ln.split() for ln in (RECIPE / "uutils-coreutils.hash").read_text().splitlines()
              if ln.strip() and not ln.startswith("#")]
    assert digest, "the recipe needs a hash line"
    # Buildroot's spelling: sha256  <hash>  <filename>
    assert digest[0][0] == "sha256" and re.fullmatch(r"[0-9a-f]{64}", digest[0][1]), (
        "the recipe needs a 'sha256  <64-hex>  <tarball>' line "
        "(check-hash parses algorithm/hash/filename; a bare sha256sum line never matches)"
    )
    version = re.search(r"^UUTILS_COREUTILS_VERSION\s*=\s*(\S+)", mk, re.M).group(1)
    assert digest[0][2] == f"coreutils-{version}.tar.gz", (
        "the hashed filename must track the pinned SOURCE"
    )


def test_recipe_ships_every_core_utility_the_image_already_had():
    mk = (RECIPE / "uutils-coreutils.mk").read_text()
    start = next(i for i, l in enumerate(mk.splitlines())
                 if l.startswith("UUTILS_COREUTILS_UTILITIES"))
    names: set[str] = set()
    for raw in mk.splitlines()[start:]:
        line = raw.split("=", 1)[1] if raw.startswith("UUTILS_COREUTILS_UTILITIES") else raw
        names |= set(line.rstrip()[:-1].split() if line.rstrip().endswith("\\") else line.split())
        if not line.rstrip().endswith("\\"):
            break
    missing = sorted(set(GNU_CORE_IN_CORE) - names)
    assert not missing, f"uutils must ship every utility GNU coreutils put in Core: {missing}"


# --- 2. no BusyBox, and nothing it answered is lost ----------------------------
# The applets BusyBox still built in the last image that had it (26.10.57 plus the
# Vim step), by command name -> the package that answers that name since 26.11.0.
# "STUB:<file>" means the edition has no such tool and board/core/tool-stubs says
# what to use; on the editions that do (dig/host/nslookup: Server) the package is
# named too.
LAST_APPLETS = {
    "[": "BR2_PACKAGE_UUTILS_COREUTILS",
    "sh": "BR2_PACKAGE_BASH",
    "login": "BR2_PACKAGE_SHADOW", "passwd": "BR2_PACKAGE_SHADOW",
    "nologin": "BR2_PACKAGE_SHADOW",
    "adduser": "OVERLAY:usr/bin/adduser", "addgroup": "OVERLAY:usr/bin/addgroup",
    "deluser": "OVERLAY:usr/bin/deluser", "delgroup": "OVERLAY:usr/bin/delgroup",
    "setpriv": "BR2_PACKAGE_UTIL_LINUX_SETPRIV",
    "ping": "BR2_PACKAGE_IPUTILS", "arping": "BR2_PACKAGE_IPUTILS",
    "traceroute": "BR2_PACKAGE_TRACEROUTE",
    "netstat": "BR2_PACKAGE_NET_TOOLS", "ifconfig": "BR2_PACKAGE_NET_TOOLS",
    "route": "BR2_PACKAGE_NET_TOOLS", "arp": "BR2_PACKAGE_NET_TOOLS",
    "iptunnel": "BR2_PACKAGE_NET_TOOLS",
    "dnsdomainname": "BR2_PACKAGE_UUTILS_COREUTILS",  # `hostname -d`, post-build.sh
    "wget": "BR2_PACKAGE_WGET",
    "nslookup": "STUB:pengyos-no-dnstools",
    "lspci": "BR2_PACKAGE_PCIUTILS", "lsusb": "BR2_PACKAGE_USBUTILS",
    "hdparm": "BR2_PACKAGE_HDPARM",
    "strings": "BR2_PACKAGE_BINUTILS_TARGET", "ar": "BR2_PACKAGE_BINUTILS_TARGET",
    "bc": "BR2_PACKAGE_BC", "dc": "BR2_PACKAGE_BC",
    "cpio": "BR2_PACKAGE_CPIO",
    "dos2unix": "BR2_PACKAGE_DOS2UNIX", "unix2dos": "BR2_PACKAGE_DOS2UNIX",
    "clear": "BR2_PACKAGE_NCURSES_TARGET_PROGS", "reset": "BR2_PACKAGE_NCURSES_TARGET_PROGS",
    "mkpasswd": "BR2_PACKAGE_WHOIS",
    "run-parts": "BR2_PACKAGE_DEBIANUTILS",
}
# Applets with no successor, and why (approved 2026-11-03).
LAST_APPLETS_DROPPED = {
    # systemd runs and supervises services; the hardware watchdog is systemd's
    # RuntimeWatchdogSec.
    "start-stop-daemon", "watchdog",
    # Embedded/console leftovers: serial terminal, framebuffer, tiny sleeps,
    # character tables, a hex editor (vim + xxd), SCSI listing (lsblk -S),
    # terminal sizing (bash/ncurses), the [[ builtin is bash's, ash is bash.
    "microcom", "fbset", "mim", "usleep", "ascii", "hexedit", "lsscsi", "resize",
    "[[", "ash",
    # lzop does both: lzop -d / lzop -dc.
    "lzopcat", "unlzop",
    # No sharutils/moreutils recipe in Buildroot: base64 (uutils) and python3 cover
    # uuencode; ts has no packaged successor.
    "uuencode", "uudecode", "ts",
    # ifupdown is gone (networkd/NetworkManager own the interfaces; see below).
    "ifup", "ifdown",
    # BusyBox-only shorthands for `ip addr`, `ip link`, ... (iproute2's `ip`).
    "ipaddr", "iplink", "ipneigh", "iproute", "iprule",
}


def _layers_text() -> str:
    return LAYER_CORE.read_text() + LAYER_SERVER.read_text() + LAYER_DESKTOP.read_text()


def _enabled(text: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"^(BR2_[A-Z0-9_]+)=y", text, re.M)}


def test_busybox_is_not_built_in_any_edition():
    for cfg in DEFCONFIGS:
        text = (EXT / "configs" / cfg).read_text()
        assert re.search(r"^# BR2_PACKAGE_BUSYBOX is not set$", text, re.M), (
            f"{cfg} must turn BusyBox off (Buildroot defaults it on)")
        assert "BR2_PACKAGE_BUSYBOX_CONFIG_FRAGMENT_FILES" not in text, cfg
        assert "BR2_SYSTEM_BIN_SH_BASH=y" in text, f"{cfg}: /bin/sh must be bash"
    assert not list(EXT.rglob("busybox.fragment")), "a BusyBox fragment with no BusyBox"


def test_every_last_applet_has_a_successor_or_a_reason():
    core = _enabled(LAYER_CORE.read_text())
    assert not set(LAST_APPLETS) & LAST_APPLETS_DROPPED
    for name, owner in LAST_APPLETS.items():
        if owner.startswith("OVERLAY:"):
            path = CORE_BOARD / "rootfs-overlay" / owner.split(":", 1)[1]
            assert path.exists(), f"{name}: {path} missing"
        elif owner.startswith("STUB:"):
            stub = CORE_BOARD / "tool-stubs" / name
            assert stub.is_symlink() and stub.resolve().name == owner.split(":", 1)[1], name
        else:
            assert owner in core, f"{name}: Core must select {owner} (every edition needs it)"


def test_dns_tools_on_the_server_and_a_pointer_on_core():
    server = _enabled(LAYER_SERVER.read_text())
    assert {"BR2_PACKAGE_BIND", "BR2_PACKAGE_BIND_TOOLS"} <= server
    assert "BR2_PACKAGE_BIND_SERVER=y" not in _layers_text(), "tools only, no named"
    for name in ("dig", "host", "nslookup"):
        assert (CORE_BOARD / "tool-stubs" / name).is_symlink(), name


def test_every_applet_compiled_out_before_still_has_its_owner():
    """The record of 26.10.x: each command a real package took over from BusyBox
    must still come from that package."""
    enabled = _enabled(_layers_text())
    for applet, owner in APPLET_OWNER.items():
        assert owner in enabled, f"{applet}: {owner} is no longer selected in any layer"


def test_binutils_brings_no_linker():
    """strings/ar came with binutils; ld/as must stay the no-toolchain stubs."""
    mk = (EXT / "external.mk").read_text()
    assert "BINUTILS_POST_INSTALL_TARGET_HOOKS += PENGYOS_BINUTILS_NO_LINKER" in mk
    for tool in ("usr/bin/ld ", "usr/bin/as", "usr/bin/ld.bfd"):
        assert tool in mk, tool
    for stub in ("ld", "as"):
        assert (CORE_BOARD / "tool-stubs" / stub).is_symlink(), stub


def test_debianutils_does_not_replace_gnu_which():
    mk = (EXT / "external.mk").read_text()
    m = re.search(r"define DEBIANUTILS_INSTALL_TARGET_CMDS\n(.*?)endef", mk, re.S)
    assert m, "debianutils' install must be written out (its make install ships `which`)"
    assert "run-parts" in m.group(1) and "which" not in m.group(1)


def test_hostname_has_one_owner():
    """net-tools and uutils both install hostname; install order picked one, and
    dnsdomainname pointed at a binary that did not know the name (Server 26.10.x)."""
    text = POST_BUILD_CORE.read_text()
    assert 'ln -sf coreutils "${TARGET_DIR}/usr/bin/hostname"' in text
    assert "exec hostname -d" in text


def test_incremental_trees_lose_busybox_too():
    text = POST_BUILD_CORE.read_text()
    assert 'rm -f "${TARGET_DIR}/bin/busybox"' in text
    assert "removed leftover BusyBox link" in text


def test_the_tools_agents_reach_for_are_the_real_packages():
    core = LAYER_CORE.read_text()
    for sym in ("BR2_PACKAGE_UUTILS_FINDUTILS", "BR2_PACKAGE_GREP", "BR2_PACKAGE_SED",
                "BR2_PACKAGE_GAWK", "BR2_PACKAGE_TIME", "BR2_PACKAGE_WHICH",
                "BR2_PACKAGE_DIFFUTILS", "BR2_PACKAGE_PATCH", "BR2_PACKAGE_UNZIP",
                "BR2_PACKAGE_LSOF", "BR2_PACKAGE_TREE", "BR2_PACKAGE_UTIL_LINUX_LOSETUP",
                "BR2_PACKAGE_UTIL_LINUX_LAST", "BR2_PACKAGE_UTIL_LINUX_EJECT",
                "BR2_PACKAGE_UTIL_LINUX_SU", "BR2_PACKAGE_PSMISC", "BR2_PACKAGE_VIM",
                "BR2_PACKAGE_LIBCURL_CURL", "BR2_PACKAGE_WGET"):
        assert f"{sym}=y" in core, f"Core must select {sym}"


# --- 3. nothing in the layers is a made-up symbol ----------------------------
def test_every_layer_symbol_resolves():
    """Buildroot ignores an unknown BR2_ name in a defconfig — silently. Check
    every symbol the layers set against the pinned 2026.08.1 list, plus the symbols
    this external tree declares in its own Config.in files."""
    known = {ln.strip() for ln in BR_SYMBOLS.read_text().splitlines()
             if ln.strip() and not ln.startswith("#")}
    external = _external_symbols()
    for layer in (LAYER_CORE, LAYER_SERVER, LAYER_DESKTOP):
        unknown = [s for s in _layer_symbols(layer.read_text())
                   if s not in known and s not in external]
        assert not unknown, f"{layer.name} sets symbols Buildroot 2026.08.1 does not define: {unknown}"


# --- 4. /bin/sh is bash ------------------------------------------------------
def test_core_post_build_points_bin_sh_at_bash():
    text = POST_BUILD_CORE.read_text()
    assert re.search(r"ln\s+-sf\s+bash\s+\"\$\{TARGET_DIR\}/usr/bin/sh\"", text), (
        "/bin/sh must be bash: agents write bash-isms into `sh -c`"
    )


def test_no_unit_is_left_calling_the_dropped_ifup():
    """ifupdown-scripts' network.service runs `ip addr flush dev lo; ifup -a`.
    With BusyBox's ifup compiled out it flushed 127.0.0.1 and failed, so the
    machine had no localhost. With BusyBox gone nothing provides ifup at all."""
    core = LAYER_CORE.read_text()
    assert re.search(r"^# BR2_PACKAGE_IFUPDOWN_SCRIPTS is not set$", core, re.M), (
        "core layer must turn ifupdown-scripts off (its network.service needs ifup)"
    )
    assert "BR2_PACKAGE_IFUPDOWN=y" not in core
    for cfg in DEFCONFIGS:
        text = (EXT / "configs" / cfg).read_text()
        assert "BR2_PACKAGE_IFUPDOWN_SCRIPTS=y" not in text, cfg
