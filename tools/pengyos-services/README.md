# pengyos-services

What this machine serves, and whether it will after a reboot, in one command.

```sh
pengyos-services                      # every service the owner added, listens, or failed
pengyos-services status jellyfin      # one in full
sudo pengyos-services status --json   # every user's, as one JSON document
pengyos-services status --check       # exit 1 if something is wrong (for scripts)
pengyos-services status --probe-from laptop   # test LAN reachability for real, over ssh
```

For each systemd unit, Quadlet container and `pengyos-vm` VM it reports:

- **state**: active, failed (and why), restarts.
- **autostart**: `yes`, `at-login-only`, `on-demand`, `via-timer`, or `no`, worked out
  from what systemd will do, with the reason. `systemctl is-enabled` says `generated`
  for every Quadlet, and a user service without linger only starts once its user
  logs in. This answers both.
- **ports**: every port it listens on or declares (`PublishPort=`, a VM forward), with
  the bind address, what the firewall does with it, and whether the LAN can reach it.
- **memory**: `memory.peak` and anonymous memory. Page cache is shown apart: it is
  what made `MemoryCurrent` read 9.8 GB for a 148 MB syncthing.
- **data**: where its state lives (Quadlet volumes, `StateDirectory=`, a VM's folder).

Then the problems, each with the command that fixes it.

Without root it covers system services and your own. Run it with sudo to see every
user's services and the owner of every socket.

It changes nothing. Starting, enabling and opening ports are for `systemctl`,
`pengyos-vm` and `pengyos-firewall`; this prints the command instead.

## Tests

```sh
cd tools/pengyos-services && python3 -m pytest -q
```

The suite runs the shipped script against a sandbox root (`--root DIR`), with one
fake standing in for systemctl, ss, ip, pengyos-firewall, ssh and the podman
generators. See `spec.md` for what is checked on a booted image.
