# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [Unreleased]

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
