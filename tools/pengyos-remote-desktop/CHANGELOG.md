# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [0.1.1] - 2026-10-31

### Fixed
- The certificate fingerprint ran off the right edge of the window, even
  maximized: one 95-character token cannot wrap. It is now a Certificate row
  with the fingerprint in two monospace lines of 16 bytes. The column is
  600 px wide (was 560) and the window opens at 680×720.

## [0.1.0] - 2026-10-31

### Added
- The Remote Desktop window: on/off, how to connect, the RDP password, ask again.
  Every change runs `pengyos-remote`.
