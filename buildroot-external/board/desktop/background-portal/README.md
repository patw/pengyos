# `background-portal` — the Background portal backend

A ~200-line D-Bus service that makes `org.freedesktop.portal.Background` exist on
PengyOS Desktop.

## The problem it fixes

xdg-desktop-portal exports a portal interface only when a backend declares it.
`init_background()` (xdg-desktop-portal 1.22.1, `src/background.c`) returns
immediately when no `.portal` file names `org.freedesktop.impl.portal.Background`.
COSMIC's backend declares Access, FileChooser, RemoteDesktop, Screenshot,
ScreenCast and Settings; portal-gtk declares none of the background ones. So the
interface is absent, and a flatpak app that asks to run in the background or to
start at login gets:

```
GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod: No such interface
"org.freedesktop.portal.Background" on object at path /org/freedesktop/portal/desktop
```

SyncThingy reports that as "Background permission request failed", which is
wrong: nothing was denied, the question could not be asked. The practical loss is
autostart — the app never gets its `~/.config/autostart` entry.

## What the core already does (this backend does not duplicate any of it)

| Job | Where |
| --- | ----- |
| permission store lookup/write (table `background`) | `src/background.c` |
| "Allow <app> to Run in the Background?" dialog | core → `impl.portal.Access`, which COSMIC implements |
| writing `~/.config/autostart/<id>.desktop`, Exec rewritten to `flatpak run <id> …`, `X-Flatpak` set | core (`enable_autostart_sync` + `xdp_app_info_flatpak_validate_autostart`) |
| default when no permission is stored | core: unset means **allow**, and it stores `yes` |

## What a backend still owes, and why each answer is what it is

- **GetAppState** (0 background / 1 running / 2 active). The core *kills* an app it
  believes is in the background and lacks permission, so a wrong guess here is
  harmful, not merely inaccurate. We do not know COSMIC's window state, so every
  live flatpak instance is reported as **RUNNING** — the one direction that can
  never nag or kill. Real window state needs the compositor; that is the upgrade
  path in cosmic-portal, which already enumerates toplevels.
- **NotifyBackground** answers **IGNORE** (allow this instance, change no
  permission). FORBID makes the core SIGKILL the app and store a standing denial;
  ALLOW stores a standing permission. Neither is this helper's call.
- **EnableAutostart** is not implemented: 1.22 never calls it, and a second writer
  of the autostart file is how it gets corrupted.

## Files

| File | Installed by |
| --- | --- |
| `background-portal.c` (here) | `../post-build.sh`, cross-compiled against the image's glib → `/usr/libexec/pengyos/background-portal` |
| `../rootfs-overlay/usr/share/xdg-desktop-portal/portals/pengyos-background.portal` | overlay — declares the interface, so the core will export the portal |
| `../rootfs-overlay/usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.pengyos.service` | overlay — D-Bus activation, by `Exec=` because the session bus is `dbus-run-session` |
| `org.freedesktop.impl.portal.Background=pengyos-background;` in `/usr/share/xdg-desktop-portal/portals.conf` | `../post-build.sh` appends it (the file itself comes from cosmic-portal) |

## Built and checked

`post-build.sh` compiles it with the image's own toolchain:

```sh
${HOST_DIR}/bin/*-buildroot-linux-gnu-gcc -Wall -O2 -o .../background-portal \
    background-portal.c $(pkg-config --cflags --libs gio-2.0)
```

Verified on behoweather (PengyOS Desktop 26.10.13) with the backend staged into
`~/bin` and the same three config lines in the user's portal dirs:

- before: `RequestBackground` → `UnknownMethod` (the reported bug).
- after: the interface is exported; a call from inside the SyncThingy sandbox
  returns a request object, the permission store gains
  `com.github.zocker_160.SyncThingy → yes`, and the core writes
  `~/.config/autostart/com.github.zocker_160.SyncThingy.desktop` with
  `Exec=flatpak run --command=SyncThingy com.github.zocker_160.SyncThingy --no-browser`.
- `GetAppState` control: a live instance is reported `1`, a dead one is skipped, a
  non-directory is skipped.
