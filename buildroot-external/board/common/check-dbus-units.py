#!/usr/bin/env python3
"""Fail the build if a dbus-activated service names a systemd unit the image
doesn't ship.

    check-dbus-units.py TARGET_DIR

Both buses in a PengyOS image run `dbus-daemon --systemd-activation`
(`dbus-daemon --session --address=systemd: --systemd-activation` for the user
session, `--system --systemd-activation` for the system bus).  For those daemons
a file under /usr/share/dbus-1/services or /usr/share/dbus-1/system-services is
a *pointer*: its `SystemdService=` key names a **unit**, and activation is
answered by systemd, which resolves that name only through its unit search
paths.  A .service file under /usr/share/dbus-1/services is not a unit.

Ship the pointer without the unit and the failure is silent at build time and
invisible in the image — it surfaces at runtime as `Unit X.service not found`
in the journal, `ServiceUnknown` to whoever asked, and a feature a person
simply cannot use.  Found on behoweather 26.10.14 (2026-10-08): the COSMIC
panel logged

    com.system76.CosmicAppletStatusArea: Failed to start status notifier watcher:
        org.freedesktop.systemd1.NoSuchUnit: Unit com.system76.CosmicStatusNotifierWatcher.service not found.
    com.system76.CosmicAppletStatusArea: Failed to register status notifier host:
        org.freedesktop.DBus.Error.ServiceUnknown: The name org.kde.StatusNotifierWatcher ...

and every tray icon (Discord, Steam, SyncThingy, …) was missing from the panel.

Files with `Exec=` and no `SystemdService=` are skipped: dbus activates those
itself.  Template units are accepted for an instance name (`foo@bar.service`
resolves against `foo@.service`).
"""
import os
import sys

# (dbus servicedir, systemd unit dir) — the bus each directory serves.
BUS_DIRS = [
    ("services", "user"),
    ("system-services", "system"),
]
# systemd's unit search paths, in the order it looks them up (the image's own
# tree: /etc is the mutable overlay, /usr/lib is the image).
UNIT_PATHS = ["etc/systemd/{scope}", "run/systemd/{scope}", "usr/lib/systemd/{scope}"]


def systemd_service_target(path):
    """The unit name dbus should activate for this service file, or None."""
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("SystemdService="):
                    unit = line.split("=", 1)[1].strip()
                    return unit or None
    except OSError:
        return None
    return None


def unit_bus_names(target_dir):
    """BusName -> unit path, for every unit file the image ships.

    systemd's rootfs preset hook (Buildroot's SYSTEMD_PRESET_ALL, run while
    assembling the rootfs image, not in target/) creates a
    `dbus-<BusName>.service` alias for every unit that declares a BusName.  Those
    aliases are therefore in the image but not in the tree a post-build script
    sees, and a SystemdService= naming one of them is not a defect.  Verified on
    behoweather 26.10.14: all 8 /etc/systemd/system/dbus-org.*.service aliases
    point at a unit carrying the matching BusName (bluetooth=org.bluez,
    avahi-daemon=org.freedesktop.Avahi, ModemManager, thermald, systemd-resolved,
    systemd-timesyncd, NetworkManager-dispatcher, NetworkManager).
    """
    found = {}
    for scope in ("user", "system"):
        for rel in UNIT_PATHS:
            d = os.path.join(target_dir, rel.format(scope=scope))
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if not name.endswith((".service", ".busname")):
                    continue
                path = os.path.join(d, name)
                if not os.path.isfile(path):
                    continue
                try:
                    with open(path, "r", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("BusName="):
                                bus = line.split("=", 1)[1].strip()
                                if bus:
                                    found.setdefault(bus, path)
                except OSError:
                    continue
    return found


def unit_exists(target_dir, unit, scope, bus_names):
    """Is `unit` shipped — as a unit file, a template it instantiates from, or the
    `dbus-<BusName>.service` alias systemd generates for a unit that declares it?"""
    names = [unit]
    if "@" in unit and not unit.startswith("@"):
        # foo@bar.service -> the foo@.service template
        prefix, _, rest = unit.partition("@")
        names.append(f"{prefix}@.service")
    for rel in UNIT_PATHS:
        for name in names:
            if os.path.exists(os.path.join(target_dir, rel.format(scope=scope), name)):
                return True
    if unit.startswith("dbus-") and unit.endswith(".service"):
        alias = unit[len("dbus-"):-len(".service")]
        # systemd spells '_' as '-' in the alias name (nm_dispatcher -> nm-dispatcher)
        for bus, path in bus_names.items():
            if bus == alias or bus.replace("_", "-") == alias:
                return True
    return False


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[3], file=sys.stderr)
        return 2
    target = argv[1]
    if not os.path.isdir(target):
        print(f"check-dbus-units: no such directory: {target}", file=sys.stderr)
        return 2

    checked, findings = 0, []
    bus_names = unit_bus_names(target)
    for dbus_dir, scope in BUS_DIRS:
        d = os.path.join(target, "usr/share/dbus-1", dbus_dir)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".service"):
                continue
            path = os.path.join(d, name)
            if not os.path.isfile(path):
                continue
            unit = systemd_service_target(path)
            if unit is None:
                continue  # dbus fork-activates these itself
            checked += 1
            if not unit_exists(target, unit, scope, bus_names):
                findings.append(f"usr/share/dbus-1/{dbus_dir}/{name} activates through "
                                f"{scope} unit {unit}, which the image does not ship")

    # Report what was examined before the verdict: a check that only speaks when
    # it fails cannot be told apart from a check that looked at nothing.
    print(f"check-dbus-units: {checked} dbus-activated service(s) checked")
    for f in findings:
        print(f"check-dbus-units: {f}", file=sys.stderr)
    if findings:
        print(f"check-dbus-units: {len(findings)} dbus-activated service(s) point at a "
              f"missing unit — activation fails at runtime and the feature is absent "
              f"(journal: 'Unit <name>.service not found').", file=sys.stderr)
        return 1
    print("check-dbus-units: every SystemdService= resolves to a shipped unit")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
