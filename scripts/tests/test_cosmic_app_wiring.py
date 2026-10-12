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
    """Every /usr/bin/<name> the Desktop post-build installs."""
    text = POST_BUILD.read_text(encoding="utf-8")
    return set(re.findall(r"\$\{TARGET_DIR\}/usr/bin/([\w.-]+)", text))


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
    for app in ("pengyos-disks", "pengyos-gamepads", "pengyos-printers", "pengyos-updates"):
        assert app in programs, f"{app} is installed but no .desktop launcher runs it"


def test_every_launcher_runs_an_installed_program():
    installed = _installed_bins()
    # Programs the image ships from Buildroot recipes, not from post-build.sh.
    for name, prog in _launchers().items():
        if name in NON_APP_LAUNCHERS:
            continue
        assert prog in installed, (
            f"{name} runs `{prog}`, which no post-build step installs"
        )


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
