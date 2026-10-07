"""Fact collection: run hardware commands and parse them into hwprofile data.

``pengyos-fleet collect`` reaches each host through a
:class:`~pengyos_fleet.runner.Runner` and runs a small, fixed set of hardware
commands. This module turns those commands into the plain ``dict`` / ``list``
structures shaped exactly like the corresponding sections of the PengyOS
hwprofile schema (see the sibling ``pengyos_hwprofile.schema`` package):
``metadata``, ``cpu``, ``memory``, ``gpu``, ``storage`` and ``network``.

Each command maps to one parser, a pure function over the command's stdout:

========================  ===========  ==========================================
Section                   Command      Output shape
========================  ===========  ==========================================
``metadata``              ``uname -a``  ``{"hostname", "kernel"}``
``cpu``                   ``lscpu``     ``{"vendor", "model", "cores", "threads", "mhz"}``
``memory``                ``free -b``   ``[{"locator", "size_bytes", ...}]``
``gpu``                   ``lspci``     ``[{"vendor", "model", "bus", ...}]``
``storage``               ``lsblk -P``  ``[{"bus", "model", "size_bytes", ...}]``
``network``               ``ip -o link`` ``[{"interface", "mac", ...}]``
========================  ===========  ==========================================

The parsers carry no filesystem, subprocess or network logic — they only turn a
string into structured data — so they are unit-tested directly against the
committed fixtures. :func:`collect_facts` is the single place that talks to a
runner. Standard library only.
"""

from __future__ import annotations

import re
from typing import Callable

from . import __version__
from .hosts import Host
from .runner import RunResult, Runner

# ---------------------------------------------------------------------------
# The hardware commands, in collection order.
#
# Each entry is ``(section, argv)``. ``argv[0]`` is the command; the mock runner
# keys fixtures off its basename, so ``free -b`` reads ``<fixtures>/free`` and
# ``lsblk -P`` reads ``<fixtures>/lsblk``.
# ---------------------------------------------------------------------------

_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("metadata", ("uname", "-a")),
    ("cpu", ("lscpu",)),
    ("memory", ("free", "-b")),
    ("gpu", ("lspci",)),
    ("storage", ("lsblk", "-P")),
    ("network", ("ip", "-o", "link", "show")),
)


class FactsError(RuntimeError):
    """Raised when fact collection cannot produce a complete fact set.

    Covers a command that exits non-zero (so a missing hardware tool fails
    loudly rather than emitting a half-built profile) and a command whose output
    no parser recognises.
    """


# ---------------------------------------------------------------------------
# Command registry — the single source of truth the CLI and the tests agree on.
# ---------------------------------------------------------------------------


def commands() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return the ``(section, argv)`` commands collected for every host."""

    return _COMMANDS


# ---------------------------------------------------------------------------
# Parsers.
#
# Each parser is a pure function over a command's stdout and returns data shaped
# like the matching hwprofile section. They are defined before ``_PARSERS`` so
# the mapping can reference them.
# ---------------------------------------------------------------------------


def _key_value_lines(text: str) -> dict[str, str]:
    """Split ``"Key: value"`` lines into a dict with lower-cased, stripped keys.

    Lines without a colon are ignored. Later duplicates win, matching how a
    reader would naturally treat the last value for a repeated key.
    """

    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()
    return fields


def _parse_metadata(text: str) -> dict:
    """Parse ``uname -a`` into ``{"hostname", "kernel"}``.

    ``uname -a`` layout is ``<kernel> <hostname> <kernel-release> ...``; the
    second field is the hostname and the third is the kernel release. A bare
    ``uname -s`` (just ``"Linux"``) still yields a hostname of ``"linux"`` and
    no kernel, so a mis-sized fixture never raises — it just produces a sparse
    metadata block.
    """

    parts = text.split()
    if len(parts) >= 3:
        return {"hostname": parts[1], "kernel": parts[2]}
    if len(parts) == 1:
        return {"hostname": parts[0].lower()}
    return {}


def parse_cpu(text: str) -> dict:
    """Parse ``lscpu`` output into a hwprofile ``cpu`` table.

    Reads the ``Key: value`` lines: ``vendor`` and ``model`` from ``Vendor ID``
    / ``Model name``, ``threads`` from ``CPU(s)``, and ``cores`` as
    ``(Core(s) per socket) x Socket(s)`` (falling back to ``CPU(s)`` when the
    per-socket breakdown is absent). ``mhz`` is ``CPU MHz`` or ``0.0``.
    """

    fields = _key_value_lines(text)

    threads_raw = fields.get("cpu(s)")
    if not threads_raw:
        raise FactsError("cpu: lscpu output described no CPU(s)")

    cores_raw = fields.get("core(s) per socket")
    sockets_raw = fields.get("socket(s)")
    if cores_raw and sockets_raw:
        cores = int(cores_raw) * int(sockets_raw)
    else:
        cores = int(threads_raw)

    threads = int(threads_raw)

    mhz_raw = fields.get("cpu mhz")
    mhz = float(mhz_raw) if mhz_raw else 0.0

    return {
        "vendor": fields.get("vendor id", "unknown"),
        "model": fields.get("model name", "unknown"),
        "cores": cores,
        "threads": threads,
        "mhz": mhz,
    }


def parse_memory(text: str) -> list:
    """Parse ``free -b`` into a one-element memory list reporting total RAM.

    ``free`` reports per-DIMM detail only under root (``dmidecode``); as a
    non-root user the honest fact is *total* memory. It is recorded as a single
    synthetic module so the result still fits the hwprofile ``memory`` schema
    (``locator`` + ``size_bytes``). Returns ``[]`` when no ``Mem:`` row is found.
    """

    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "Mem:":
            try:
                size_bytes = int(parts[1])
            except (IndexError, ValueError):
                raise FactsError("memory: cannot parse total from free output")
            return [{"locator": "System", "size_bytes": size_bytes}]
    return []


def _lspci_line(line: str) -> tuple[str, str, str] | None:
    """Split one ``lspci`` line into ``(address, class, description)``.

    ``lspci`` layout is ``<address> <class>: <description>`` where the address is
    ``bus:slot.func`` (e.g. ``00:02.0``) and the *class* may be several words
    (``Display controller``). Returns ``None`` for lines that do not match (e.g.
    the ``/proc/bus/pci`` warning some kernels print).
    """

    match = re.match(r"^\s*(\S+)\s+(.*)$", line)
    if not match:
        return None
    address, rest = match.group(1), match.group(2)
    if ":" not in rest:
        return None
    cls, _, desc = rest.partition(":")
    return address, cls.strip(), desc.strip()


# Known PCI vendors, so a human-readable name is recorded instead of a raw hex
# id. Anything not listed falls back to the raw vendor string from ``lspci``.
_VENDOR_NAMES: dict[str, str] = {
    "0x8086": "Intel Corporation",
    "0x1022": "Advanced Micro Devices, Inc. [AMD]",
    "0x10de": "NVIDIA Corp",
}

# Display-class PCI devices we can name from a vendor/device pair. Unknown
# pairs fall back to the raw ``lspci`` description, which is still valid.
_DISPLAY_MODELS: dict[tuple[str, str], str] = {
    ("0x8086", "0x9a49"): "Iris Xe Graphics",
    ("0x1022", "0x1632"): "Radeon RX Vega",
    ("0x10de", "0x1f92"): "TU117 [GeForce GTX 1650]",
}


def _pci_ids(desc: str) -> tuple[str | None, str | None]:
    """Extract ``(vendor_id, device_id)`` from an ``lspci -nn`` description.

    ``-nn`` appends ``[XXXX:YYYY]`` (vendor:device) to each line; we pull those
    hex ids out so the device can be named. Both with and without a leading
    ``0x`` are accepted, and the result is normalised to carry the ``0x`` prefix
    so it matches the lookup tables. Returns ``(None, None)`` when the line lacks
    a ``[..:..]`` token.
    """

    match = re.search(
        r"\[((?:0[xX])?[0-9a-fA-F]{4}:(?:0[xX])?[0-9a-fA-F]{4})\]", desc
    )
    if not match:
        return None, None
    vendor, device = match.group(1).split(":")
    return _with_0x(vendor.lower()), _with_0x(device.lower())


def _with_0x(hex_id: str) -> str:
    """Ensure a hex id carries the ``0x`` prefix (``8086`` -> ``0x8086``)."""

    return hex_id if hex_id.startswith("0x") else f"0x{hex_id}"


def _gpu_vendor_model(desc: str) -> tuple[str, str]:
    """Return ``(vendor, model)`` for a display device described by ``desc``.

    Best-effort and never raises: uses the embedded vendor table plus a small
    display-device table, and falls back to the raw ``lspci`` description when
    the pair is unknown.
    """

    vendor_id, device_id = _pci_ids(desc)
    if vendor_id is not None:
        model = _DISPLAY_MODELS.get((vendor_id, device_id))
        if model:
            return (_VENDOR_NAMES.get(vendor_id, vendor_id), model)
        # Known vendor, unknown model: keep the vendor name and use the
        # human-readable description as the model.
        return (_VENDOR_NAMES.get(vendor_id, vendor_id), _gpu_text(desc))

    # No -nn ids: fall back to the human-readable description.
    return ("unknown", _gpu_text(desc))


def _gpu_text(desc: str) -> str:
    """Return a display description with a trailing ``(rev XX)`` stripped."""

    return re.sub(r"\s*\(rev\s+[^)]*\)\s*$", "", desc).strip()


def parse_gpu(text: str) -> list:
    """Parse ``lspci`` into hwprofile ``gpu`` records (display controllers).

    Only display / VGA / 3D controllers are profiled; storage, network and
    bridge classes are ignored here (they are collected from ``lsblk`` and
    ``ip``). Each record carries ``vendor``, ``model`` and the PCI ``bus``
    address.
    """

    devices: list[dict] = []
    for line in text.splitlines():
        parsed = _lspci_line(line)
        if parsed is None:
            continue
        _bus, cls, desc = parsed
        if "display" not in cls.lower() and "vga" not in cls.lower():
            continue
        vendor, model = _gpu_vendor_model(desc)
        devices.append({"vendor": vendor, "model": model, "bus": _bus})
    return devices


def parse_storage(text: str) -> list:
    """Parse ``lsblk -P`` into hwprofile ``storage`` records.

    ``lsblk -P`` emits ``KEY="value"`` fields per device (NAME, MODEL, SIZE,
    TRAN, SERIAL, ...); each field becomes a schema key. ``size_bytes`` is the
    ``SIZE`` field, ``transport`` the ``TRAN`` field, and ``bus`` defaults to
    ``transport`` (``nvme`` / ``sata`` / ...). Devices with no size are skipped.
    """

    devices: list[dict] = []
    for line in text.splitlines():
        fields = _key_value_pairs(line)
        try:
            size_bytes = int(fields["SIZE"])
        except (TypeError, ValueError):
            continue  # no usable size — not a real device.
        transport = fields.get("TRAN")
        record: dict = {
            "bus": transport or "unknown",
            "model": fields.get("MODEL") or "unknown",
            "size_bytes": size_bytes,
        }
        serial = fields.get("SERIAL")
        if serial:
            record["serial"] = serial
        if transport:
            record["transport"] = transport
        devices.append(record)
    return devices


def _key_value_pairs(text: str) -> dict[str, str]:
    """Parse ``KEY="value"`` pairs (as ``lsblk -P`` emits) into a dict."""

    return {m[0]: m[1] for m in re.findall(r'(\w+)="([^"]*)"', text)}


def parse_network(text: str) -> list:
    """Parse ``ip -o link show`` into hwprofile ``network`` records.

    Each line is one interface; the name (before the ``:``) and the ``link/ether
    <mac>`` address are required — both map straight onto the hwprofile
    ``network`` schema (``interface`` + ``mac``). ``lo`` (loopback) is skipped —
    it is not a real network device. ``driver`` / ``speed_mbps`` / ``bus`` stay
    absent unless a richer ``ip`` verb is used (a follow-up).
    """

    interfaces: list[dict] = []
    for line in text.splitlines():
        match = re.match(r"^\s*\d+:\s+(\S+?):", line)
        if not match:
            continue
        iface = match.group(1)
        if iface == "lo":
            continue
        mac_match = re.search(r"link/ether (\S+)", line)
        interfaces.append(
            {
                "interface": iface,
                "mac": mac_match.group(1) if mac_match else "",
            }
        )
    return interfaces


# A command's section name -> the parser that turns its stdout into hwprofile
# data. Kept in lock-step with ``_COMMANDS``. Defined after the parsers so the
# names resolve.
_PARSERS: dict[str, Callable[[str], object]] = {
    "metadata": _parse_metadata,
    "cpu": parse_cpu,
    "memory": parse_memory,
    "gpu": parse_gpu,
    "storage": parse_storage,
    "network": parse_network,
}


# ---------------------------------------------------------------------------
# Orchestration — the only place that talks to a runner.
# ---------------------------------------------------------------------------


def _run_all(runner: Runner, host: Host) -> dict[str, str]:
    """Run every hardware command and return ``{section: stdout}``.

    Raises :class:`FactsError` if any command exits non-zero, so a missing
    hardware tool (say ``lsblk`` is not installed) surfaces immediately instead
    of silently producing an incomplete profile.
    """

    outputs: dict[str, str] = {}
    for section, argv in _COMMANDS:
        result: RunResult = runner.run(host, list(argv))
        if result.returncode != 0:
            message = (
                f"{host.name!r}: {section} command {' '.join(argv)} "
                f"exited {result.returncode}"
            )
            if result.stderr.strip():
                message += f": {result.stderr.strip()}"
            raise FactsError(message)
        outputs[section] = result.stdout
    return outputs


def collect_facts(runner: Runner, host: Host) -> dict:
    """Run the hardware commands for ``host`` and parse them into a document.

    The returned mapping is shaped like a parsed ``profile.toml`` — the
    ``metadata`` and ``cpu`` tables plus the ``memory`` / ``gpu`` / ``storage`` /
    ``network`` list sections. ``tool_version`` is stamped from this package's
    version. The document is *not* validated here (that is the emitter's job);
    a validating wrapper is added when the emit subtask lands.
    """

    outputs = _run_all(runner, host)
    document = {
        "metadata": {
            **_parse_metadata(outputs["metadata"]),
            "tool_version": __version__,
        },
        "cpu": _PARSERS["cpu"](outputs["cpu"]),
        "memory": _PARSERS["memory"](outputs["memory"]),
        "gpu": _PARSERS["gpu"](outputs["gpu"]),
        "storage": _PARSERS["storage"](outputs["storage"]),
        "network": _PARSERS["network"](outputs["network"]),
    }
    return document
