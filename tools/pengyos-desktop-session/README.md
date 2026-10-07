# pengyos-desktop-session

The COSMIC session + a VM-testable smoke matrix (**US-DSK-1**) for PengyOS
Desktop.

A declarative COSMIC-session manager for the PengyOS Desktop edition:
greetd/cosmic-greeter → cosmic-session startup as deterministic systemd +
session files, plus a check engine for the session facts QEMU+OVMF can actually
verify — with GPU/audio/Bluetooth explicitly out of scope.

## What it does

The tool has two jobs:

1. **`emit`** — write the COSMIC session definition as deterministic files into a
   target overlay dir:
   * a **greetd config** pointing at the cosmic-greeter session,
   * a **cosmic-greeter session entry** (compositor = `cosmic-comp`,
     `exec = cosmic-session`, wayland socket path, environment),
   * the **cosmic-session systemd unit / launcher** and the session
     **environment**,
   all laid out with stable ordering and content so the output is byte-for-byte
   reproducible.

2. **`check`** — verify the VM-testable session facts over a boot log (or, opt-in,
   over ssh from a running target). The engine is a real present/absent check:
   it **fails if a present pattern is missing** (no vacuous pass).

### VM-testable facts

| Fact | How it is checked |
| ---- | ----------------- |
| `cosmic-comp` started | boot log / ssh probe |
| a wayland socket exists | socket path present |
| `cosmic-session` / panel up | boot log / ssh probe |
| `pipewire` / `wireplumber` running | boot log / ssh probe |
| portals responding | boot log / ssh probe |

### Out of scope (named honestly, never silently dropped)

Real GPU acceleration, audio devices, Bluetooth, and suspend/resume are
**real-hardware facts** — they cannot be verified in QEMU+OVMF. They are encoded
as a documented list (`pengyos_desktop_session.oos.OUT_OF_SCOPE`) and the check
engine reports them as *"real-hardware, not verified here"* beneath every
verdict (PASS, FAIL, and SKIP) — never silently omitted.

## Interface

```console
$ pengyos-desktop-session emit --out DIR [--json]
$ pengyos-desktop-session check --log FILE [--json]
$ pengyos-desktop-session check --ssh HOST [--json]   # opt-in, PENGYOS_IT=1
```

* `emit --out DIR` writes the greetd config + cosmic-greeter session entry +
  cosmic-session systemd unit/launcher + environment under `DIR`, deterministically.
* `check --log FILE` checks the VM-testable facts over a boot log; the engine
  fails if a present pattern is missing.
* `check --ssh HOST` collects the same facts over ssh from a running target
  (opt-in behind `PENGYOS_IT=1`; skips honestly — `RESULT: SKIP` / exit `4` —
  when the probe cannot run, e.g. the gate is off, the host is unreachable, or
  there is nothing to verify).

## Development

```console
$ pip install -e .[test]
$ python -m pytest -q
```

Tests need no network, root, real display, or real hardware — they use
fixtures and mocks. Integration tests (boot log / ssh paths) are opt-in behind
`PENGYOS_IT=1`.

## Docs

* [`spec.md`](spec.md) — the full specification.

## License

MIT — see [`LICENSE`](LICENSE). Copyright (c) 2026 Pat Wendorf.
