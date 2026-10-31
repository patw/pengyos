# pengyos-vm — spec

## Contract

- One stdlib Python script, `board/server/rootfs-overlay/usr/bin/pengyos-vm`,
  so the Server and every edition stacked on it ship it. A user template unit,
  `pengyos-vm@.service`, runs `pengyos-vm _run NAME`, which execs QEMU;
  `ExecStop` is `_powerdown` (QMP `system_powerdown`, wait for QEMU to exit).
- A VM is `~/vms/NAME` with `vm.json`. Names are `[a-z0-9][a-z0-9-]{0,31}`.
- `create` needs `--iso` or `--image`; misuse exits 2 and leaves no folder.
  Defaults: 2 CPUs, 2G memory, 20G disk, UEFI (`--bios` for SeaBIOS), NAT.
- The QEMU command: q35, KVM (`-cpu host`) or TCG (`-cpu max`) when `/dev/kvm`
  is not usable; virtio disk at bootindex 0, the ISO at bootindex 1 (an empty
  disk falls through to the installer); virtio-net; serial on a socket with
  `console.log`; QMP socket; VGA + VNC on `127.0.0.1:N`; USB tablet; RNG;
  systemd credentials over SMBIOS type 11.
- NAT forwards: host port ≥ 1024, unique across the user's VMs, on 127.0.0.1
  unless an address is given. The SSH forward is allocated from 2201 up.
- Bridge: networkd `.netdev`/`.network` files named `05-pengyos-br0*`, the
  bridge with the port's MAC, DHCP on the bridge with `UseHostname=no`;
  `/etc/qemu/bridge.conf` = `allow pengyos-br0`. Every file starts with the
  managed-by mark and `disable` removes only marked files. Wireless and
  virtual ports are refused.
- Image side: `qemu-bridge-helper` is setuid root (`board/server/
  permissions.txt`); with no `bridge.conf` it allows nothing. QEMU is built
  with VNC (`BR2_PACKAGE_LIBVNCSERVER`). The firewall's forward chain accepts
  `pengyos-br0 → pengyos-br0` (br_netfilter is active on the Server).

## Verification

- `tools/pengyos-vm/tests` (43 tests): the script against a sandbox root with
  fake systemctl / qemu-img / networkctl / ip / ssh.
- 2026-10-31, ThinkPad Server (Wi-Fi, real KVM): a PengyOS Core VM from
  `core.img.xz` with `--ssh-key` and a forward: `pengyos-vm ssh core1` logged
  in as root, hostname `core1` from the credential, the disk grown to 4G;
  console in and out; `autostart on` with linger brought it back after a host
  reboot with nobody logged in.
- 2026-10-31, a Server image under QEMU (nested KVM): `bridge enable` moved
  10.0.2.15 to `pengyos-br0` without dropping SSH; a bridged Core VM, started
  by an unprivileged user, got 10.0.2.16 from the LAN's DHCP, answered ping
  and `pengyos-vm ssh lan1` (address from the neighbour table); VNC listened
  on 127.0.0.1:5901; `bridge disable` gave the address back to `ens3` and left
  no files.
- Found on the way, and fixed: a VM stopped when its user logged out without
  linger, and only root could turn linger on (polkit is now on the Server,
  with `60-pengyos-linger.rules`); PengyOS Core ignored the ACPI power button
  (logind is now in Core); the image's QEMU had no VNC until libvncserver was
  selected.
- 2026-10-31, the next Server image: an unprivileged `loginctl enable-linger`
  succeeds (another user's is still refused); a Core VM's `pengyos-vm stop`
  took 0.7 s (it was 100 s). pengy-vmm as a user unit: 403 without the token,
  the page lists and details the VM with its serial console, a line typed into
  the page ran in the guest, and a POST with only the cookie got 403.

## pengy-vmm

- `/usr/bin/pengy-vmm` (stdlib), user unit `pengy-vmm.service`, off by default.
- 127.0.0.1:5100 by default. Token: `~/.config/pengy-vmm/token`, 0600, made on
  first run. GET needs it as `?token=` or the SameSite=Strict HttpOnly cookie;
  POST needs it in `X-Token`. CSP and `X-Frame-Options: DENY` on every page.
- Every action is a `pengyos-vm` command line run as the user (create runs in
  the background and is polled as a job); VM names are checked against the
  same pattern before they reach a command line or a path.
