# pengyos-machines — "Machines"

The Desktop's app for **virtual machines**, **dev boxes** and **containers**: a
window over `pengyos-vm`, `distrobox`, `podman` and Quadlet that does nothing
those tools cannot, and shows the command behind each result. The contract,
and the order it is built in, is [`spec.md`](spec.md).

Status: the VM page with the built-in screen and snapshots (spec step 2). Dev
boxes and containers are next.

```sh
pengyos-machines                 # the window
pengyos-machines --list [--json] # the VMs and the ISO library, no window
```

- `src/lib.rs`, `src/rfb.rs`, `src/keymap.rs`, `src/backend.rs`: std + serde_json,
  `cargo test` with no display, QEMU or podman (a fake VNC server on a Unix
  socket stands in for QEMU).
- `src/app.rs`, `src/screen.rs` (feature `cosmic`): the window and the VNC canvas.

Built like the other PengyOS libcosmic apps:
`cargo build --release --features cosmic` into `~/cosmic-build/install-gui`.
