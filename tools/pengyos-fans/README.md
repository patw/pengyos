# pengyos-fans

Fan profiles and curves for the fans this machine can actually control.

```sh
pengyos-fans                                   # what can be controlled, and how it is set
pengyos-fans status --json                     # the same, for the Hardware Monitor
sudo pengyos-fans detect                       # which control drives which fan (spins each up)
sudo pengyos-fans profile quiet                # or balanced, full, firmware
sudo pengyos-fans name nct6798:pwm1 "CPU"
sudo pengyos-fans follow nct6798:pwm2 cpu+gpu  # a case fan that also cools the graphics card
sudo pengyos-fans role nct6798:pwm6 pump       # never touched by a profile
```

## What can be controlled

It depends on the hardware, and the tool says which case you are in:

| Machine | What you get |
|---|---|
| Desktop board with a Nuvoton chip (most ASUS, MSI, ASRock boards) | Every fan header with a fan on it. The profile is written into the chip's own curve. |
| Desktop board with an ITE, Fintek or Winbond chip | Every fan header with a fan on it, through a software curve. |
| USB fan controller or AIO cooler (Corsair Commander Pro, NZXT Kraken X53-Z73 and RGB & Fan Controller, Aquacomputer Octo/Quadro/D5 Next, ASUS ROG Ryujin) | Its fans, through a software curve. Pumps are left alone. |
| Laptop | The firmware's profiles (`platform_profile`): quiet, balanced, performance. |
| Most mini PCs | Nothing: the embedded controller owns the fan. |

GPU fans belong to the GPU driver and are not touched.

## Profiles

| Profile | Curve (°C → duty) |
|---|---|
| `quiet` | 40 → 20 %, 60 → 35 %, 75 → 60 %, 85 → 100 % |
| `balanced` | 35 → 30 %, 55 → 45 %, 70 → 70 %, 80 → 100 % |
| `full` | 100 % |
| `firmware` | Whatever the BIOS or the device set up (the default). |

A fan never goes below 20 %. A fan that follows the GPU uses the same curve 10 °C
higher, because GPUs are built to run hotter; `cpu+gpu` takes whichever of the two
asks for more, and is the CPU curve alone while no GPU temperature can be read. A
fan following `gpu` alone goes to full speed when it cannot be read.

## How it stays safe

Software fan control is fragile in one way: if the program that sets the duty
dies, the fan stays wherever it was last told. So:

- **The chip runs the curve wherever it can.** On a Nuvoton chip in Smart Fan IV
  (how every board in the survey shipped) the profile goes into the chip's own
  curve points. Nothing has to keep running; a crash of anything changes nothing.
  The last point, the chip's critical temperature at full speed, is never changed.
- **The service only drives what the chip cannot.** `pengyos-fans.service` sets
  the duty of USB controllers, non-Nuvoton boards and GPU-following fans every
  2 seconds. Speeding up is immediate; slowing down is at most 4 points a tick.
  A temperature it cannot read for 6 seconds sends that fan to full speed.
- **Stopping hands fans back.** When the service stops, crashes or is killed, each
  fan it drove goes back to the chip's curve (with the same profile), the device's
  own automatic mode, or full speed. Never to a low fixed duty.
- **`firmware` means hands off.** At boot, after resume and after `detect`, the
  firmware profile writes nothing. Choosing it writes back exactly what `detect`
  found. A Commander Pro has no curve of its own to go back to, so it goes to full
  speed, and the tool says so.

## detect

```
$ sudo pengyos-fans detect
Testing 5 fan control(s): each runs at full speed for about 6 seconds, then goes back.
  nct6798:pwm1: drives nct6798:fan1 (538 -> 1562 RPM)
  nct6798:pwm2: drives nct6798:fan2 (374 -> 1523 RPM)
  nct6798:pwm4: drives nct6798:fan4 (513 -> 1548 RPM)
  nct6798:pwm5: no fan answered
  nct6798:pwm6: no fan answered
Saved to /etc/pengyos/fans.json.
```

Each control is set to full speed for 6 seconds; a fan whose speed rises by at
least 150 RPM (and 15 %) is the one it drives. Its settings are then written back
and read again; Ctrl-C mid-test restores it first. A control with no fan answering
may be an empty header, or a pump or fan without a speed wire: it is left alone
(`role ... fan` to include it). Run `detect` again after moving fans around; names
and roles you set are kept.

Channels are named `CHIP:pwmN` and found by chip and device path, so they survive
reboots, where `hwmonN` numbers do not. A second identical controller is `CHIP.2`.

## Files

- `/usr/bin/pengyos-fans` (in the Server overlay, so Server and Desktop).
- `/etc/pengyos/fans.json`: the map, the settings `detect` found, the profile.
  On the data partition, so it survives updates.
- `pengyos-fans.service`: runs only once that file exists.
- `/usr/lib/systemd/system-sleep/pengyos-fans`: `apply` after resume.

The suite (`tests/`) drives the script against fake hwmon chips whose fans follow
their PWMs. See `spec.md` for the contract.
