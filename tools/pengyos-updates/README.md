# pengyos-updates — "PengyOS Updates"

The desktop half of PengyOS updates (the root half is
[`pengyos-update`](../pengyos-update/)).

- **The app** (`pengyos-updates`, *PengyOS Updates*: in the App Library and pinned to the dock):
  shows the running version and the last daily check, what is available and
  its release notes, **Update and restart** or **Update, restart later** (installs
  into the other system slot in the background, with progress; the first
  restarts by itself when it's done, unless you press *Don't restart*),
  **Restart now** once it is installed, *Remind me*
  tomorrow / in 3 days / in a week / in a month, **Check now**, a switch for
  notifications, a switch for the testing channel, and **Go back** to the
  previous version.
- **The notifier** (`pengyos-updates --notifier`, from
  `/etc/xdg/autostart`): every 5 minutes it reads root's saved check result and
  the user's settings and, only when wanted, posts one notification ("26.09.3
  is available" / "Restart to finish updating"). Clicking it opens the app.
  Each thing is announced at most once a day; a snoozed release stays quiet
  until the snooze ends, but a newer release is announced right away.

The machine checks daily either way (`pengyos-update-check.timer`, as root);
the user's choices (`~/.config/pengyos-updates/settings.json`) only decide what
is shown. Privileged actions are systemd units started with `systemctl`;
`/etc/polkit-1/rules.d/60-pengyos-update.rules` lets administrators (wheel)
start them without a password.

## Build

```console
cargo test                                    # the decision logic, no display needed
CARGO_TARGET_DIR=~/cosmic-build/install-gui cargo build --release --features cosmic
```

The Desktop post-build installs `~/cosmic-build/install-gui/release/pengyos-updates`.
Without `--features cosmic` the binary prints what the app would show.
