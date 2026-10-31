"""The host-built Desktop apps must be wired in all three places at once.

WHY
---
A host-built libcosmic app reaches an image through three separate files, and
`board/desktop/post-build.sh` *guards* its copy (`if [ -x … ]`) so a missing
build is a warning, not an error:

    scripts/build-cosmic.sh            the app list it runs cargo for
    board/desktop/post-build.sh        the install line that copies it in
    rootfs-overlay/usr/share/applications/<id>.desktop   the launcher

Miss one and the image builds green and boots green with an app that is simply
not there — the exact failure the guard was written to make visible in the
build log, which nobody reads. This test reads the three files and requires them
to agree, with no build and no cargo.

It also checks the launcher's `Exec=` binary: a `.desktop` whose program no
post-build step installs is a launcher that fails at click time.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD_COSMIC = ROOT / "scripts" / "build-cosmic.sh"
POST_BUILD = ROOT / "buildroot-external" / "board" / "desktop" / "post-build.sh"
APPS_DIR = (
    ROOT / "buildroot-external" / "board" / "desktop" / "rootfs-overlay"
    / "usr" / "share" / "applications"
)
# Every layer's overlay, in the order a Desktop build stacks them (core,
# server, desktop). An overlay file lands on the target verbatim, so a program
# in an overlay's usr/bin is as installed as one a post-build copies in.
OVERLAY_BIN_DIRS = [
    ROOT / "buildroot-external" / "board" / board / "rootfs-overlay" / "usr" / "bin"
    for board in ("core", "server", "desktop")
]

# Launchers that point at something other than a PengyOS app binary. (The old
# Printers entry, pengyos-printers.desktop, opened CUPS' web pages through
# xdg-open; the Printers app replaced it.)
NON_APP_LAUNCHERS: set[str] = set()


def _app_list() -> list[str]:
    """The apps `build-cosmic.sh` builds into $PENGYOS_COSMIC_BUILD/install-gui."""
    text = BUILD_COSMIC.read_text(encoding="utf-8")
    m = re.search(r"for app in ([^;]+); do", text)
    assert m, "build-cosmic.sh: no `for app in …; do` app list found"
    return m.group(1).split()


def _installed_bins() -> set[str]:
    """Every /usr/bin/<name> the Desktop post-build installs.

    A name built from a loop variable (the games' symlinks:
    `for game in a b; do ln … "${TARGET_DIR}/usr/bin/pengy-${game}"; done`) is
    expanded over the loop's list, so each symlink counts as installed.
    """
    text = POST_BUILD.read_text(encoding="utf-8")
    out = set()
    for m in re.finditer(r"\$\{TARGET_DIR\}/usr/bin/([\w.-]+)(\$\{(\w+)\})?", text):
        prefix, var = m.group(1), m.group(3)
        if var is None:
            out.add(prefix)
            continue
        loop = None
        for lm in re.finditer(r"for " + var + r" in ([^;]+); do", text[: m.start()]):
            loop = lm
        assert loop, f"post-build.sh: ${{{var}}} in /usr/bin/{prefix}${{{var}}} is not a loop variable"
        out.update(prefix + item for item in loop.group(1).split())
    return out


def _overlay_bins() -> set[str]:
    """Every /usr/bin/<name> a rootfs-overlay ships.

    `BR2_ROOTFS_OVERLAY` is copied with `rsync -a` after the packages, so an
    overlay file is installed by the image, exactly like a post-build `install`
    line. The launcher check used to see only post-build copies, which made a
    launcher running an overlay program (`open`, `pengyos-autologin`) look
    broken.
    """
    out = set()
    for d in OVERLAY_BIN_DIRS:
        if d.is_dir():
            out.update(p.name for p in d.iterdir() if p.is_file() or p.is_symlink())
    return out


def _launchers() -> dict[str, str]:
    """launcher file name -> its Exec= program (first word)."""
    out: dict[str, str] = {}
    for path in sorted(APPS_DIR.glob("*.desktop")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("Exec="):
                prog = line[len("Exec="):].split()[0]
                out[path.name] = prog.split("/")[-1]
                break
        else:
            raise AssertionError(f"{path.name}: no Exec= line")
    return out


def test_apps_are_built_and_installed():
    installed = _installed_bins()
    launchers = _launchers()
    for app in _app_list():
        assert app in installed, (
            f"{app} is in build-cosmic.sh's app list but post-build.sh never "
            f"installs it — the image would ship without it"
        )
        # A launcher is not required: the graphical installer is started by
        # pengyos-live-installer.service on the live medium, not from the
        # Applications list, so it has no .desktop file by design.
        for f, prog in launchers.items():
            if prog == app:
                assert f.startswith("io.catbee.") or f.startswith("com.system76."), (
                    f"{f} runs {app} but is not named after an app id"
                )


def test_the_desktop_apps_have_launchers():
    """The apps that are meant to be opened from the Applications list."""
    programs = set(_launchers().values())
    for app in ("pengyos-disks", "pengyos-firmware", "pengyos-gamepads", "pengyos-printers",
                "pengyos-sharing", "pengyos-updates"):
        assert app in programs, f"{app} is installed but no .desktop launcher runs it"


def test_every_launcher_runs_an_installed_program():
    installed = _installed_bins() | _overlay_bins()
    # Programs the image ships from Buildroot recipes, not from post-build.sh.
    for name, prog in _launchers().items():
        if name in NON_APP_LAUNCHERS:
            continue
        assert prog in installed, (
            f"{name} runs `{prog}`, which no post-build step installs and no "
            "rootfs-overlay ships"
        )


def test_the_overlay_bins_are_counted_as_installed():
    """The fix, with teeth: `open` is an overlay file, not a post-build copy.

    Asserting it by name keeps this from being quietly reverted to
    `_installed_bins()` alone, which is what made a launcher over an overlay
    program look like a broken launcher.
    """
    assert "open" in _overlay_bins()
    assert "open" not in _installed_bins()
    assert "open" in _installed_bins() | _overlay_bins()


def test_the_gamepads_app_is_wired_end_to_end():
    """The app this change adds, named: the three files plus its group and rule."""
    assert "pengyos-gamepads" in _app_list()
    assert "pengyos-gamepads" in _installed_bins()
    assert "io.catbee.pengyos.Gamepads.desktop" in _launchers()
    users = (ROOT / "buildroot-external" / "board" / "desktop" / "users.table").read_text()
    assert re.search(r"^-\s+-1\s+input\s+-1", users, re.M), (
        "the image must define the `input` group: udev gives /dev/input/event* "
        "to it at 0660, and without the group a user cannot read a controller"
    )
    frag = (ROOT / "buildroot-external" / "board" / "desktop" / "linux.fragment").read_text()
    # Rumble goes through the event node (EVIOCSFF + an EV_FF write); the
    # pad drivers select INPUT_FF_MEMLESS themselves. There is no
    # CONFIG_INPUT_FF_MEMSET: a line for it was once added here and kconfig
    # dropped it silently.
    assert "INPUT_FF_MEMSET" not in frag, "CONFIG_INPUT_FF_MEMSET does not exist"
    assert "CONFIG_INPUT_JOYDEV=m" in frag
    overlay = ROOT / "buildroot-external" / "board" / "desktop" / "rootfs-overlay"
    assert (overlay / "usr/lib/modules-load.d/pengyos-joydev.conf").is_file(), (
        "joydev must load at boot so legacy games find /dev/input/jsN"
    )
    rules = (overlay / "usr/lib/udev/rules.d/90-pengyos-joystick.rules").read_text()
    assert 'KERNEL=="js[0-9]*"' in rules and 'GROUP="input"' in rules


def test_the_printers_app_is_wired_end_to_end():
    """Printers replaced a launcher that opened CUPS' web pages: the app must be
    built, installed and launched, the old launcher gone from the overlay *and*
    deleted from existing build trees (Buildroot never removes target files, so
    a tree that shipped it would keep listing two "Printers"), and wheel must
    stay a CUPS admin group, or the app could not add a printer."""
    assert "pengyos-printers" in _app_list()
    assert "pengyos-printers" in _installed_bins()
    launchers = _launchers()
    assert launchers.get("io.catbee.pengyos.Printers.desktop") == "pengyos-printers"
    assert "pengyos-printers.desktop" not in launchers
    desktop = (APPS_DIR / "io.catbee.pengyos.Printers.desktop").read_text()
    assert "Exec=pengyos-printers --add" in desktop, "the launcher's Add a printer action"
    post = POST_BUILD.read_text(encoding="utf-8")
    assert 'rm -f "${TARGET_DIR}/usr/share/applications/pengyos-printers.desktop"' in post
    assert re.search(r"SystemGroup lpadmin sys root wheel", post), (
        "the app administers CUPS by peer credentials as a wheel member"
    )


def test_the_firmware_app_is_wired_end_to_end():
    """The Firmware app is unprivileged; root's side is two units that run the
    same binary. All of it must ship together: the build, the install, the
    launcher, the notifier, the units, their enablement and the polkit rule
    that lets wheel start them. (It first landed with the overlay half only —
    a launcher, a timer and two units all pointing at a binary nothing built.)"""
    assert "pengyos-firmware" in _app_list()
    assert "pengyos-firmware" in _installed_bins()
    assert _launchers().get("io.catbee.pengyos.Firmware.desktop") == "pengyos-firmware"
    overlay = ROOT / "buildroot-external" / "board" / "desktop" / "rootfs-overlay"
    notifier = (overlay / "etc/xdg/autostart/io.catbee.pengyos.FirmwareNotifier.desktop").read_text()
    assert "Exec=pengyos-firmware --notifier" in notifier
    units = overlay / "etc/systemd/system"
    for unit in ("pengyos-firmware-check.service", "pengyos-firmware-check.timer",
                 "pengyos-firmware-update@.service"):
        assert (units / unit).is_file(), unit
    assert (units / "timers.target.wants/pengyos-firmware-check.timer").is_symlink()
    assert (units / "multi-user.target.wants/fwupd.service").is_symlink(), (
        "Buildroot's fwupd recipe enables nothing; without the daemon fwupdmgr has "
        "nothing to talk to"
    )
    # A comma (the device-id separator) is not allowed in a unit name: the app
    # starts the systemd-escaped instance, so the unit must pass the unescaped %I.
    update = (units / "pengyos-firmware-update@.service").read_text()
    assert "ExecStart=/usr/bin/pengyos-firmware apply %I" in update
    rules = (overlay / "etc/polkit-1/rules.d/60-pengyos-firmware.rules").read_text()
    assert '"pengyos-firmware-check.service"' in rules
    assert '"pengyos-firmware-update@"' in rules


def test_file_sharing_is_wired_with_its_polkit_rule():
    """pengyos-sharing is a window over pengyos-shares, run through pkexec: the
    app, its launcher, the CLI it drives and the rule that lets wheel run that
    one program must all be there, or the buttons fail at click time."""
    assert "pengyos-sharing" in _app_list()
    assert "pengyos-sharing" in _installed_bins()
    assert _launchers().get("io.catbee.pengyos.Sharing.desktop") == "pengyos-sharing"
    assert "pengyos-shares" in _overlay_bins()
    overlay = ROOT / "buildroot-external" / "board" / "desktop" / "rootfs-overlay"
    rules = (overlay / "etc/polkit-1/rules.d/60-pengyos-shares.rules").read_text()
    assert '"org.freedesktop.policykit.exec"' in rules
    assert '"/usr/bin/pengyos-shares"' in rules
    assert 'isInGroup("wheel")' in rules and "subject.local" in rules and "subject.active" in rules
    lib = (ROOT / "tools/pengyos-sharing/src/lib.rs").read_text()
    assert 'pub const SHARES: &str = "/usr/bin/pengyos-shares";' in lib, (
        "pkexec matches the rule on the absolute program path"
    )


def test_remote_desktop_is_wired():
    """The Remote Desktop window drives pengyos-remote, which runs the RDP server
    (a host-built binary in /usr/libexec) through a user unit in the session."""
    assert "pengyos-remote-desktop" in _app_list()
    assert "pengyos-remote-desktop" in _installed_bins()
    assert _launchers().get("io.catbee.pengyos.RemoteDesktop.desktop") == "pengyos-remote-desktop"
    assert "pengyos-remote" in _overlay_bins()
    lib = (ROOT / "tools/pengyos-remote-desktop/src/lib.rs").read_text()
    assert 'pub const REMOTE: &str = "/usr/bin/pengyos-remote";' in lib
    post = POST_BUILD.read_text(encoding="utf-8")
    assert '"${TARGET_DIR}/usr/libexec/pengyos-rdp-server"' in post
    assert "wanted pengyos-rdp-server" in BUILD_COSMIC.read_text(encoding="utf-8")
    unit = ROOT / "buildroot-external/board/desktop/rootfs-overlay/usr/lib/systemd/user/pengyos-rdp-server.service"
    assert "ExecStart=/usr/libexec/pengyos-rdp-server" in unit.read_text()


def test_the_games_are_one_binary_and_eight_symlinks():
    """tools/pengy-games carries every game: it alone is built and installed,
    and each game's launcher runs a /usr/bin/pengy-<game> symlink to it."""
    assert "pengy-games" in _app_list()
    installed = _installed_bins()
    assert "pengy-games" in installed
    post = POST_BUILD.read_text(encoding="utf-8")
    assert 'ln -sfn pengy-games "${TARGET_DIR}/usr/bin/pengy-${game}"' in post
    games = [p for p in _launchers().values() if p.startswith("pengy-") and p != "pengy-games"]
    assert len(games) == 8, games
    for prog in games:
        assert prog in installed, f"{prog} is not linked"
        assert prog not in _app_list(), f"{prog} is built on its own as well"
