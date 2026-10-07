# pengyos-guest

Declarative [distrobox](https://distrobox.it)/[podman](https://podman.io)
guests for the [PengyOS](../../README.md) project (Lane 4).

`pengyos-guest` manages compatibility guest environments declaratively:
create, enter, export host-side launchers, and tear down — with **no host
writes** outside the guest's home mount and the exports you declare.

## Install

This is a standard-library-only Python package (src layout). To run the tests
without installing:

```bash
python3 -m pytest -q
```

To install the console script (`pengyos-guest`):

```bash
pip install -e .
```

## Usage

```bash
pengyos-guest list                    # guests this machine knows, and whether they exist
pengyos-guest enter dev               # a shell in the dev box (created on first use)
pengyos-guest enter dev -- make -j8   # one command in it; exit code passed back
pengyos-guest apply [name...]         # create + provision (idempotent; all when none named)
pengyos-guest export dev --app code   # host launcher in ~/.local/bin (+ menu entry for GUI apps)
pengyos-guest destroy dev             # remove the container; your home is kept
pengyos-guest --config FILE list      # use one guests.toml instead of the defaults
```

## The dev box

PengyOS images are immutable: there is no `apt install` on the host. Compilers
and SDKs live in a guest instead — an ordinary Ubuntu (or Fedora, Arch, …)
container that shares your home directory, user, network and display with the
host. Clone a repo into `~/src`, edit it on the host, build it with
`pengyos-guest enter dev -- make`; the binaries land in your home and usually
run on the host too.

The image ships the definitions (`/usr/share/pengyos/guests.d/`):

| Guest | Image | What for |
|---|---|---|
| `dev` | `ubuntu:24.04` | build-essential, cmake, ninja, git, go, python3, rustup, uv |
| `cuda` (NVIDIA images) | `nvidia/cuda:13.1.0-devel-ubuntu24.04`, `nvidia = true` | CUDA builds (llama.cpp …) against the host driver |

The first `enter` pulls and provisions the box (about a minute and a half for
`dev` on a fast link); after that entering takes a fraction of a second. Inside
it `sudo apt install` works as usual — it changes the box, never the host. To
start fresh, `destroy` it and enter again; your home, `~/.cargo` included, is
untouched.

## Configuration (`guests.toml`)

Definitions are read from the image's `/usr/share/pengyos/guests.d/*.toml`,
then from `~/.config/pengyos/guests.toml`; a guest you define replaces a
shipped one of the same name. Each guest is a table under `[guests]`:

```toml
[guests.dev]
description = "My build box"
image = "registry.fedoraproject.org/fedora:42"
packages = ["gcc", "cmake", "git"]
setup = ["command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh"]
mounts = ["/data/models:/models:ro"]
nvidia = false
```

- `image` — the container image the guest is built from (required).
- `packages` — installed with the image's package manager (apt, dnf, pacman,
  apk, nix) on every `apply`.
- `setup` — shell commands run inside the guest as you, after the packages, on
  every `apply`; keep them idempotent.
- `mounts` — extra `host:guest[:mode]` bind mounts (your home is always shared).
- `nvidia` — share the host's NVIDIA driver with the guest (CUDA).
- `exports` — launcher name → guest-side executable path.
- `description` — shown by `list`.

`guests.toml` at the tool's root is a small example.

## Design

- **No host writes.** Everything that touches the outside world goes through a
  mockable [`Runner`](src/pengyos_guest/runner.py). Production uses a
  subprocess runner; tests use an in-memory fake to assert on exact argv.
- **Sandbox guarantee.** [`pengyos_guest.sandbox`](src/pengyos_guest/sandbox.py)
  turns "no host writes outside the guest's home mount and the exports you
  declare" into an assertable contract: the only host paths the tool emits are
  the declared bind mounts, so `apply` locks an optional pre-execution
  `SandboxGuard` to those mounts and rejects any command that points elsewhere.
  Tests assert the same guarantee by scanning every argv across the full
  apply/enter/export/destroy lifecycle.
- **Standard library only.** No runtime dependencies.
- **Testable offline.** Tests need no network, root, display, or hardware.
  System integration tests are opt-in behind `PENGYOS_IT=1`.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
