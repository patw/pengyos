# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [Unreleased]

### Fixed — no screen capture while nobody is connected (2026-11-17)
- **The server captured the screen around the clock.** It started the portal
  session at login and kept its PipeWire stream running, so cosmic-comp and the
  portal produced 1080p frames for nobody and this process copied every one:
  ~5% of a core here on behotv with no client in 25 hours, and a live video
  link in the graph at all times. The stream now connects inactive and runs only
  while a client is connected (the session stays, so there is still no consent
  dialog and no delay beyond the first frame); it pauses 10 s after the last
  client leaves. Measured on behotv: this process 4.7% -> 0% idle; two FreeRDP
  connections resumed and paused cleanly.
- The stall check (a paused stream means a dead portal backend) now applies only
  while a client is watching, since an idle pause is ours. A backend that died
  while idle is caught when the next client connects and the capture does not
  start within 5 s, and the session is replaced.

### Fixed — remote desktop survives the monitor being switched off or blanking
- **A monitor that went away killed the session silently.** The portal session
  shares one output object; when the monitor is switched off, or goes into
  standby and briefly drops off HDMI (the LG UltraGear on behobasement does on
  every screen blank), cosmic-comp replaces the output and the session dies with
  its stream still "running" and no buffers, and its libei input closed. Clients
  got a black or frozen screen and dead input until someone restarted things by
  hand. The server now watches the compositor's `wl_output` globals and replaces
  the portal session whenever they change, the stream errors, input fails three
  times running, or the portal closes the session. The RDP listener and frame
  channels outlive sessions, so a connected client sees frames resume.
- **A crashed portal backend is recovered from in about 2 s.** cosmic-portal can
  panic mid-session (a `duplicate_frame` race in cosmic-client-toolkit's frame
  handling, hit after the compositor fails a frame); the stream is left Paused
  with no `Closed` signal. A stream out of Streaming for 2 s now replaces the
  session at once, which also starts a new backend. `RDP_CURSOR=embedded` paints
  the cursor into frames instead of sending it as an RDP pointer (tried against
  that panic; it is not the cause, so metadata stays the default).
- **No display at start no longer crash-loops.** The server waits for an output
  instead of failing `Start` with the portal's "No output" every 5 s.
- **Stopping the server closes its portal session.** A killed server left the
  portal capturing for nobody (two days on behobasement), and the next session on
  that portal never streamed. SIGTERM and Ctrl-C now stop the PipeWire loop and
  call `Session.Close`.

## [0.1.0] - 2026-10-31

### Added
- The RDP server, from the 2026-10-21..23 prototype (pengyos-remote-proto): the
  portal + PipeWire + ironrdp path, cursor as an RDP pointer, keyboard (scancodes
  and Unicode), mouse and wheel, takeover by a new client.
- Configuration from `~/.config/pengyos-remote` instead of environment
  variables; the RDP user is the login name; a password file others can read is
  refused.
