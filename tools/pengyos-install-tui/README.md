# pengyos-install-tui

The **console installer** for PengyOS Core and Server.

The install stick boots to a root console on tty1 and starts
`pengyos-install-tui install` there. It installs the stick's image through the
same engine as the Desktop's GUI (`pengyos-install image`, see
`tools/pengyos-install`), so both installers produce the same machine.

The expected setup is a monitor and keyboard plugged into the machine for the
install, then SSH from somewhere else afterwards. So the installer is built for
the Linux console (no pasting, often an old laptop panel) and puts SSH access
up front.

## Pages

1. **Disk**: every disk is listed. Only usable ones can be chosen; the
   installer's own stick, disks in use and disks under the minimum size are
   shown with the reason. A single usable disk is preselected.
2. **Computer name**: defaults to `pengyos`.
3. **Account**: username and password (typed twice, masked). The password is
   also set for root.
4. **SSH access**: "Import keys from" takes one of
   - a GitHub username (`patw` or `gh:patw`): fetches
     `https://github.com/<name>.keys`, the way `ssh-import-id gh:<name>` does;
   - an `http(s)://` URL: any authorized_keys-style file, e.g. on a NAS;
   - `user@host` or `user@host:port`: another machine that takes a password
     over SSH. The installer asks for that password (masked) and copies the
     machine's whole `~/.ssh/authorized_keys`, so whoever can log in there can
     log in here. The host key is accepted on first sight (a LAN machine).

   A pasted key can be added too. Lines with options (`from="..."`,
   `command="..."`) are skipped and counted in the review. sshd is key-only,
   so leaving everything empty is allowed only after a warning: the machine can
   then be used only from its own keyboard.
5. **Pengy**: the assistant's base URL, API key (masked) and model. Optional:
   leave the URL empty and Pengy keeps the image's defaults.
6. **Logging in** (Desktop images only): log in automatically or ask for the
   password (the default).
7. **Review**: the summary. Type `erase` to install.

Then a progress page, and a last page that says how to reach the machine
(`ssh <user>@<hostname>`).

Nothing is written before `erase`. Ctrl-C (or Esc on the first page) asks
before quitting, and quitting before `erase` changes nothing. If the install
fails, the error is shown and Enter returns to the review page with every
answer kept.

## Usage

```console
$ pengyos-install-tui install [--payload DIR] [--plain] [--reboot]
```

- **`--payload DIR`**: the payload directory (default: the stick's
  `/run/pengyos/payload`, mounted if needed).
- **`--plain`**: ask line by line instead of drawing the full-screen installer.
  It is also used automatically when stdin/stdout are not a TTY, `TERM` is
  unset or `dumb`, or the terminal is smaller than 80x24.
- **`--reboot`**: once installed, restart when the user presses Enter (the
  stick's launcher, `/usr/libexec/pengyos/console-installer`, passes it).

The launcher also switches to the 8x16 console font when the default 16x32 one
leaves the console under 80x24 (a 1024x768 panel is 64x24 with it), so old
laptops get the full-screen installer too.

## Layout

```
pengyos_install_tui/
  session.py   # the pages, fields and rules: pure, no I/O, shared by both front-ends
  screen.py    # full screen: App (keys in, frames out; no curses) + the curses driver
  plain.py     # the line-by-line fallback
  keys.py      # import SSH keys: GitHub, a URL, or user@host over SSH
  install.py   # load the medium, pick a front-end, run the install
  cli.py       # argparse
tests/
  test_session.py      # pages, validation, SSH key sources, review
  test_screen.py       # the full-screen App driven by key names, read from frames
  test_terminal.py     # the real curses driver in a pseudo-terminal
  test_install.py      # the plain front-end end to end (fake disks and install)
  test_keys.py         # key sources, with a fake opener and a fake ssh
  test_scaffold.py     # package and CLI wiring
  test_github_ready.py # README/LICENSE/CHANGELOG/.gitignore/pyproject contract
```

Every answer is validated by the engine's own
`pengyos_install.image.ProvisionAnswers.validate`, so the console installer, the
GUI and the engine cannot disagree about what is acceptable.

## Development

Python ≥ 3.11, standard library only (`curses` ships in the image's Python).

```console
$ python3 -m pytest -q
```

The suite is hermetic (no network, no root, no disks). The tests put
`tools/pengyos-install/src` on `sys.path` for the engine. `spec.md` holds the
contract, and `docs/qemu-testing.md` (§5b) covers testing the stick under QEMU.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
