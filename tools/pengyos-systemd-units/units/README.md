# units/ — Core systemd unit set

This directory holds the Core PengyOS systemd unit files:

- `*.service` — service units (e.g. sshd, pengy-agent).
- `*.target` — synchronization point / grouping units.
- `*.socket` — socket units for socket-activated services.

Units are authored as INI-style key/value files (the format systemd consumes).
They are validated by `pengyos_systemd_units.validator` and exercised by the
pytest suite.
