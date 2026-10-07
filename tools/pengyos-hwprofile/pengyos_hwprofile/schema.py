"""Schema for the PengyOS hwprofile document.

This module *defines* the profile schema: the concrete data model (a set of
frozen ``dataclass``es) and a strict validator that enforces it. It is the
shared contract that the later subtasks build on:

* collectors emit plain ``dict`` / ``list`` structures shaped like this schema;
* the TOML emitter serialises a validated :class:`Profile`;
* ``diff`` loads and validates two profiles before comparing them.

The schema is intentionally declarative so it stays self-documenting and easy
to extend. It is standard-library only.

The document is a TOML table whose top level holds a ``metadata`` table, a
``cpu`` table, and zero or more list-valued hardware sections
(``memory``, ``gpu``, ``storage``, ``network``, ``peripherals``). See
``spec.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Tuple


class ProfileError(Exception):
    """Raised when a parsed profile violates the hwprofile schema.

    The message always names the offending path (e.g. ``cpu.cores`` or
    ``gpu[2].driver``) so drift is easy to locate in a ``profile.toml``.
    """


# ---------------------------------------------------------------------------
# Section name constants — these are the exact top-level keys of a profile.
# ---------------------------------------------------------------------------
METADATA = "metadata"
CPU = "cpu"
MEMORY = "memory"
GPU = "gpu"
STORAGE = "storage"
NETWORK = "network"
PERIPHERALS = "peripherals"

# Every key a profile may legally contain at the top level.
TOP_LEVEL_SECTIONS: frozenset[str] = frozenset(
    {METADATA, CPU, MEMORY, GPU, STORAGE, NETWORK, PERIPHERALS}
)

# Sections that must be present in a valid profile. Every machine has a CPU and
# a metadata block (hostname/kernel); the hardware sections are optional because
# a device may legitimately have, say, no discrete GPU.
REQUIRED_SECTIONS: frozenset[str] = frozenset({METADATA, CPU})


# ---------------------------------------------------------------------------
# Declarative field descriptions.
#
# A ``_Field`` describes one scalar member of a table. ``kind`` is one of
# ``"str"``, ``"int"`` or ``"number"``. ``required`` controls whether the field
# must be present. Anything not listed here is an *unknown field* and is
# rejected — this catches typos and keeps profiles diff-clean.
# ---------------------------------------------------------------------------
_KINDS = ("str", "int", "number")


@dataclass(frozen=True)
class _Field:
    """A single named scalar field of a table."""

    name: str
    kind: str
    required: bool = True


_METADATA_FIELDS: Tuple[_Field, ...] = (
    _Field("hostname", "str"),
    _Field("kernel", "str"),
    _Field("tool_version", "str"),
)

_CPU_FIELDS: Tuple[_Field, ...] = (
    _Field("vendor", "str"),
    _Field("model", "str"),
    _Field("cores", "int"),
    _Field("threads", "int"),
    _Field("mhz", "number"),
)

# ``memory`` is a list of modules; the optional members are best-effort and may
# be unavailable from a fixture.
_MEMORY_FIELDS: Tuple[_Field, ...] = (
    _Field("locator", "str"),
    _Field("size_bytes", "int"),
    _Field("speed_mhz", "int", required=False),
    _Field("manufacturer", "str", required=False),
    _Field("memory_type", "str", required=False),
)

# ``gpu`` is a list (a machine may have one or more render devices).
_GPU_FIELDS: Tuple[_Field, ...] = (
    _Field("vendor", "str"),
    _Field("model", "str"),
    _Field("bus", "str"),
    _Field("driver", "str", required=False),
    _Field("vram_bytes", "int", required=False),
)

_STORAGE_FIELDS: Tuple[_Field, ...] = (
    _Field("bus", "str"),
    _Field("model", "str"),
    _Field("size_bytes", "int"),
    _Field("serial", "str", required=False),
    _Field("speed", "str", required=False),
    _Field("transport", "str", required=False),
)

_NETWORK_FIELDS: Tuple[_Field, ...] = (
    _Field("interface", "str"),
    _Field("mac", "str"),
    _Field("driver", "str", required=False),
    _Field("speed_mbps", "int", required=False),
    _Field("bus", "str", required=False),
)

_PERIPHERALS_FIELDS: Tuple[_Field, ...] = (
    _Field("bus", "str"),
    _Field("vendor_id", "str"),
    _Field("product_id", "str"),
    _Field("name", "str"),
)

# The declarative schema, keyed by section name. This is the single source of
# truth the validator walks and a natural place to document the shape.
SECTIONS: dict[str, Tuple[_Field, ...]] = {
    METADATA: _METADATA_FIELDS,
    CPU: _CPU_FIELDS,
    MEMORY: _MEMORY_FIELDS,
    GPU: _GPU_FIELDS,
    STORAGE: _STORAGE_FIELDS,
    NETWORK: _NETWORK_FIELDS,
    PERIPHERALS: _PERIPHERALS_FIELDS,
}


# ---------------------------------------------------------------------------
# The concrete data model — this *is* the schema, materialised.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Metadata:
    """Machine identity, recorded so two profiles can be told apart."""

    hostname: str
    kernel: str
    tool_version: str


@dataclass(frozen=True)
class Cpu:
    """A single logical processor aggregate."""

    vendor: str
    model: str
    cores: int
    threads: int
    mhz: float


@dataclass(frozen=True)
class MemoryModule:
    """One physical memory module (DIMM / slot)."""

    locator: str
    size_bytes: int
    speed_mhz: int | None = None
    manufacturer: str | None = None
    memory_type: str | None = None


@dataclass(frozen=True)
class Gpu:
    """A single render / GPU device."""

    vendor: str
    model: str
    bus: str
    driver: str | None = None
    vram_bytes: int | None = None


@dataclass(frozen=True)
class StorageDevice:
    """A single storage device (disk / NVMe / partition)."""

    bus: str
    model: str
    size_bytes: int
    serial: str | None = None
    speed: str | None = None
    transport: str | None = None


@dataclass(frozen=True)
class NetworkDevice:
    """A single network interface."""

    interface: str
    mac: str
    driver: str | None = None
    speed_mbps: int | None = None
    bus: str | None = None


@dataclass(frozen=True)
class Peripheral:
    """A single bus peripheral (USB or PCI vendor/product pair)."""

    bus: str
    vendor_id: str
    product_id: str
    name: str


@dataclass(frozen=True)
class Profile:
    """A complete, validated hardware profile."""

    metadata: Metadata
    cpu: Cpu
    memory: tuple[MemoryModule, ...] = ()
    gpu: tuple[Gpu, ...] = ()
    storage: tuple[StorageDevice, ...] = ()
    network: tuple[NetworkDevice, ...] = ()
    peripherals: tuple[Peripheral, ...] = ()


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------
def _typename(value: Any) -> str:
    """Return the ``repr``-friendly type name of ``value`` for error messages."""

    return type(value).__name__


def _check_scalar(value: Any, kind: str, path: str) -> None:
    """Validate a scalar against ``kind``, raising :class:`ProfileError`."""

    if kind == "str":
        if not isinstance(value, str):
            raise ProfileError(f"{path}: expected str, got {_typename(value)}")
    elif kind == "int":
        # ``bool`` is a subclass of ``int``; reject it explicitly so a stray
        # ``true`` is never mistaken for ``1``.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProfileError(f"{path}: expected int, got {_typename(value)}")
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProfileError(f"{path}: expected number, got {_typename(value)}")
    else:  # pragma: no cover - guarded by _KINDS
        raise ProfileError(f"{path}: unknown field kind {kind!r}")


def _validate_table(data: Any, spec: Tuple[_Field, ...], path: str) -> dict[str, Any]:
    """Validate a table against ``spec`` and return a clean copy of its fields."""

    if not isinstance(data, Mapping):
        raise ProfileError(f"{path}: expected a table, got {_typename(data)}")

    known = {f.name for f in spec}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ProfileError(f"{path}: unknown field(s): {', '.join(unknown)}")

    result: dict[str, Any] = {}
    for f in spec:
        if f.name not in data:
            if f.required:
                raise ProfileError(f"{path}: missing required field '{f.name}'")
            continue
        _check_scalar(data[f.name], f.kind, f"{path}.{f.name}")
        result[f.name] = data[f.name]
    return result


def _validate_list_section(data: Any, spec: Tuple[_Field, ...], name: str) -> list[dict[str, Any]]:
    """Validate a list-valued section (e.g. ``gpu``) as a list of tables."""

    if not isinstance(data, list):
        raise ProfileError(f"{name}: expected a list of tables, got {_typename(data)}")
    return [
        _validate_table(item, spec, f"{name}[{index}]")
        for index, item in enumerate(data)
    ]


def validate(data: Any) -> None:
    """Validate a parsed profile, raising :class:`ProfileError` on any violation.

    ``data`` is typically the object returned by ``tomllib.load`` (a plain
    ``dict``). Passing this here before serialising or diffing guarantees the
    document conforms to the schema.
    """

    if not isinstance(data, Mapping):
        raise ProfileError(f"expected a table at the top level, got {_typename(data)}")

    unknown = sorted(set(data) - TOP_LEVEL_SECTIONS)
    if unknown:
        raise ProfileError(f"unknown top-level section(s): {', '.join(unknown)}")

    missing = sorted(REQUIRED_SECTIONS - set(data))
    if missing:
        raise ProfileError(f"missing required section(s): {', '.join(missing)}")

    # ``metadata`` and ``cpu`` are single tables.
    _validate_table(data[METADATA], _METADATA_FIELDS, METADATA)
    _validate_table(data[CPU], _CPU_FIELDS, CPU)

    # The hardware sections are lists and are optional.
    if MEMORY in data:
        _validate_list_section(data[MEMORY], _MEMORY_FIELDS, MEMORY)
    if GPU in data:
        _validate_list_section(data[GPU], _GPU_FIELDS, GPU)
    if STORAGE in data:
        _validate_list_section(data[STORAGE], _STORAGE_FIELDS, STORAGE)
    if NETWORK in data:
        _validate_list_section(data[NETWORK], _NETWORK_FIELDS, NETWORK)
    if PERIPHERALS in data:
        _validate_list_section(data[PERIPHERALS], _PERIPHERALS_FIELDS, PERIPHERALS)


# ---------------------------------------------------------------------------
# Construction: turn a validated mapping into the frozen data model.
# ---------------------------------------------------------------------------
def _as_memory(d: Mapping[str, Any]) -> MemoryModule:
    return MemoryModule(
        locator=d["locator"],
        size_bytes=int(d["size_bytes"]),
        speed_mhz=int(d["speed_mhz"]) if "speed_mhz" in d else None,
        manufacturer=d.get("manufacturer"),
        memory_type=d.get("memory_type"),
    )


def _as_gpu(d: Mapping[str, Any]) -> Gpu:
    return Gpu(
        vendor=d["vendor"],
        model=d["model"],
        bus=d["bus"],
        driver=d.get("driver"),
        vram_bytes=int(d["vram_bytes"]) if "vram_bytes" in d else None,
    )


def _as_storage(d: Mapping[str, Any]) -> StorageDevice:
    return StorageDevice(
        bus=d["bus"],
        model=d["model"],
        size_bytes=int(d["size_bytes"]),
        serial=d.get("serial"),
        speed=d.get("speed"),
        transport=d.get("transport"),
    )


def _as_network(d: Mapping[str, Any]) -> NetworkDevice:
    return NetworkDevice(
        interface=d["interface"],
        mac=d["mac"],
        driver=d.get("driver"),
        speed_mbps=int(d["speed_mbps"]) if "speed_mbps" in d else None,
        bus=d.get("bus"),
    )


def _as_peripheral(d: Mapping[str, Any]) -> Peripheral:
    return Peripheral(
        bus=d["bus"],
        vendor_id=d["vendor_id"],
        product_id=d["product_id"],
        name=d["name"],
    )


def load_profile(data: Mapping[str, Any]) -> Profile:
    """Validate ``data`` and build a frozen :class:`Profile` from it.

    Raises :class:`ProfileError` if ``data`` does not conform to the schema.
    """

    validate(data)

    md = data[METADATA]
    cpu = data[CPU]
    return Profile(
        metadata=Metadata(
            hostname=md["hostname"],
            kernel=md["kernel"],
            tool_version=md["tool_version"],
        ),
        cpu=Cpu(
            vendor=cpu["vendor"],
            model=cpu["model"],
            cores=int(cpu["cores"]),
            threads=int(cpu["threads"]),
            mhz=float(cpu["mhz"]),
        ),
        memory=tuple(_as_memory(m) for m in data.get(MEMORY, [])),
        gpu=tuple(_as_gpu(g) for g in data.get(GPU, [])),
        storage=tuple(_as_storage(s) for s in data.get(STORAGE, [])),
        network=tuple(_as_network(n) for n in data.get(NETWORK, [])),
        peripherals=tuple(_as_peripheral(p) for p in data.get(PERIPHERALS, [])),
    )
