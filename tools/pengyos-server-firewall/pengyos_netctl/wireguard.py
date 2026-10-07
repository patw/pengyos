"""WireGuard config generator (US-SRV-5).

Generate a ``wg0`` server config plus one config per peer from a
:class:`~pengyos_netctl.config.WgSpec`. Private keys are read from the
spec's ``key_dir`` (generated when absent) and written ``chmod 0600``; they
are injected into the one config that belongs to each key and are never
printed to stdout.

Standard library only:

* ``x25519`` key derivation follows RFC 7748 (a constant-time Montgomery
  ladder). It is verified against the RFC 7748 test vectors and the
  ``cryptography`` library's ``X25519`` exchange — see the module tests.
* Private keys are drawn with :func:`secrets.token_bytes`.
* Network addresses are handled with :mod:`ipaddress`.

## Key format

WireGuard keys are base64-encoded 32-byte values (``wg genkey`` output):
44 characters ending in ``=``, possibly with surrounding whitespace. A
private key is *clamped* (RFC 7748 §5.5) before it is used in ``x25519``;
the clamp is applied inside :func:`x25519`, so callers only ever pass the
raw decoded key.

## Output layout

``wg0.conf`` (the server) lists the server's own ``private_key``/``address``/
``listen_port`` and, for every peer, that peer's ``public_key`` and
``allowed_ips``. Each peer's own config lists its own ``private_key``/
``address`` and the server's ``public_key`` (plus ``allowed_ips`` covering
the server subnet and a keepalive). Private keys never leave the config
that belongs to their owner.
"""

from __future__ import annotations

import base64
import ipaddress
import os
import secrets
from typing import Dict, List, Optional, Tuple, Union

from .config import PeerSpec, ServerSpec, WgSpec
from .errors import KeyFormatError, NetctlError, OutputError

# Both ServerSpec and PeerSpec expose ``private_key`` (a resolved path), so
# this helper accepts either.
_KeyOwner = Union[ServerSpec, PeerSpec]

__all__ = [
    "WgOutput",
    "generate",
    "private_to_public",
    "generate_private_key",
    "decode_private_key",
    "encode_private_key",
    "x25519",
    "render_server_config",
    "render_peer_config",
]

# The WireGuard base point is the curve point whose u-coordinate is 9.
_BASEPOINT_U = 9

# Field prime for Curve25519: p = 2^255 - 19.
_P = 2**255 - 19

# (A - 2) / 4 for the Montgomery curve y^2 = x^3 + A x^2 + x with A = 486662.
_A24 = 121665


# ------------------------------------------------------------- x25519 (RFC 7748)


def x25519(private: int, u: int) -> int:
    """Return ``u`` scaled by ``private`` on Curve25519 (RFC 7748).

    ``private`` is a Python int; it is clamped per RFC 7748 §5.5 before use.
    ``u`` is the u-coordinate of the input point and its most-significant
    bit is masked first (§5.5: implementations MUST mask the top bit of the
    final byte of a received u-coordinate). The result is the u-coordinate
    of the output point, as an int in ``[0, p)``.

    This is a constant-time Montgomery ladder. It is exact (pure Python
    big-int arithmetic) and matches the RFC 7748 test vectors and the
    ``cryptography`` library.
    """
    # Mask the top bit of the (32-byte) u-coordinate, then reduce mod p.
    u = (u & ~(1 << 255)) % _P
    # Clamp the scalar: clear the low 3 bits, clear bit 255, set bit 254.
    k = private & ~(2**3 - 1)
    k &= ~(1 << 255)
    k |= 1 << 254

    x1 = u
    x2 = 1
    z2 = 0
    x3 = u
    z3 = 1
    swap = 0
    for t in range(254, -1, -1):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = (da + cb)
        x3 = x3 * x3 % _P
        z3 = x1 * ((da - cb) % _P) % _P
        z3 = z3 * ((da - cb) % _P) % _P
        x2 = aa * bb % _P
        z2 = e * ((aa + _A24 * e) % _P) % _P
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return x2 * pow(z2, _P - 2, _P) % _P


def encode_private_key(raw: bytes) -> str:
    """Encode 32 raw bytes as a WireGuard base64 private key."""
    if len(raw) != 32:
        raise KeyFormatError(f"private key must be 32 bytes, got {len(raw)}")
    return base64.b64encode(raw).decode("ascii")


def decode_private_key(raw: str) -> bytes:
    """Validate and decode a WireGuard base64 private key to 32 raw bytes.

    Surrounding whitespace (e.g. a trailing newline from ``wg genkey``) is
    tolerated. Raises :class:`KeyFormatError` on malformed input.
    """
    text = raw.strip()
    try:
        decoded = base64.b64decode(text, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # pragma: no cover - defensive
        raise KeyFormatError(f"private key is not valid base64: {exc}") from exc
    if len(decoded) != 32:
        raise KeyFormatError(
            f"private key must decode to 32 bytes, got {len(decoded)}"
        )
    return decoded


def private_to_public(private_key: str) -> str:
    """Return the base64 public key for a base64 private key string."""
    raw = decode_private_key(private_key)
    u = int.from_bytes(raw, "little")
    pub = x25519(u, _BASEPOINT_U)
    return base64.b64encode(pub.to_bytes(32, "little")).decode("ascii")


def generate_private_key() -> str:
    """Return a fresh random base64 WireGuard private key."""
    return encode_private_key(secrets.token_bytes(32))


# ------------------------------------------------------------- key management


def _resolve_private_key(owner: _KeyOwner, key_label: str) -> str:
    """Return the (possibly freshly generated) base64 private key for ``owner``.

    The key lives at ``owner.private_key`` (an absolute path or one resolved
    against the spec's ``key_dir``). When the file is missing a new key is
    generated, written ``chmod 0600`` to that path, and returned. When it is
    present it is read and validated. The returned value is the raw base64
    string (no surrounding whitespace).

    Returns ``(key, fresh)`` where ``fresh`` is ``True`` when this call
    allocated (created) the key file rather than reading an existing one.
    """
    path = owner.private_key
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read().strip()
        except OSError as exc:
            raise NetctlError(f"cannot read {key_label} key {path}: {exc}") from exc
        # Validate the key decodes to 32 bytes, but return the base64 string
        # itself (not the decoded bytes) so callers can pass it straight to
        # private_to_public.
        decode_private_key(raw)
        return raw, False
    key = generate_private_key()
    try:
        _write_key_file(path, key)
    except OSError as exc:
        raise OutputError(f"cannot write {key_label} key {path}: {exc}") from exc
    return key, True


def _write_key_file(path: str, key: str) -> None:
    """Write ``key`` to ``path`` at mode 0600 (creating parent dirs)."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(key + "\n")
    os.chmod(path, 0o600)


# ------------------------------------------------------------- rendering


def _section(header: str, lines: List[str]) -> str:
    """Render one ``[header]`` block from its (non-empty) body ``lines``."""
    return f"[{header}]\n" + "\n".join(lines)


def _interface_lines(
    private_key: str, address: str, extra: List[Tuple[str, str]]
) -> List[str]:
    """The ``[interface]`` body: private_key, address, then extras."""
    lines = [
        f"private_key = {private_key}",
        f"address = {address}",
    ]
    lines.extend(f"{key} = {value}" for key, value in extra)
    return lines


def _peer_lines(
    public_key: str,
    allowed_ips: List[str],
    extra: List[Tuple[str, str]],
) -> List[str]:
    """The ``[peer: ...]`` body: public_key, allowed_ips, then extras."""
    lines = [
        f"public_key = {public_key}",
        f"allowed_ips = {', '.join(allowed_ips)}",
    ]
    lines.extend(f"{key} = {value}" for key, value in extra)
    return lines


def render_server_config(
    server: ServerSpec,
    server_key: str,
    peers: List[Tuple[PeerSpec, str]],
) -> str:
    """Render ``wg0.conf``: the server interface plus one block per peer.

    ``peers`` is a list of ``(peer, peer_public_key)`` tuples in the order
    they should appear in the file (deterministic: the spec order).
    """
    interface_extra: List[Tuple[str, str]] = []
    if server.listen_port is not None:
        interface_extra.append(("listen_port", str(server.listen_port)))
    if server.dns:
        interface_extra.append(("dns", str(server.dns)))

    blocks: List[str] = [_section("interface", _interface_lines(
        server_key, server.address, interface_extra
    ))]
    for peer, peer_pub in peers:
        blocks.append(_section(f"peer: {peer.name}", _peer_lines(
            peer_pub, peer.allowed_ips, []
        )))
    return _config_document("wg0 — server interface", "\n\n".join(blocks))


def render_peer_config(
    peer: PeerSpec,
    peer_key: str,
    server_public: str,
    server_allowed_ips: List[str],
    endpoint: Optional[str] = None,
) -> str:
    """Render a single peer's client config.

    ``server_allowed_ips`` is what the client routes through the server
    (typically the server's subnet). ``endpoint`` is optional and, when
    given, becomes the server's ``endpoint``.
    """
    interface_extra: List[Tuple[str, str]] = []
    blocks: List[str] = [_section("interface", _interface_lines(
        peer_key, peer.address, interface_extra
    ))]
    peer_extra: List[Tuple[str, str]] = [("persistent_keepalive_interval", "25")]
    if endpoint:
        peer_extra.insert(0, ("endpoint", endpoint))
    blocks.append(_section("peer: server", _peer_lines(
        server_public, server_allowed_ips, peer_extra
    )))
    return _config_document(f"{peer.name} — peer config", "\n\n".join(blocks))


def _config_document(comment: str, body: str) -> str:
    """Wrap a config body in the standard generated-file header."""
    header = (
        f"# {comment} — generated by `pengyos-netctl wg` (US-SRV-5)\n"
        "# Do not edit by hand; regenerate with:\n"
        "#   pengyos-netctl wg --spec <spec> --out <dir>\n"
    )
    return header + body + "\n"


def _server_subnet(server: ServerSpec) -> str:
    """The network the server subnet covers, from ``server.address`` (CIDR)."""
    net = ipaddress.ip_network(server.address, strict=False)
    return str(net)


# ------------------------------------------------------------- orchestration


class WgOutput:
    """The result of :func:`generate`: rendered configs + written keys.

    Attributes:

    * ``server_config`` — the ``wg0.conf`` text.
    * ``peer_configs`` — ``{peer_name: <config text>}`` in spec order.
    * ``keys_written`` — the absolute paths of any key files created.
    """

    def __init__(
        self,
        server_config: str,
        peer_configs: Dict[str, str],
        keys_written: List[str],
    ) -> None:
        self.server_config = server_config
        self.peer_configs = peer_configs
        self.keys_written = keys_written


def generate(spec: WgSpec, out_dir: str) -> WgOutput:
    """Render the full server + peer config pair from ``spec`` into ``out_dir``.

    Private keys are resolved (or generated) from the spec's ``key_dir``,
    written ``chmod 0600`` when new, and injected into the config that
    belongs to each key. The server config is written to ``<out_dir>/wg0.conf``
    and each peer's config to ``<out_dir>/<name>.conf``. Returns a
    :class:`WgOutput` describing everything that was written.

    Raises :class:`NetctlError` (incl. :class:`KeyFormatError` /
    :class:`OutputError`) on any failure so ``main`` can turn it into a
    clean ``exit 2``.
    """
    keys_written: List[str] = []
    server_key, server_fresh = _resolve_private_key(spec.server, "server")
    if server_fresh:
        keys_written.append(spec.server.private_key)
    peer_keys: Dict[str, str] = {}
    for peer in spec.peers:
        peer_keys[peer.name], peer_created = _resolve_private_key(peer, "peer")
        if peer_created:
            keys_written.append(peer.private_key)

    server_public = private_to_public(server_key)
    peer_public = {name: private_to_public(k) for name, k in peer_keys.items()}

    server_subnet = _server_subnet(spec.server)
    server_config = render_server_config(
        spec.server, server_key, [(p, peer_public[p.name]) for p in spec.peers]
    )
    peer_configs: Dict[str, str] = {}
    for peer in spec.peers:
        peer_configs[peer.name] = render_peer_config(
            peer,
            peer_keys[peer.name],
            server_public,
            [server_subnet],
        )

    try:
        os.makedirs(out_dir, exist_ok=True)
        _write_config(os.path.join(out_dir, "wg0.conf"), server_config)
        for peer in spec.peers:
            _write_config(os.path.join(out_dir, f"{peer.name}.conf"), peer_configs[peer.name])
    except OSError as exc:
        raise OutputError(f"cannot write configs to {out_dir}: {exc}") from exc

    return WgOutput(server_config, peer_configs, keys_written)


def _write_config(path: str, text: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o644)
