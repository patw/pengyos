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
pengyos-vm autostart web on         # start at boot (turns on linger, see below)
pengyos-vm delete web --yes
```

## Throwaway runs: pengyos-vm run

```sh
export PENGYOS_VM_IMAGE=~/pengyos-core-x86_64.img            # or --image FILE|URL
pengyos-vm run -- uname -a                                    # boot, run, delete
pengyos-vm run --copy ./mytool --fetch out --timeout 30m -- ./mytool/run.sh
pengyos-vm run                                                # a shell; deleted on exit
```

A fresh VM for one job, then gone. The image is converted to qcow2 once and
cached (`~/.cache/pengyos-vm/images`); each run boots a copy-on-write overlay
of it, so it starts in seconds and the cached image is never written. The run
makes an SSH key for itself and gives it to the guest as root's (a systemd
credential, so the image must be a systemd guest with sshd: PengyOS Core).
`--copy` puts files and folders in `/root/run`, where COMMAND runs (as ssh
runs it: the words are joined and the guest's shell reads them); `--fetch`
copies `/root/run/PATH` back into the current folder, even when COMMAND
failed. The exit status is COMMAND's, 124 when `--timeout` stopped it.

Afterwards the VM is switched off and its folder deleted: Ctrl-C, SIGTERM and
a closed terminal clean up too. A run killed outright (SIGKILL) leaves its
`run-XXXXXX` VM behind; the next `run` deletes it. `--keep` keeps the VM
instead (`pengyos-vm ssh NAME` uses the run's key). Defaults: 2 CPUs, 1G
memory, an 8G sparse disk, NAT (`--forward` works), 180 s to answer SSH.

## In a browser: pengy-vmm

```sh
systemctl --user enable --now pengy-vmm    # serves http://127.0.0.1:5100
pengy-vmm url                              # the address to open, with your token
```

A page over the same commands: the VM list, start / shut down / force off /
start at boot / eject / delete, the serial console (read it, type a line), and
a form for a new VM (ISO, or an image file or URL, downloaded in the
background). Every button runs `pengyos-vm` as you. It listens on 127.0.0.1
and needs your token (`~/.config/pengy-vmm/token`, 0600), because other
accounts on the machine can reach that port too; changes need it in a header
as well, so another site cannot post a form at it. From another machine:
`ssh -L 5100:127.0.0.1:5100 you@host`, then open the `pengy-vmm url` address.

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
  manager ends with your last session. `loginctl enable-linger` (no sudo:
  `60-pengyos-linger.rules` lets anyone set their own) keeps it running
  without a login; `autostart on` turns it on for you. `start` says so.
- `--ssh-key` hands your public key to the guest as the systemd credential
  `ssh.authorized_keys.root`, and every VM gets `system.hostname=NAME`.
  systemd guests (PengyOS Core, recent Fedora/Debian/Arch) use them; others
  ignore them.
- `stop` presses the ACPI power button and waits up to 100 s; a guest with
  nothing listening for it (PengyOS Core before 26.10.51: no logind) is then
  switched off. When the machine shuts down or reboots, each VM gets 30 s, and
  the console says which VMs it is waiting for.
- Without access to `/dev/kvm` a VM still runs, in QEMU's emulator (slow).
- Images: qcow2 or raw, `.xz`/`.gz` unpacked (sparsely), http(s) downloaded.
  The disk is grown to `--disk`, or to 20G without it, never shrunk (qcow2 is
  sparse: unused space costs nothing).
- A forward reaches the guest's port, and the guest's own firewall decides the
  rest. A PengyOS guest is default-deny like every edition: open the port
  inside it too (`pengyos-firewall allow 80`). `create` and `set` remind you.

## Files

`~/vms/NAME/`: `vm.json` (settings), `disk.qcow2`, `vars.fd` (UEFI),
`console.log`, `qemu.log`, `known_hosts`, and while it runs `serial.sock` and
`qmp.sock`; a `run` VM also has its key, `id_ed25519`. Cached images:
`~/.cache/pengyos-vm/images/`. The unit is `/usr/lib/systemd/user/pengyos-vm@.service`.

See [spec.md](spec.md) for the contract and how it was verified.
