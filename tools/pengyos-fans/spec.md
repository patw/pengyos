# pengyos-fans: spec

## Why

PengyOS shows fans (the Hardware Monitor) but could not set them. Asked for fan
curves, real-time control and profiles, the survey of six machines (2026-11-08)
found:

- the two desktops (behopc: NCT6798D, behotv: NCT6797D) have 2-3 controllable
  fans each, already running curves the BIOS wrote into the chip (Smart Fan IV);
- the laptop has firmware profiles only (`platform_profile`);
- the mini PCs have nothing: the embedded controller owns the fan.

Linux fan control is reliable where the chip keeps running a curve by itself,
and fragile where a program has to keep writing a duty: if that program dies,
the fan stays at whatever it was last told. So the design leaves the hardware in
charge wherever it can be, and keeps the software part small, restartable and
failing towards full speed.

## Words

- **Channel**: one PWM output, `CHIP:pwmN` (`nct6798:pwm2`, `corsaircpro:pwm3`).
  A second chip with the same name is `CHIP.2`. Channels are found by chip name
  and device path, never by `hwmonN` (which changes between boots).
- **Role**: `fan` (it drives a fan with a speed reading: found by `detect`),
  `pump` (its fan's label says pump, or the operator said so), `none` (no fan
  answered; may be an empty header or a pump without a tach). Only `fan`
  channels follow profiles; the others are never written outside `detect`.
- **Chip curve**: an NCT677x/NCT679x channel in Smart Fan IV (`pwmN_enable=5`):
  the profile is written into the chip's own curve points and the chip runs it.
  Nothing has to keep running.
- **Software curve**: every other controllable channel (ITE, Fintek, Winbond,
  USB controllers), and a chip-curve channel told to follow the GPU (the chip
  cannot read the GPU's temperature). `pengyos-fans run` (the service) sets its
  duty every 2 seconds.
- **Profile**: `quiet`, `balanced`, `full`, or `firmware` (hand everything back
  to what the BIOS or device set up). `firmware` is the default.

## Contract

1. **status** (any user, the default command): the profile in effect; each
   channel with its name, role, fan, RPM, duty and who controls it (chip curve
   and its temperature source, software curve and what it follows, or left
   alone). `--json` gives the same for the Hardware Monitor. With controllable
   channels but no `detect` yet, it says to run `sudo pengyos-fans detect`. With
   only `platform_profile`, it shows the firmware profiles. With neither, it says
   the firmware runs the fans and there is nothing to control (exit 0).
2. **detect** (root): for every controllable channel, one at a time: remember
   `pwmN_enable`, `pwmN` and the curve points; set full speed for 6 seconds,
   then 20 % for 9; the fan whose RPM differs most between the two (at least
   max(150, 25 %)) is the one it drives. If it stopped at 20 %, try 30, 40, 50,
   60 % from standstill: the first that turns it, plus 5, is its `min_duty` (70
   if none). A pump (fan label) is only sped up. Then write the remembered values
   back and read them again. A signal or an error mid-test
   restores the channel first. The map and the remembered values (the
   **baseline**) go to `/etc/pengyos/fans.json`; names and roles set before keep.
   It then applies the saved profile.
   Controllable means: a Super-I/O chip (nct, it87, f71/f81, w83) or a USB
   device, with a writable `pwmN`. GPU, laptop and PSU drivers are excluded:
   their fans belong to their own driver or firmware.
3. **profile NAME** (root): save and apply.
   - Chip-curve channels: write the profile's points into the first N-1 curve
     points; the last point (the chip's critical temperature, full speed) is
     never changed. Temperatures stay below it. `firmware` writes the baseline
     back.
   - Software channels: take manual control (`pwmN_enable=1` where there is
     one) and set the first duty now; the service keeps it up. `firmware`
     writes the baseline `pwmN_enable` back when the device has an automatic
     mode of its own, the baseline duty when it had one, or full speed when it
     has neither (a Commander Pro has no curve to return to), and says which.
   - Laptops (`platform_profile` and no controllable channel): quiet, balanced,
     full map to low-power (or quiet), balanced, performance; through
     power-profiles-daemon when it runs (it remembers the choice), otherwise
     straight to sysfs, saved and reapplied at boot.
   - Duty is never below 20 %, nor below the channel's `min_duty`, on a `fan`
     channel, so a fan never stops.
4. **name CHANNEL TEXT**, **role CHANNEL fan|pump|none**, **follow CHANNEL
   chip|cpu|gpu|cpu+gpu** (root): edit the map and apply. `chip` is only for
   chip-curve channels. `gpu` curves are shifted 10 °C up (GPUs run hotter by
   design); `cpu+gpu` takes the higher duty of the two, and is the CPU curve
   alone while no GPU temperature can be read. `gpu` and `cpu+gpu` are refused
   (exit 2) when no GPU temperature can be read at all.
5. **curve CHANNEL TEMP:DUTY ...|default** (root): the channel's own curve,
   used instead of the profile's by `quiet` and `balanced` (not `full`, not
   `firmware`). 2-8 points, temperatures 0-100 rising, duties not falling, the
   last 100 %; a chip-curve channel takes at most its chip's points less one.
   Exit 2 otherwise.
6. **apply** (root): apply the saved profile again. Run at boot by the service,
   and after resume by a systemd sleep hook (some chips forget on suspend).
7. **run** (root, the service): apply, then drive the software channels until
   stopped. A temperature that cannot be read for 3 ticks in a row sets that
   channel to full speed. Duty goes up at once and down by at most 4 points per
   tick. The map is re-read when the file changes. With no software channel it
   exits 0 after applying. On SIGTERM or SIGINT, and again from `release`
   (ExecStopPost, for a killed process), each software channel is handed back:
   a chip-curve channel gets the profile as a chip curve, others their device's
   automatic mode, else full speed. Never left at a low fixed duty.
8. Every writer takes `/run/pengyos-fans.lock`; `run` skips a tick when it is
   held, so `detect` is never fought.
9. Changing commands restart the service (`systemctl restart`), except under
   `--root` (tests).
10. Exit: 0 ok; 2 usage; 3 nothing to control; 4 not detected yet; 5 not root;
   6 unknown channel; 7 a write failed (the channel was restored).

## Acceptance

- The unit suite drives the shipped script against a sandbox root holding fake
  hwmon chips whose fans respond to their PWMs: detect, every profile on chip
  and software channels, the critical point left alone, the 20 % floor,
  follow/name/role, the laptop path, the run loop (ramp, failure to full
  speed, release on SIGTERM), and status text and JSON.
- On behopc and behotv: `detect` maps the same fans the survey did, each profile
  changes RPM under load, `firmware` gives back the BIOS curve byte for byte,
  and a `kill -9` of the service leaves no fan at a low fixed duty.
