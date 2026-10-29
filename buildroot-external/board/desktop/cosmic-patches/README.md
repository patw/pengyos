# `cosmic-patches/` — fixes we carry on COSMIC components

COSMIC is one Buildroot package, `package/cosmic`. Fixes we carry on top of
upstream live here, one directory per component, as `git am`-style patches, so
they are reviewed and versioned with the tree. The package's post-patch hook
applies each component's patches inside that component's source dir (Buildroot's
own patch step works on the package root, not per component), and a patch that
fails to apply fails the build. The patches are an input of `COSMIC_VERSION`:
adding or changing one rebuilds the package (rerun `scripts/cosmic-recipe.py`).

To check a patch against a new upstream commit by hand:

```sh
cd <component checkout at the new commit>
patch -p1 --dry-run < <repo>/buildroot-external/board/desktop/cosmic-patches/<component>/<patch>
```

| Component | Patch | Why |
|---|---|---|
| `cosmic-notifications` | `0001-applet-wait-longer-for-the-panel-connection.patch` | The daemon gave the panel 1 s to connect; a slow-starting panel lost its notifications applet. Upstream went to 10 s in epoch-1.11.0 (#181); we keep 30 s until a cold USB-stick boot is proven on 10 s, then drop it. |
| `cosmic-notifications` | `0002-app-clicking-a-notification-with-no-actions-opens-its-app.patch` | A notification without actions was only dismissed on click; now its app (desktop-entry hint) is launched with the activation token, as GNOME Shell does. Single-instance apps come to the front. |
| `cosmic-applets` | `0001-battery-no-battery-icon-on-machines-without-a-system-battery.patch` | A desktop with a wireless mouse/keyboard showed a 0% battery icon: the applet took later UPower updates after "no battery". Backport of cosmic-settings a366fc7 (epoch-1.9). |
| `cosmic-store` | `0001-flatpak-refresh-appstream-by-the-remote-s-name.patch` | The hourly appstream refresh named the system remote by its display id ("flathub (system)"), so a system-wide Flathub's catalogue never refreshed. Unfixed upstream as of 2026-10-11. |
| `cosmic-files` | `0001-declare-the-archive-mime-types-cosmic-files-opens.patch` | cosmic-files extracts zip/tar/gzip/bzip2/xz itself (`src/archive.rs`) but its `.desktop` declared only `MimeType=inode/directory;`, so it never appeared in *Open with* for an archive and `mimeinfo.cache` had no archive handler at all. Declares the nine types its `SUPPORTED_ARCHIVE_TYPES` covers. |
| `cosmic-player` | `0001-declare-the-audio-mime-types-cosmic-player-decodes.patch` | Player's `.desktop` declares 100 media types and exactly one of them audio (`audio/x-pn-realaudio`), while its `Categories` say `AudioVideo` and the image's GStreamer plugins demux/decode the rest of the common audio. So *Open with* never listed Player for an `.ogg`/`.m4a`/`.aiff`, and `mimeinfo.cache` had no audio handler for them. Declares the 19 the shipped plugins cover (ogg/vorbis/opus, isomp4, flac, wav, mp3, adts aac, aiff). No MIDI synth or module player is shipped, so `.mid`/`.mod` stay out. |
| `cosmic-viewer` | `0001-declare-the-image-mime-types-cosmic-viewer-decodes.patch` | cosmic-viewer depends on the Rust `image` crate with its default features on, so `default-formats` gives it `dds`, `exr` and `tga` decoders, and `image/x-icon` is the legacy name — `.ico` is `image/vnd.microsoft.icon` in shared-mime-info. Its `.desktop` declared none of the four, so Viewer was not a handler for any of them. Declares the four it decodes. (`psd` is *not* enabled in the image crate, so `.psd` stays out.) |
| `cosmic-greeter` | `0001-locker-no-lock-for-sleep-when-the-session-logged-in-automatically.patch` | The locker locked on every logind `PrepareForSleep`, with no option to turn it off, so a machine set to log in without a password still asked for one on every wake (Ubuntu doesn't). Now the sleep lock is skipped, and the sleep-delay inhibitor released, when `/etc/pengyos/autologin.conf` names the session's user. Read at each sleep, so `pengyos-autologin off` brings the lock back at once. A hand lock (Super+Escape, `loginctl lock-session`) is unchanged. |
| `cosmic-idle` | `0001-no-idle-lock-when-the-session-logged-in-automatically.patch` | The same for the idle screen-off: it turned the screen off and then ran the lock action. Now the screen still turns off but does not lock for the automatic-login user (`$USER`). Same file, same parsing rules as `greetd-config`. |
| `cosmic-settings` | `0001-vpn-show-every-config-file-in-the-add-vpn-picker.patch` | The VPN page's *Add VPN* picker shipped two filters — OpenVPN (`.ovpn`) and WireGuard (`.conf*`) — with OpenVPN as the default, so the dropdown reads "OpenVPN", the file list hides every `wg*.conf`, and the page presents as OpenVPN-only: the WireGuard entry is never found (behoweather 26.10.24, reproduced 2026-10-12 through the portal with screenshots). Adds an "All Files" filter and makes it the default, so `.conf` files are listed and both VPN filters are visible. The WireGuard import path (`import_wireguard` → NM's built-in plugin, verified working on the live image) is untouched. |

When the COSMIC recipes move into `package/`, these become that package's
patches.
