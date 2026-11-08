# Changelog

## 0.1.0 — 2026-11-08

- First version: `status [--json]`, `detect`, `profile quiet|balanced|full|firmware`,
  `name`, `role`, `follow chip|cpu|gpu|cpu+gpu`, `apply`, and the service's `run`
  and `release`. Nuvoton (Smart Fan IV) channels get the profile written into the
  chip's own curve points, with the critical point left alone. Other Super-I/O
  chips and USB fan controllers, and any fan following the GPU, get a software
  curve that is handed back when the service stops. Laptops get the firmware's
  `platform_profile`. Built from the 2026-11-08 survey of six machines.
