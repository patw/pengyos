# pengyos-rdp-server

The PengyOS Desktop's RDP server: it shares the logged-in COSMIC session.

    portal RemoteDesktop + ScreenCast  ->  PipeWire frames (RGBA, SHM)  ->  ironrdp-server
    RDP input  ->  portal Notify* (cosmic-portal -> cosmic-comp over libei)

Turned on and off with `pengyos-remote` (tools/pengyos-remote), which runs it
as the user unit `pengyos-rdp-server.service`. It reads the user's
`~/.config/pengyos-remote/` (password, cert.pem, key.pem) and refuses a
password file others can read. The portal's restore token
(`~/.local/state/pengyos-remote/restore-token`) skips the consent dialog after
the first allowed session.

Host-built (`scripts/build-cosmic.sh`, into the libcosmic apps' target dir) and
installed by the Desktop post-build to `/usr/libexec/pengyos-rdp-server`.

## Vendored ironrdp

`vendor/ironrdp-server` (0.13.0) and `vendor/ironrdp-acceptor` (0.10.0) are the
crates.io sources with our changes (`[patch.crates-io]`); `vendor/pengyos.diff`
is the whole change, for re-applying on a bump. Why each change exists and when
it can go: `divergence.toml` -> `ironrdp-server-0.13.0-fork`. pengyos-nightly
watches crates.io for new releases.

## Clients it was proven with

FreeRDP 3.32 (`xfreerdp3`, sdl-freerdp3) and Remmina's snap (FreeRDP 3.14.1),
2026-10-23 and 2026-10-31.
