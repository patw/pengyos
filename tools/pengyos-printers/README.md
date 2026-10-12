# pengyos-printers — "Printers"

Add and remove printers, see and manage the print queue, and print a test page
on the PengyOS Desktop. COSMIC Settings (epoch 1.9) has no printer page; before
this app the "Printers" launcher opened CUPS' web pages in a browser.

- **Printers**: each with its model, connection, status in words ("Ready ·
  Toner or ink low", "Paused", "Stopped · The printer is not responding.") and
  supply levels, and **Print test page**, **Pause**/**Resume**, **Make
  default**, **Remove…**. Printers cups-browsed added on its own are marked:
  removing one lasts only until it is seen on the network again.
- **Print queue**: every waiting or printing document, whoever sent it, with
  **Cancel** and **Hold**/**Resume**; **Cancel all** when there are several.
  Below it, the last few finished ones (printed, cancelled, failed).
- **Add printer…**: the printers CUPS finds on USB and the network, one entry
  each (a network printer answers on several addresses; the driverless one is
  used), with the ones already set up marked; or type an address
  (`192.168.1.20`, `printer.local`, or a full `ipp://`, `ipps://`, `socket://`,
  `lpd://` URI). Then a name, a location and a driver: **driverless (IPP
  Everywhere)** whenever the printer supports it, which needs nothing
  model-specific; otherwise the Gutenprint/HPLIP/CUPS drivers that match its
  device ID or model, and a search over all ~5,500 of them. The first printer
  added becomes the default.

## How it works

The app speaks IPP to cupsd over its local socket (`/run/cups/cups.sock`, which
also starts cupsd through `cups.socket`); there is no web page and no
`lpadmin`. Every request carries `Authorization: PeerCred <user>`: cupsd checks
the name against the connecting process's uid, so a member of a CUPS admin group
— `wheel` on PengyOS, set as `SystemGroup` by the Desktop post-build — adds and
removes printers with no password prompt. Anyone else sees the printers and the
queue and is told that only administrators can change printers.

- `src/ipp.rs` — the IPP wire format (encode a request, decode a response), std only.
- `src/lib.rs` — std only: the printer, job, device and driver models read from
  CUPS' answers, status words, which found printer is offered and how, driver
  ranking, queue names, address parsing, and every request the app sends.
- `src/cups.rs` — the transport: HTTP over the Unix socket (chunked or not).
- `src/app.rs` (feature `cosmic`) — the window.

The test page is the one CUPS' own web interface prints (`testprint`); on
PengyOS it is cups-filters' PDF banner, so its format is read from the file
(declaring CUPS' `application/vnd.cups-banner` is refused as "Unsupported
format").

## Use

```console
pengyos-printers            # the window
pengyos-printers --add      # the window, on the Add page (the launcher's "Add a printer")
pengyos-printers --list     # printers, the queue and recent documents, no window
pengyos-printers --find     # the printers CUPS can find and the driver chosen for each
```

## Tested

`cargo test` decodes answers captured from behoweather's cupsd (`tests/data/`).
On behoweather itself: every action against a real network Brother and a
throwaway `socket://` queue whose output went to a listener — see
[`CHANGELOG.md`](CHANGELOG.md). See [`spec.md`](spec.md) for the contract.

## Build

```console
cargo test                                   # no display, no CUPS
cargo run -- --list                          # against the host's CUPS
CARGO_TARGET_DIR=~/cosmic-build/install-gui cargo build --release --features cosmic
```

`scripts/build-cosmic.sh pengyos-printers` does the last one; the Desktop
post-build installs `~/cosmic-build/install-gui/release/pengyos-printers`, and
the overlay carries `io.catbee.pengyos.Printers.desktop`.
