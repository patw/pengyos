# Changelog — pengyos-printers

## [0.1.0] — 2026-10-12

### Added
- The Printers app (libcosmic, over IPP to cupsd's local socket): the
  printers with their status, supply levels and default; **Print test page**,
  **Pause/Resume**, **Make default** and **Remove…** for each; the print queue
  with **Cancel**, **Hold/Resume** and **Cancel all**, refreshed every two
  seconds; the last few finished documents. **Add printer…** lists the printers
  CUPS' backends find on USB and the network (one entry per printer, driverless
  first, ones already set up marked) or takes a typed address, then a name, a
  location and a driver: driverless (IPP Everywhere) when the printer supports
  it, else the drivers whose IEEE-1284 device ID or model matches, with a search
  over all of them. The first printer added becomes the default. `--list` and
  `--find` answer without a window; `--add` opens on the Add page.
- Wheel members change printers with no password: requests carry
  `Authorization: PeerCred`, which cupsd checks against the socket's peer uid.
  Anyone else can see printers and the queue, and is told that only
  administrators can change them.
- Replaces `pengyos-printers.desktop`, which opened CUPS' web pages in a
  browser.

### Verified
- On behoweather (26.10 Desktop, CUPS 2.4, cups-filters 1.28) against a real
  Brother MFC-L3720CDW on the network and a throwaway `socket://` queue: add
  (driverless, 3 s; generic PostScript), make default, pause, test page (the
  job waits, then 900 KB of PostScript reached the listener), hold, release,
  cancel, resume, remove; a non-wheel user refused. The main page, the Add page
  and the setup step checked by screenshot; the clicks themselves still need a
  person.
