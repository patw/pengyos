# pengyos-printers — "Printers", add printers and manage the print queue

## Summary
A small libcosmic app for the Desktop that does what people need to do with a
printer: **add** one (found on USB or the network, or by address), **remove**
one, **see and manage the print queue** (cancel, hold, resume), **pause** a
printer, choose the **default**, and **print a test page**. It talks to CUPS
directly; the app never runs as root.

## Purpose
US-DSK-13 asks for "a way to *add* a printer from the desktop". COSMIC Settings
(epoch 1.9) has no printer page. Driverless network printers already appeared on
their own (cups-browsed), but adding a USB or older printer, clearing a stuck
queue or printing a test page meant CUPS' web pages in a browser
(`pengyos-printers.desktop`, now replaced), which ask for a password and speak
CUPS' vocabulary.

## Deliverable
A Rust crate `pengyos-printers` (MIT), built like `pengyos-disks`:
- `ipp.rs` — std only: IPP/1.1 encoding of requests and decoding of responses
  (1setOf values folded, collections and unknown tags skipped).
- `lib.rs` — std only, `cargo test`-able with no display and no CUPS: the
  models, status words, device merging, driver ranking, names, addresses, the
  requests.
- `cups.rs` — std only: HTTP over CUPS' Unix socket, PeerCred auth.
- `app.rs` (feature `cosmic`) — the window.

## Interface
- `pengyos-printers` — the window. `--add` opens it on the Add page.
- `pengyos-printers --list` — printers, the queue and recent documents.
- `pengyos-printers --find` — the printers CUPS can find and the driver the
  app would choose for each (admin only, like adding).

## Behaviour
**Transport.** IPP over HTTP/1.1 to `$CUPS_SERVER` when it is a socket path,
else `/run/cups/cups.sock`; one connection per request (`Connection: close`);
answers may be chunked (CUPS-Get-Devices is). Every request carries
`requesting-user-name` and `Authorization: PeerCred <login name of the uid>`.
HTTP 401/403 and IPP not-authorized/forbidden/not-authenticated become "only
administrators (members of the wheel group) can change printers".

**Main page**, re-read every 2 s (CUPS-Get-Printers, CUPS-Get-Default, Get-Jobs
which-jobs=not-completed and completed, my-jobs=false):
- each printer: title (its description, unless that is just the queue name, as
  cups-browsed sets it — then its model), model without the driver suffix,
  connection (USB for `usb:`, `hp:/usb`, ipp-usb's `localhost:6xxxx`; Network
  for IPP/mDNS/socket/LPD and cups-browsed queues), status (Ready / Printing /
  Paused / Stopped + the state reasons in words + the message when stopped by
  CUPS), supply levels when reported (named, or "Toner 90 %, …"). Buttons:
  Print test page, Pause or Resume, Make default (not on the default), Remove…
  (confirmed; the dialog says how many documents are cancelled and that an
  automatic printer comes back).
- the queue: title, printer, owner, size, age, state (Waiting — "the printer is
  paused" when it is —, On hold, Printing + the printer's message, Stopped);
  Cancel, Hold (pending only) or Resume (held); Cancel all with two or more.
- recently finished: the newest five completed/cancelled/aborted jobs.

**Add page.** CUPS-Get-Devices (8 s backend timeout) and CUPS-Get-PPDs (all
drivers) start together. Found printers: bare backend names (`socket`, `lpd`,
…), `implicitclass:` and `beh:` are dropped; URIs of one mDNS service are one
printer, ranked driverless IPPS > driverless IPP > IPPS > IPP > dnssd > USB;
sorted by connection then name; marked "already added as …" when a queue has
the same URI, the same mDNS service, or is an automatic queue for the same
model. A typed address: a host/IP (optionally `:port`, IPv6 in brackets) becomes
`ipp://HOST/ipp/print`; `ipp`, `ipps`, `http`, `https`, `socket`, `lpd` URIs are
used as typed; anything else is refused with the reason.

**Setting up** is its own step. Name (prefilled with the model; the queue name
is derived from it: letters, digits, `-_.`, other runs → `_`, ≤ 100 chars,
case-insensitive clash → `_2`, `_3`…), Location (optional), Driver:
1. `driverless:<uri>` when CUPS lists it for this URI, or when the printer is
   driverless or speaks IPP (cups-filters makes it on request);
2. drivers whose `ppd-device-id` MFG+MDL equal the device's (Hewlett-Packard =
   HP);
3. drivers whose model equals the device's model;
4. the model family (maker + model core, e.g. "MFC-L3720CDW" of "… series").
Gutenprint's simplified flavour before expert; fax drivers never. A search box
adds drivers containing every typed word. The caption says whether the chosen
driver is recommended, matched, or from the search.
Add = CUPS-Add-Modify-Printer with `ppd-name`, `device-uri`, `printer-info`,
`printer-location`, accepting, idle, **not shared**; then CUPS-Set-Default if
there was no default.

**Test page.** Print-Job of `/usr/share/cups/data/testprint` named "Test
Page", its `document-format` read from its first bytes (`#PDF-BANNER` →
`application/vnd.cups-pdf-banner`, `#CUPS-BANNER` →
`application/vnd.cups-banner`, `%PDF` → `application/pdf`, else
`application/octet-stream`).

## Non-goals
Scanning (SANE/airscan work from scanning apps); per-printer options (paper
size, duplex — print dialogs set them); sharing printers on the network;
classes; remote CUPS servers; a password prompt for non-admins.

## Acceptance
- `cargo test` passes, including decoding Get-Printers, Get-Default (none),
  Get-Jobs, Get-Devices (chunked on the wire) and Get-PPDs answers captured from
  a real cupsd, and the request encodings read back.
- On a Desktop image as a wheel user: a driverless network printer is found
  and added with no password; a test page prints; a paused printer keeps a job
  waiting, Hold/Resume/Cancel work on it; Make default and Remove work.
- As a non-wheel user: printers and the queue are listed; changing anything
  says only administrators can.
