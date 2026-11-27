# pengyos-machines — "Machines"

The Desktop's app for **virtual machines**, **dev boxes** and **containers**: a
window over `pengyos-vm`, `distrobox`, `podman` and Quadlet that does nothing
those tools cannot, and shows the command behind each result. The contract,
and the order it is built in, is [`spec.md`](spec.md).

Status: all three pages (0.1.0).

```sh
pengyos-machines                       # the window
pengyos-machines --screen deb          # open on a VM's screen
pengyos-machines --page containers     # or on a page (boxes, containers)
pengyos-machines keep-at-boot web      # make a podman container a Quadlet service
pengyos-machines --list [--json]       # the VMs and the ISO library, no window
```

- `src/lib.rs`, `src/containers.rs`, `src/rfb.rs`, `src/keymap.rs`, `src/backend.rs`: std + serde_json,
  `cargo test` with no display, QEMU or podman (a fake VNC server on a Unix
  socket stands in for QEMU).
- `src/app.rs`, `src/app_pods.rs`, `src/screen.rs` (feature `cosmic`): the window,
  the Dev boxes and Containers pages, and the VNC input layer.

Built like the other PengyOS libcosmic apps:
`cargo build --release --features cosmic` into `~/cosmic-build/install-gui`.
