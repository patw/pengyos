"""Load and validate the small YAML/JSON specs the CLI consumes.

Two spec shapes drive the two commands:

``ports.yaml`` (feeds ``pengyos-netctl rules``) — the declared-ports list
that becomes the nftables allow list::

    listen_port: 51820          # WireGuard, always allowed (UDP)
    ssh_port: 22                # SSH, always allowed (TCP)
    allowed_tcp: [443, 8080]    # extra declared TCP ports
    allowed_udp: [1194]         # extra declared UDP ports

``wg.yaml`` (feeds ``pengyos-netctl wg``) — the server + peers::

    key_dir: keys               # where key files live (relative to the spec)
    server:
      private_key: server.private
      address: "10.8.0.1/24"
      listen_port: 51820
      dns: "10.8.0.1"
    peers:
      - name: alice
        address: "10.8.0.2/24"
        private_key: alice.private
        allowed_ips: ["10.8.0.2/32"]

Everything here is standard-library only; YAML parsing is delegated to
``PyYAML`` (declared as a runtime dependency in ``pyproject.toml``).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from .errors import SpecError

__all__ = [
    "PeerSpec",
    "PortsConfig",
    "ServerSpec",
    "WgSpec",
    "load_config",
    "load_ports_config",
    "load_wg_spec",
]


def _require(mapping: Any, key: str, where: str):
    """Return ``mapping[key]`` or raise :class:`SpecError` if absent/blank."""
    if not isinstance(mapping, dict):
        raise SpecError(f"{where}: expected a mapping, got {type(mapping).__name__}")
    if key not in mapping or mapping[key] is None:
        raise SpecError(f"{where}: missing required key {key!r}")
    value = mapping[key]
    if isinstance(value, str) and value.strip() == "":
        raise SpecError(f"{where}: key {key!r} is empty")
    return value


def _as_int(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise SpecError(f"{name}: expected an integer, got {value!r}")


def _as_port(value: Any, name: str) -> int:
    port = _as_int(value, name)
    if not 1 <= port <= 65535:
        raise SpecError(f"{name}: port out of range (1-65535): {port}")
    return port


def _as_str_list(value: Any, name: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    # A bare scalar (e.g. `allowed_tcp: 8080`) is treated as a single item.
    return [str(value)]


def load_config(path: str) -> Dict[str, Any]:
    """Load a YAML or JSON spec into a plain dict.

    ``.json`` files are parsed as JSON; everything else as YAML. Raises
    :class:`SpecError` if the file is missing, unreadable, not a mapping,
    or unparsable.
    """
    if not os.path.isfile(path):
        raise SpecError(f"spec not found: {path}")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise SpecError(f"cannot read spec {path}: {exc}") from exc

    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".json":
            data = json.loads(raw)
        else:
            import yaml

            data = yaml.safe_load(raw)
    except SpecError:
        raise
    except Exception as exc:  # yaml.YAMLError, json.JSONDecodeError, ...
        raise SpecError(f"cannot parse spec {path}: {exc}") from exc

    if data is None:
        raise SpecError(f"spec {path} is empty")
    if not isinstance(data, dict):
        raise SpecError(f"spec {path} must be a top-level mapping")
    return data


class PortsConfig:
    """Validated ``ports.yaml``: the declared-ports allow list."""

    def __init__(self, data: Dict[str, Any]):
        self.listen_port = _as_port(_require(data, "listen_port", "ports"), "ports")
        self.ssh_port = _as_port(_require(data, "ssh_port", "ports"), "ports")
        if self.listen_port == self.ssh_port:
            raise SpecError(
                "ports: listen_port and ssh_port must differ "
                f"(both {self.listen_port})"
            )
        self.allowed_tcp = [
            _as_port(p, "ports.allowed_tcp") for p in _as_int_list(data.get("allowed_tcp"))
        ]
        self.allowed_udp = [
            _as_port(p, "ports.allowed_udp") for p in _as_int_list(data.get("allowed_udp"))
        ]
        _assert_unique(self.allowed_tcp, "ports.allowed_tcp")
        _assert_unique(self.allowed_udp, "ports.allowed_udp")

    def allow_list(self) -> Dict[str, set]:
        """The declared ports, keyed by protocol. Always includes SSH + WG."""
        tcp = {self.ssh_port} | set(self.allowed_tcp)
        udp = {self.listen_port} | set(self.allowed_udp)
        return {"tcp": sorted(tcp), "udp": sorted(udp)}


def _as_int_list(value: Any) -> List[int]:
    items = _as_str_list(value, "ports.list")
    return [_as_port(p, "ports.list") for p in items]


def _assert_unique(values: List[int], name: str) -> None:
    seen = set()
    for v in values:
        if v in seen:
            raise SpecError(f"{name}: duplicate port {v}")
        seen.add(v)


class PeerSpec:
    """One entry from ``wg.yaml`` ``peers``."""

    def __init__(self, data: Dict[str, Any], key_dir: str):
        self.name = str(_require(data, "name", f"peer {data}"))
        self.address = str(_require(data, "address", f"peer {self.name}"))
        # private_key is optional in the spec: when omitted the generator
        # allocates one from the key dir.
        self.private_key = (
            str(data["private_key"]) if data.get("private_key") else None
        )
        self.allowed_ips = _as_str_list(data.get("allowed_ips"), f"peer {self.name}")
        if not self.allowed_ips:
            # Default: this peer's own /32 (v4) or /128 (v6) address.
            self.allowed_ips = [self.address.split("/", 1)[0] + "/32"]
        # A relative key path is resolved against the spec's key_dir.
        if self.private_key and not os.path.isabs(self.private_key):
            self.private_key = os.path.join(key_dir, self.private_key)


class ServerSpec:
    """The ``server`` block of ``wg.yaml``."""

    def __init__(self, data: Dict[str, Any], key_dir: str):
        self.private_key = str(_require(data, "private_key", "server"))
        if not os.path.isabs(self.private_key):
            self.private_key = os.path.join(key_dir, self.private_key)
        self.address = str(_require(data, "address", "server"))
        self.listen_port = _as_port(data.get("listen_port", 51820), "server")
        self.dns = data.get("dns")


class WgSpec:
    """Validated ``wg.yaml``: the server + peers for the WireGuard config."""

    def __init__(self, data: Dict[str, Any]):
        self.key_dir = str(data.get("key_dir", "keys"))
        if "server" not in data or data["server"] is None:
            raise SpecError("wg: missing required 'server' block")
        self.server = ServerSpec(data["server"], self.key_dir)
        peers = data.get("peers", [])
        if isinstance(peers, dict):
            peers = list(peers.values())
        if not peers:
            raise SpecError("wg: 'peers' must list at least one peer")
        self.peers: List[PeerSpec] = [PeerSpec(p, self.key_dir) for p in peers]
        _assert_unique_peer_names(self.peers)
        _assert_unique_addresses(self.peers)


def _assert_unique_peer_names(peers: List[PeerSpec]) -> None:
    seen = set()
    for p in peers:
        if p.name in seen:
            raise SpecError(f"wg: duplicate peer name {p.name!r}")
        seen.add(p.name)


def _assert_unique_addresses(peers: List[PeerSpec]) -> None:
    seen = set()
    for p in peers:
        addr = p.address.split("/", 1)[0]
        if addr in seen:
            raise SpecError(f"wg: duplicate peer address {addr}")
        seen.add(addr)


def load_ports_config(path: str) -> PortsConfig:
    """Load and validate a ``ports.yaml`` file."""
    return PortsConfig(load_config(path))


def load_wg_spec(path: str) -> WgSpec:
    """Load and validate a ``wg.yaml`` file.

    The spec's ``key_dir`` is resolved relative to the spec file's
    directory (not the current working directory) so the ``wg`` command
    behaves the same no matter where it is invoked from. A peer that
    omits ``private_key`` is given a deterministic default of
    ``<key_dir>/<name>.private``.
    """
    data = load_config(path)
    spec_dir = os.path.dirname(os.path.abspath(path))
    key_dir = data.get("key_dir", "keys")
    if not os.path.isabs(key_dir):
        data["key_dir"] = os.path.normpath(os.path.join(spec_dir, key_dir))
    spec = WgSpec(data)
    for peer in spec.peers:
        if peer.private_key is None:
            peer.private_key = os.path.join(spec.key_dir, f"{peer.name}.private")
    return spec
