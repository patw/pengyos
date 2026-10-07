"""Collectors: turn a :class:`~pengyos_hwprofile.source.Source` into a profile.

Each collector reads one hardware category from a source directory (a fixture
in tests, a ``/sys``+``/proc`` mirror in production) and returns a plain
``dict`` / ``list`` shaped exactly like the corresponding section of the schema
in :mod:`pengyos_hwprofile.schema`. ``collect_all`` assembles every section into
the full document; :func:`collect` / :func:`collect_profile` additionally
validate it, so what comes out here is always a legal ``profile.toml`` payload.

The collectors are pure over :class:`Source` — no live filesystem access, no
subprocess, no network — which is what makes the whole suite runnable with no
root and no hardware. The ``detect`` CLI (later subtask) simply opens a
directory source and serialises what these functions return.

Layout the source is expected to expose (all paths are relative to the source
root, mirroring a real ``/sys`` + ``/proc`` tree):

* ``proc/cpuinfo`` — CPU vendor / model / threads / mhz.
* ``proc/sys/kernel/hostname`` and ``proc/sys/kernel/osrelease`` — machine identity.
* ``class/dmi/id/*`` — system / board / chassis / bios identity (a subset of the
  DMI table; the rest is best-effort and dropped when absent).
* ``class/net/<iface>/*`` — network interfaces (address, speed, driver symlink).
* ``bus/pci/devices/<bus>/*`` — PCI devices, filtered to display / storage /
  network by their class code.
* ``class/nvme/nvme<n>/device/*`` and ``class/block/<disk>/*`` — storage.
* ``bus/usb/devices/<dev>/*`` — USB peripherals.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import __version__
from .parsers import parse_cpuinfo, parse_int, parse_pci_class
from .schema import Profile, ProfileError, load_profile
from .source import Source, SourceError

# ---------------------------------------------------------------------------
# Small read helpers — everything goes through ``Source.read`` so a missing file
# is a single, well-typed error rather than a scatter of ``FileNotFoundError``.
# ---------------------------------------------------------------------------


def _safe_read(source: Source, relpath: str) -> str | None:
    """Read ``relpath`` or return ``None`` if it is absent.

    Used for the many optional DMI fields where a machine may legitimately have
    none. A read error on a *present* file still propagates; only a genuinely
    missing file is tolerated here.
    """

    try:
        return source.read(relpath)
    except SourceError:
        return None


def _read_link(source: Source, relpath: str) -> str | None:
    """Return the basename of a symlink target, or ``None`` if there is none.

    sysfs ``driver`` entries are symlinks (``driver -> ../../../module/e1000e``).
    We only care about the module name (``e1000e``), so we resolve the link and
    return its final path component. Missing links (no driver bound) yield
    ``None``.
    """

    try:
        path = (source.root / relpath)
        if not path.is_symlink():
            return None
        return Path(os.readlink(path)).name
    except OSError:
        return None


def _list_dir(source: Source, relpath: str) -> list[str]:
    """Return the sorted entry names under ``relpath`` (or ``[]`` if absent).

    sysfs directories are real directories on disk; listing them is a plain
    ``os.scandir``. Sorting keeps iteration deterministic (and therefore the
    emitted profile byte-stable).
    """

    try:
        entries = [e.name for e in os.scandir(source.root / relpath) if e.is_dir()]
    except OSError:
        return []
    return sorted(entries)


# ---------------------------------------------------------------------------
# Metadata.
# ---------------------------------------------------------------------------


def collect_metadata(source: Source) -> dict:
    """Collect machine identity: hostname, kernel, tool version.

    ``hostname`` and ``kernel`` come from the source (``/proc/sys/kernel/*``),
    keeping the whole document reproducible from a fixture. ``tool_version`` is
    this package's version.
    """

    hostname = _safe_read(source, "proc/sys/kernel/hostname")
    kernel = _safe_read(source, "proc/sys/kernel/osrelease")
    if not hostname:
        raise ProfileError(
            "metadata.hostname: source has no proc/sys/kernel/hostname "
            "(add one to the fixture tree)"
        )
    if not kernel:
        raise ProfileError(
            "metadata.kernel: source has no proc/sys/kernel/osrelease "
            "(add one to the fixture tree)"
        )
    return {
        "hostname": hostname,
        "kernel": kernel,
        "tool_version": __version__,
    }


# ---------------------------------------------------------------------------
# CPU.
# ---------------------------------------------------------------------------


def collect_cpu(source: Source) -> dict:
    """Collect the CPU aggregate from ``proc/cpuinfo``.

    ``/proc/cpuinfo`` lists one block per *logical* processor. The number of
    blocks is the thread count; ``cpu cores`` (per block) is the core count;
    ``siblings`` is the logical count per physical package. We take vendor and
    model from the first block and mhz from the first block's ``cpu MHz``.
    """

    text = _safe_read(source, "proc/cpuinfo")
    if not text:
        raise ProfileError(
            "cpu: source has no proc/cpuinfo (add one to the fixture tree)"
        )

    processors = parse_cpuinfo(text)
    if not processors:
        raise ProfileError("cpu: proc/cpuinfo described no processors")

    first = processors[0]
    vendor = first.get("vendor_id") or first.get("cpu family") or "unknown"
    model = first.get("model name") or first.get("model") or "unknown"

    threads = len(processors)
    cores = int(first.get("cpu cores", "1") or "1")

    mhz_raw = first.get("cpu mhz")
    mhz = float(mhz_raw) if mhz_raw else 0.0

    return {
        "vendor": vendor,
        "model": model,
        "cores": cores,
        "threads": threads,
        "mhz": mhz,
    }


# ---------------------------------------------------------------------------
# Memory (DMI).
# ---------------------------------------------------------------------------


def collect_memory(source: Source) -> list:
    """Collect memory modules from DMI memory-device records.

    Each ``class/dmi/id/memory/N`` directory (N = 0, 1, 2, ...) describes one
    physical module. ``size`` is stored as ``"<n> MB"`` / ``"No Module
    Installed"``; :func:`_parse_dmi_size` converts it to bytes. Modules with no
    installed memory are skipped.
    """

    modules = []
    for name in _list_dir(source, "class/dmi/id/memory"):
        size = _safe_read(source, f"class/dmi/id/memory/{name}/size")
        size_bytes = _parse_dmi_size(size)
        if size_bytes is None:
            continue  # "No Module Installed" — not a real module.
        module = {
            "locator": _safe_read(source, f"class/dmi/id/memory/{name}/locator")
            or f"Slot {name}",
            "size_bytes": size_bytes,
        }
        manufacturer = _safe_read(source, f"class/dmi/id/memory/{name}/manufacturer")
        memory_type = _safe_read(source, f"class/dmi/id/memory/{name}/type")
        speed = _safe_read(source, f"class/dmi/id/memory/{name}/speed")
        if manufacturer:
            module["manufacturer"] = manufacturer
        if memory_type:
            module["memory_type"] = memory_type
        if speed:
            module["speed_mhz"] = _parse_dmi_speed(speed)
        modules.append(module)
    return modules


def _parse_dmi_size(text: str | None) -> int | None:
    """Convert a DMI size string (``"8192 MB"``) to bytes, or ``None``.

    DMI reports ``"No Module Installed"`` for empty slots and sizes in
    kB/MB/GB. Returns ``None`` for the empty-slot marker so the caller can skip
    it.
    """

    if not text:
        return None
    text = text.strip()
    if text.lower() == "no module installed":
        return None
    parts = text.split()
    if len(parts) != 2:
        raise ProfileError(f"memory: unparseable size {text!r}")
    value, unit = parts
    try:
        amount = parse_int(value)
    except ValueError:
        raise ProfileError(f"memory: unparseable size {text!r}") from None
    unit = unit.upper().rstrip(".")
    multiplier = {
        "KB": 1024,
        "MB": 1024 ** 2,
        "GB": 1024 ** 3,
        "TB": 1024 ** 4,
    }.get(unit)
    if multiplier is None:
        raise ProfileError(f"memory: unknown size unit in {text!r}")
    return amount * multiplier


def _parse_dmi_speed(text: str | None) -> int | None:
    """Convert a DMI speed string (``"3200 MHz"``) to MHz, or ``None``."""

    if not text:
        return None
    text = text.strip()
    parts = text.split()
    if len(parts) == 2 and parts[1].upper().startswith("M"):
        try:
            return parse_int(parts[0])
        except ValueError:
            return None
    # Some DMI tables report a raw number (MTS) with no unit.
    try:
        return parse_int(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# PCI devices -> gpu / storage / network.
# ---------------------------------------------------------------------------


def _pci_devices(source: Source) -> list[dict]:
    """Return every profiled PCI device with its class-derived category.

    Each entry is ``{"bus", "vendor", "device", "class", "category"}`` where
    ``category`` is ``"display"``, ``"storage"`` or ``"network"``. Devices whose
    class code is not one of those (bridges, host controllers, ...) are filtered
    out here.
    """

    devices = []
    for bus in _list_dir(source, "bus/pci/devices"):
        cls = _safe_read(source, f"bus/pci/devices/{bus}/class")
        category = parse_pci_class(cls) if cls else None
        if category is None:
            continue
        devices.append(
            {
                "bus": bus,
                "vendor": _safe_read(source, f"bus/pci/devices/{bus}/vendor"),
                "device": _safe_read(source, f"bus/pci/devices/{bus}/device"),
                "class": cls,
                "category": category,
            }
        )
    return devices


def collect_gpu(source: Source) -> list:
    """Collect display controllers (PCI class ``0x03``).

    The kernel driver is read from the device's ``driver`` symlink when one is
    bound (e.g. ``i915`` / ``amdgpu``); it is omitted when no driver is bound.
    """

    out = []
    for d in _pci_devices(source):
        if d["category"] != "display":
            continue
        record = _pci_to_gpu(d)
        driver = _read_link(source, f"bus/pci/devices/{d['bus']}/driver")
        if driver:
            record["driver"] = driver
        out.append(record)
    return out


def _pci_to_gpu(d: dict) -> dict:
    """Build a schema ``gpu`` record from a profiled PCI display device."""

    vendor, model = _pci_vendor_model(d["vendor"], d["device"])
    return {
        "vendor": vendor,
        "model": model,
        "bus": d["bus"],
    }


# ---------------------------------------------------------------------------
# Network (sysfs interfaces).
# ---------------------------------------------------------------------------


def collect_network(source: Source) -> list:
    """Collect network interfaces from ``class/net/<iface>``.

    ``lo`` (loopback) is skipped — it is not a real network device. We read the
    MAC address (required), and the optional speed, driver, and bus (the PCI
    address the interface is bound to, from its ``device`` symlink).
    """

    interfaces = []
    for iface in _list_dir(source, "class/net"):
        if iface == "lo":
            continue
        mac = _safe_read(source, f"class/net/{iface}/address")
        record = {"interface": iface, "mac": mac or ""}
        speed = _safe_read(source, f"class/net/{iface}/speed")
        if speed:
            try:
                record["speed_mbps"] = int(speed)
            except ValueError:
                pass
        driver = _read_link(source, f"class/net/{iface}/driver")
        if driver:
            record["driver"] = driver
        bus = _pci_bus_for_net(source, iface)
        if bus:
            record["bus"] = bus
        interfaces.append(record)
    return interfaces


def _pci_bus_for_net(source: Source, iface: str) -> str | None:
    """Return the PCI bus address an interface is bound to, if any.

    ``class/net/<iface>/device`` is a symlink to the PCI device; we return its
    final path component (the ``0000:..:..`` address).
    """

    try:
        path = (source.root / f"class/net/{iface}/device")
        if not path.is_symlink():
            return None
        return Path(os.readlink(path)).name
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Storage (NVMe / block).
# ---------------------------------------------------------------------------


def collect_storage(source: Source) -> list:
    """Collect storage devices from ``class/nvme`` and ``class/block``.

    NVMe devices (``class/nvme/nvme<n>/device/{model,serial,vendor}`` and the
    sector count in ``class/nvme/nvme<n>/size``) are the primary case. Loop /
    RAM / CD devices in ``class/block`` are skipped (they are not physical
    storage).
    """

    devices = []
    seen = set()

    for nvme in _list_dir(source, "class/nvme"):
        model = _safe_read(source, f"class/nvme/{nvme}/device/model")
        size_sectors = _safe_read(source, f"class/nvme/{nvme}/size")
        if not model:
            continue
        serial = _safe_read(source, f"class/nvme/{nvme}/device/serial")
        size_bytes = _parse_sectors(size_sectors) if size_sectors else 0
        key = (model, serial)
        if key in seen:
            continue
        seen.add(key)
        record = {
            "bus": "nvme",
            "model": model,
            "size_bytes": size_bytes,
            "transport": "NVMe",
        }
        if serial:
            record["serial"] = serial
        devices.append(record)

    for disk in _list_dir(source, "class/block"):
        if disk.startswith(("loop", "ram", "sr")):
            continue
        model = _safe_read(source, f"class/block/{disk}/model")
        size_blocks = _safe_read(source, f"class/block/{disk}/size")
        serial = _safe_read(source, f"class/block/{disk}/serial")
        if not model:
            continue
        key = (model, serial)
        if key in seen:
            continue
        seen.add(key)
        size_bytes = _parse_sectors(size_blocks) if size_blocks else 0
        transport = _safe_read(source, f"class/block/{disk}/transport")
        record = {
            "bus": _sata_bus(source, disk),
            "model": model,
            "size_bytes": size_bytes,
        }
        if serial:
            record["serial"] = serial
        if transport:
            record["transport"] = transport
        devices.append(record)

    return devices


def _parse_sectors(text: str) -> int:
    """Convert a sector count (``class/.../size``) to bytes (512-byte sectors)."""

    try:
        return parse_int(text) * 512
    except ValueError as exc:
        raise ProfileError(f"storage: unparseable sector count {text!r}") from exc


def _sata_bus(source: Source, disk: str) -> str:
    """Best-effort bus string for a block device (``sata`` when a device link)."""

    try:
        path = (source.root / f"class/block/{disk}/device")
        if path.is_symlink():
            return "sata"
    except OSError:
        pass
    return "sata"


# ---------------------------------------------------------------------------
# USB peripherals.
# ---------------------------------------------------------------------------


def collect_peripherals(source: Source) -> list:
    """Collect USB peripherals from ``bus/usb/devices/<dev>``.

    Host controllers and hubs (vendor ``1d6b`` = Linux Foundation) are skipped;
    only real end devices are profiled. Each contributes a bus token (derived
    from the device name), vendor/product IDs, and a product name.
    """

    peripherals = []
    for dev in _list_dir(source, "bus/usb/devices"):
        vendor_id = _safe_read(source, f"bus/usb/devices/{dev}/idVendor")
        product_id = _safe_read(source, f"bus/usb/devices/{dev}/idProduct")
        name = _safe_read(source, f"bus/usb/devices/{dev}/product")
        if _is_hub(vendor_id):
            continue
        if not vendor_id or not product_id:
            continue
        record = {
            "bus": _usb_bus_for_dev(dev),
            "vendor_id": vendor_id,
            "product_id": product_id,
            "name": name or f"{vendor_id}:{product_id}",
        }
        peripherals.append(record)
    return peripherals


_HUB_VENDOR = "1d6b"  # Linux Foundation root hubs / host controllers


def _is_hub(vendor_id: str | None) -> bool:
    return (vendor_id or "").lower() == _HUB_VENDOR


def _usb_bus_for_dev(dev: str) -> str:
    """Convert a USB device name (``5-2``) to a stable ``bus:port`` token.

    sysfs names devices as ``<hub>-<port>`` (``5-2``); the classic ``lsusb``
    bus:device style is ``001:NNN``. We synthesise a deterministic token from
    the name so fixture-driven output is stable.
    """

    parts = dev.split("-")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}:{int(parts[1]):03d}"
    return dev


# ---------------------------------------------------------------------------
# PCI vendor/model decoding (best-effort, no database, no network).
# ---------------------------------------------------------------------------

# A tiny embedded subset of the PCI ID database for the common vendors we expect
# in fixtures. Anything not listed falls back to the raw hex id, which is still
# valid (and deterministic). Keeping it embedded means no network fetch and no
# runtime dependency.
_VENDOR_NAMES: dict[str, str] = {
    "0x1022": "Advanced Micro Devices, Inc. [AMD]",
    "0x1002": "Advanced Micro Devices, Inc. [AMD/ATI]",
    "0x8086": "Intel Corporation",
    "0x10de": "NVIDIA Corp",
    "0x1234": "Qemu virtual machine",
}
_DEVICE_NAMES: dict[tuple[str, str], str] = {
    ("0x1002", "0x1632"): "Vega [Radeon RX 480]",
    ("0x1022", "0x1632"): "Vega [Radeon RX Vega]",
    ("0x8086", "0x3eaf"): "Iris Xe Graphics",
    ("0x8086", "0x9a49"): "Alder Lake-S IMC",
}


def _pci_vendor_model(vendor: str | None, device: str | None) -> tuple[str, str]:
    """Return ``(vendor_name, model_name)`` for a PCI vendor/device pair.

    Best-effort: uses the embedded vendor table and a small device table, and
    falls back to the raw hex ids when unknown. Never raises.
    """

    vendor_id = (vendor or "").strip().lower()
    device_id = (device or "").strip().lower()

    vendor_name = _VENDOR_NAMES.get(vendor_id, vendor_id or "unknown")
    device_name = _DEVICE_NAMES.get((vendor_id, device_id), device_id or "unknown")
    return vendor_name, device_name


# ---------------------------------------------------------------------------
# Assembly.
# ---------------------------------------------------------------------------


def collect_all(source: Source) -> dict:
    """Collect every section and return the full profile document.

    The returned document is shaped like a parsed ``profile.toml`` (plain
    ``dict`` / ``list``). It is validated by :func:`collect` /
    :func:`collect_profile` — this function only *assembles*.
    """

    return {
        "metadata": collect_metadata(source),
        "cpu": collect_cpu(source),
        "memory": collect_memory(source),
        "gpu": collect_gpu(source),
        "storage": collect_storage(source),
        "network": collect_network(source),
        "peripherals": collect_peripherals(source),
    }


def collect(source: Source) -> dict:
    """Collect and validate the profile document, raising ``ProfileError``.

    This is the entry point the ``detect`` subcommand calls before serialising
    to TOML: it guarantees the document conforms to the schema.
    """

    document = collect_all(source)
    load_profile(document)  # raises ProfileError on any violation
    return document


def collect_profile(source: Source) -> Profile:
    """Collect, validate, and build the frozen :class:`~pengyos_hwprofile.schema.Profile`."""

    document = collect(source)
    return load_profile(document)
