# Changelog

## 0.2.0 — 2026-11-08

- `detect` compares full speed with 20 % instead of with how the fan was found,
  so a fan that was already fast is found (behoserver's case fans under load were
  missed). A fan that stops at 20 % gets its own minimum: the lowest duty that
  restarts it, plus 5 % (behotv's fan4 stopped on `quiet`). Pumps are only sped
  up.
- `curve CHANNEL TEMP:DUTY ... | default`: a fan's own curve. `status --json`
  reports each fan's curve, whether it is its own, and its minimum.

## 0.1.0 — 2026-11-08

- First version: `status [--json]`, `detect`, `profile quiet|balanced|full|firmware`,
  `name`, `role`, `follow chip|cpu|gpu|cpu+gpu`, `apply`, and the service's `run`
  and `release`. Nuvoton (Smart Fan IV) channels get the profile written into the
  chip's own curve points, with the critical point left alone. Other Super-I/O
  chips and USB fan controllers, and any fan following the GPU, get a software
  curve that is handed back when the service stops. Laptops get the firmware's
  `platform_profile`. Built from the 2026-11-08 survey of six machines.
