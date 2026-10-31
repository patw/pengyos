# pengyos-vm

Run virtual machines on a PengyOS Server or Desktop without writing QEMU
command lines. VMs run as your user, under your systemd user manager; nothing
but `bridge enable|disable` needs root.

```sh
pengyos-vm create deb --iso ~/debian-13-netinst.iso          # an installer
pengyos-vm start deb --console                               # Ctrl-] leaves
pengyos-vm set deb --eject                                   # after the install

pengyos-vm create web --image https://…/debian-13-genericcloud-amd64.qcow2 \
    --disk 20G --memory 2G --forward 8080:80 --ssh-key
pengyos-vm start web && pengyos-vm ssh web

pengyos-vm list                     # NAME STATE CPUS MEMORY NET SSH AUTOSTART
pengyos-vm show web [--json|--qemu] # settings, how to reach it, the QEMU command
pengyos-vm vnc web                  # its screen: VNC on 127.0.0.1, and the ssh -L line
pengyos-vm log web                  # what it wrote to its serial console
pengyos-vm stop web [--force]       # ACPI power button (or pull the plug)
pengyos-vm autostart web on         # start at boot (needs linger, see below)
pengyos-vm delete web --yes
```

## Networking

| Mode | What | Where |
|---|---|---|
| `nat` (default) | shares this machine's connection; reached only through forwards. Every VM gets `127.0.0.1:22xx → 22` (`pengyos-vm ssh`). Forwards listen on 127.0.0.1 unless you give an address (`0.0.0.0:8080:80`, then `sudo pengyos-firewall allow 8080`). | anywhere, Wi-Fi included |
| `bridge` | the VM is a machine on the LAN with its own DHCP address | wired only |

`sudo pengyos-vm bridge enable [IFACE]` puts the wired port on `pengyos-br0`
(systemd-networkd files `05-pengyos-br0*` in `/etc/systemd/network`; the
bridge takes the port's MAC address) and allows that bridge in
`/etc/qemu/bridge.conf`, which the setuid `qemu-bridge-helper` reads.
`bridge disable` removes exactly those files.

**Wi-Fi cannot be bridged.** An 802.11 client may only send frames from its own
MAC address, so the access point drops a VM's. `bridge enable` refuses a
wireless port and says to use NAT.

## Things to know

- **Logging out stops your VMs** unless linger is on: your systemd user
  manager ends with your last session. `sudo loginctl enable-linger USER`
  keeps it (and `autostart`) running without a login. `start` says so.
- `--ssh-key` hands your public key to the guest as the systemd credential
  `ssh.authorized_keys.root`, and every VM gets `system.hostname=NAME`.
  systemd guests (PengyOS Core, recent Fedora/Debian/Arch) use them; others
  ignore them.
- `stop` presses the ACPI power button and waits up to 100 s. A guest with
  nothing listening for it (PengyOS Core: no logind) is then switched off.
- Without access to `/dev/kvm` a VM still runs, in QEMU's emulator (slow).
- Images: qcow2 or raw, `.xz`/`.gz` unpacked (sparsely), http(s) downloaded.
  The disk is grown to `--disk`, never shrunk.

## Files

`~/vms/NAME/`: `vm.json` (settings), `disk.qcow2`, `vars.fd` (UEFI),
`console.log`, `qemu.log`, `known_hosts`, and while it runs `serial.sock` and
`qmp.sock`. The unit is `/usr/lib/systemd/user/pengyos-vm@.service`.

See [spec.md](spec.md) for the contract and how it was verified.
