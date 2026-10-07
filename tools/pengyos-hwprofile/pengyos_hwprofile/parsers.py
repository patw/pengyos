"""Low-level parsing primitives for the collectors.

These helpers turn raw sysfs text into the small structured shapes the
collectors in :mod:`pengyos_hwprofile.collectors` consume. They are pure
functions over strings (or a :class:`~pengyos_hwprofile.source.Source`), so they
are trivial to unit-test in isolation and carry no filesystem logic of their own.

Everything here is standard-library only.
"""

from __future__ import annotations

from .source import Source

# ---------------------------------------------------------------------------
# PCI class codes.
#
# A sysfs PCI device exposes its class as a 6-hex-digit code, e.g.
# ``0x030000`` (display controller), ``0x010802`` (NVMe storage),
# ``0x020000`` (Ethernet), ``0x060000`` (host bridge). The low two octets are
# the *base* class; the top octet is a *sub* class. We care about the base
# class, which tells us what kind of device a device is.
# ---------------------------------------------------------------------------

# Base class codes (the low two hex octets) we understand. Anything else —
# bridges (0x06), serial (0x0c), communication (0x07), cryptography (0x10),
# data-squeeze (0x0f), signal processing (0x11), etc. — is filtered out of the
# profile: it is platform plumbing, not a user-facing device.
_PCI_BASE_CLASSES: dict[str, str] = {
    "0x03": "display",   # GPU / display controller
    "0x01": "storage",   # mass storage (SATA/NVMe/IDE controllers)
    "0x02": "network",   # network interface (Ethernet/Wi-Fi/...)
}


def parse_int(text: str, *, base: int = 0) -> int:
    """Parse ``text`` as an integer, tolerating a ``0x`` prefix and whitespace.

    sysfs files such as ``vendor``/``device``/``class`` are hexadecimal
    (``"0x1022"``); sizes in ``/proc``/``/sys`` are decimal. ``base=0`` lets
    :func:`int` infer the radix from the ``0x`` prefix, matching Python's own
    literal rules.
    """

    try:
        return int(text.strip(), base)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"cannot parse {text!r} as an integer") from exc


def parse_pci_class(code: str) -> str | None:
    """Map a PCI class code (e.g. ``"0x030000"``) to a device category.

    Returns the category string (``"display"``, ``"storage"``, ``"network"``)
    or ``None`` if the device is not one of the user-facing classes we profile.

    The code is ``0x`` followed by three hex octets; the *base* class is the
    first octet (e.g. ``0x030000`` -> ``0x03`` = display controller).
    """

    code = code.strip().lower()
    digits = code[2:] if code.startswith("0x") else code
    base = f"0x{digits[:2]}"
    return _PCI_BASE_CLASSES.get(base)


def parse_cpuinfo(text: str) -> list[dict[str, str]]:
    """Parse ``/proc/cpuinfo`` text into a list of processor records.

    ``/proc/cpuinfo`` is a sequence of blocks separated by blank lines, each
    describing one logical processor as ``key\t: value`` lines. This returns one
    dict per block with the keys lower-cased and values stripped. Blocks with no
    recognisable ``vendor_id``/``model name`` pair are still returned (the CPU
    collector decides what to do); this parser only *splits* the document.
    """

    processors: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in text.splitlines():
        if not line.strip():
            if current:
                processors.append(current)
                current = {}
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        current[key.strip().lower()] = value.strip()

    if current:
        processors.append(current)

    return processors
