# Changelog — pengyos-updates

## [0.3.0] — 2026-10-11

### Added
- The app lane: the notifier also asks Flatpak for app updates
  (`flatpak remote-ls --updates --app`, system and user installations, no
  privileges) after login and every 6 hours, and posts one notification —
  "3 app updates are available: Discord, Firefox and Google Chrome" — that opens
  COSMIC Store. The same set is not repeated within a day; a new update is
  announced at the next check. The notifications switch covers both lanes.
  Before this nothing told anyone an app could be updated: COSMIC Store checks
  only while it is open, and never notifies (behoweather, 26.10.22).

## [0.2.0] — 2026-10-01

### Added
- Choose when to restart when an update is offered: **Update and restart** (restarts by itself
  once it is installed; *Don't restart* cancels that while it installs) or
  **Update, restart later**. Replaces the single *Update now* button.

## [0.1.0] — 2026-09-30

### Added
- The PengyOS Updates app (libcosmic) and the session notifier, with snoozing
  ("remind me" 1 day / 3 days / a week / a month), a notifications switch, the
  testing channel switch, install progress, restart and go-back.
