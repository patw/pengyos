# pengyos-shares — share folders on the network over SMB (ksmbd)

## Summary
A stdlib-only Python script shipped in the Server overlay (so the Desktop has it
too) that turns the kernel's SMB3 server on and off around a set of shares:
it owns `/etc/ksmbd/ksmbd.conf` and `/etc/ksmbd/ksmbdpwd.db`, enables
`ksmbd.service` with the first share, and opens port 445 to LAN addresses
through `pengyos-firewall`. It is the backend the Desktop's "File Sharing" app
will drive (US-SRV-14).

## Purpose
A homelab Server is where the household's files live, and SMB is what every
other machine in the house speaks. PengyOS had no SMB server at all. Samba's
smbd is the usual answer, but it is a large build that would land on every
Server, and its own defaults (guest shares, `force user`) are how miniserv's
`/data` ended up anonymously root-writable (2026-08-27). ksmbd is in the kernel
already and ksmbd-tools is small; this tool makes "share a folder" one command
whose defaults are the safe ones.

## Interface
```
pengyos-shares status [--json]
sudo pengyos-shares add NAME PATH [--read-only] [--user USER]... [--comment TEXT]
sudo pengyos-shares remove NAME
sudo pengyos-shares passwd USER [--stdin]
sudo pengyos-shares deluser USER
pengyos-shares … --root DIR        # test hook: files under DIR, no root needed
```
- `NAME`: 1-32 of letters, digits, `.`, `_`, `-`, starting with a letter or
  digit; not `global`, `homes`, `printers` or `ipc$`; unique ignoring case.
- `PATH`: an existing folder, given as a full path; not `/` and not under
  `/usr`, `/etc`, `/boot`, `/efi`, `/proc`, `/sys`, `/dev` or `/run`.
- `USER`: an account in `/etc/passwd` with uid 1000-59999. `--user` defaults
  to `$SUDO_USER`.
- Share passwords: 8-128 bytes; prompted twice, or one line with `--stdin`.
- Misuse exits 2 with a message and writes nothing; a failing `systemctl` or
  `pengyos-firewall` exits 1. `status` works unprivileged.

## Deliverable
| File | Ships at |
|---|---|
| `buildroot-external/board/server/rootfs-overlay/usr/bin/pengyos-shares` | `/usr/bin/pengyos-shares` (Server, Desktop) |
| `board/server/linux.fragment`: `CONFIG_SMB_SERVER=m` | the ksmbd module |
| `configs/layers/server.config`: `BR2_PACKAGE_KSMBD_TOOLS` | `ksmbd.mountd`, `ksmbd.control`, `ksmbd.service` |
| `10-pengyos-server.preset`: `disable ksmbd.service` | off until the first share |
| `tools/pengyos-shares/` | the test suite and its docs (not in the image) |

Runtime state, all under `/etc` (kept across OS updates):
`/etc/ksmbd/ksmbd.conf` (0644, first line `# Managed by pengyos-shares.`),
`/etc/ksmbd/ksmbdpwd.db` (0600, `user:base64(MD4(UTF-16LE(password)))` per
line, ksmbdpwd.db(5)), and `/etc/pengyos/nftables.d/allow-tcp-445-from-*.nft`
(pengyos-firewall's).

## Behaviour
1. `add` validates everything first, then writes `ksmbd.conf` (atomically:
   `.new` then rename). `[global]`: netbios name from the hostname,
   `map to guest = never`, `restrict anonymous = 2`, `server signing = auto`.
   The share: `path`, `read only`, `valid users`, `guest ok = no`,
   `browseable = yes`, plus any keys added to it by hand.
2. With at least one share: `pengyos-firewall allow 445/tcp --from` each LAN
   range (idempotent), then `systemctl reload ksmbd.service` if it is active,
   else `systemctl enable --now ksmbd.service`.
3. With none: `systemctl disable --now ksmbd.service`, then
   `pengyos-firewall remove` the same rules.
4. `passwd`/`deluser` rewrite `ksmbdpwd.db` and reload ksmbd if it is running;
   they never start it.
5. A `ksmbd.conf` without the marker line and with any section is left alone,
   and `add`/`remove` refuse (exit 2) until it is moved aside.

## Acceptance criteria
- The suite (`python3 -m pytest -q`) passes: conf and db byte for byte, the NT
  hash against the known vector, service and firewall on with the first share
  and off with the last, reload not restart, hand keys kept, every refusal.
- Built image: `ksmbd.service` is not enabled, nothing listens on 445.
- Booted Server image (QEMU): `add` + `passwd`, then a cifs mount of the share
  with the right password reads and writes as the share user; a wrong password
  and an anonymous mount are refused; `remove` stops ksmbd and 445 is closed.

## Out of scope
- The Desktop GUI ("File Sharing" app): next, on top of this tool.
- Guest/anonymous shares, SMB1, AD domains, printer sharing.
- Network discovery (WS-Discovery for Windows' Network view, mDNS `_smb._tcp`):
  shares are reached by name or address.
