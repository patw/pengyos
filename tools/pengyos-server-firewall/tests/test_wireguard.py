"""Tests for the WireGuard config generator (:mod:`pengyos_netctl.wireguard`).

Covers:

* ``x25519`` against the RFC 7748 §5.2 / §6.1 test vectors (and, when the
  ``cryptography`` library is importable, against it as a second oracle),
* base64 key encode/decode round-trips and error handling,
* ``wg0`` server + per-peer rendering (sections, fields, keepalive/endpoint),
* :func:`generate` end-to-end: keys chmod 0600, no private-key leak across
  configs, deterministic output, and a clean failure on a bad spec.

Standard library only; the ``cryptography`` oracle is optional.
"""

import base64
import os
import re
import stat

import pytest

from pengyos_netctl.errors import KeyFormatError, NetctlError, OutputError
from pengyos_netctl.wireguard import (
    WgOutput,
    decode_private_key,
    encode_private_key,
    generate,
    generate_private_key,
    private_to_public,
    render_peer_config,
    render_server_config,
    x25519,
)

# RFC 7748 §5.2 single-multiplication test vectors (hex).
RFC7748_52 = [
    (
        "a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4",
        "e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c",
        "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552",
    ),
    (
        "4b66e9d4d1b4673c5ad22691957d6af5c11b6421e0ea01d42ca4169e7918ba0d",
        "e5210f12786811d3f4b7959d0538ae2c31dbe7106fc03c3efc4cd549c715a493",
        "95cbde9476e8907d7aade45cb4b873f88b595a68799fa152e6f8f7647aac7957",
    ),
]

# RFC 7748 §6.1 ECDH test vector (hex).
RFC7748_61_ALICE_PRIV = "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a"
RFC7748_61_ALICE_PUB = "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
RFC7748_61_BOB_PRIV = "5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb"
RFC7748_61_BOB_PUB = "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
RFC7748_61_K = "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742"


def _clamp25519(raw: bytes) -> int:
    """RFC 7748 §5.1 scalar clamp, applied to raw little-endian bytes."""
    kl = list(raw)
    kl[0] &= 248
    kl[31] &= 127
    kl[31] |= 64
    return int.from_bytes(bytes(kl), "little")


def _b64(hexstr: str) -> str:
    """Hex string -> base64 (the on-disk WireGuard key encoding)."""
    return base64.b64encode(bytes.fromhex(hexstr)).decode("ascii")


# -------------------------------------------------------------- x25519


@pytest.mark.parametrize("priv_hex,u_hex,want_hex", RFC7748_52)
def test_x25519_matches_rfc7748_52(priv_hex, u_hex, want_hex):
    """The Montgomery ladder matches the RFC 7748 §5.2 test vectors."""
    u = int.from_bytes(bytes.fromhex(u_hex), "little")
    k = _clamp25519(bytes.fromhex(priv_hex))
    got = x25519(k, u).to_bytes(32, "little").hex()
    assert got == want_hex


def test_x25519_matches_cryptography_library():
    """Cross-check against ``cryptography`` when it is importable."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    for priv_hex, _u_hex, _want_hex in RFC7748_52:
        raw = bytes.fromhex(priv_hex)
        theirs = X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes_raw()
        k = _clamp25519(raw)
        mine = x25519(k, 9).to_bytes(32, "little")
        assert mine == theirs


def test_private_to_public_matches_rfc7748_61():
    """Alice's/Bob's public keys match RFC 7748 §6.1 (via base64 keys)."""
    # ``private_to_public`` returns base64; the RFC constants are hex, so
    # compare against the base64 of the documented public key.
    assert private_to_public(_b64(RFC7748_61_ALICE_PRIV)) == _b64(RFC7748_61_ALICE_PUB)
    assert private_to_public(_b64(RFC7748_61_BOB_PRIV)) == _b64(RFC7748_61_BOB_PUB)


def test_x25519_agreement_gives_shared_secret():
    """The ECDH shared secret is symmetric (K = a·B = b·A), RFC 7748 §6.1."""
    a = _clamp25519(bytes.fromhex(RFC7748_61_ALICE_PRIV))
    b = _clamp25519(bytes.fromhex(RFC7748_61_BOB_PRIV))
    a_pub = int.from_bytes(bytes.fromhex(RFC7748_61_ALICE_PUB), "little")
    b_pub = int.from_bytes(bytes.fromhex(RFC7748_61_BOB_PUB), "little")
    kab = x25519(b, a_pub)
    kba = x25519(a, b_pub)
    assert kab.to_bytes(32, "little").hex() == RFC7748_61_K
    assert kba.to_bytes(32, "little").hex() == RFC7748_61_K


def test_x25519_base_point_u_is_masked():
    """The top bit of a received u-coordinate is masked (§5.5)."""
    u = 9 | (1 << 255)  # set the otherwise-masked top bit
    assert x25519(_clamp25519(b"\x00" * 32), u) == x25519(_clamp25519(b"\x00" * 32), 9)


# ----------------------------------------------------------- key I/O


def test_encode_decode_round_trip():
    raw = os.urandom(32)
    key = encode_private_key(raw)
    assert decode_private_key(key) == raw


def test_encode_private_key_shape():
    """A WireGuard key is 44 base64 chars ending in '=', decoding to 32 bytes."""
    key = generate_private_key()
    assert len(key) == 44
    assert key.endswith("=")
    assert len(base64.b64decode(key)) == 32


def test_generate_private_key_is_random():
    assert generate_private_key() != generate_private_key()


def test_decode_private_key_tolerates_whitespace():
    key = generate_private_key()
    assert decode_private_key("  " + key + "\n") == base64.b64decode(key)


def test_decode_private_key_rejects_bad_base64():
    with pytest.raises(KeyFormatError, match="not valid base64"):
        decode_private_key("!!!not base64!!!")


def test_decode_private_key_rejects_wrong_length():
    with pytest.raises(KeyFormatError, match="32 bytes"):
        decode_private_key(base64.b64encode(b"too short").decode())


def test_encode_private_key_rejects_wrong_length():
    with pytest.raises(KeyFormatError, match="32 bytes"):
        encode_private_key(b"short")


# ------------------------------------------------------------- rendering


def _server_spec():
    from pengyos_netctl.config import ServerSpec

    return ServerSpec(
        {
            "private_key": "server.private",
            "address": "10.8.0.1/24",
            "listen_port": 51820,
            "dns": "10.8.0.1",
        },
        "keys",
    )


def _peer_spec(name="alice", address="10.8.0.2/24"):
    from pengyos_netctl.config import PeerSpec

    return PeerSpec({"name": name, "address": address}, "keys")


def test_render_server_config_has_interface_and_peers():
    server = _server_spec()
    peer = _peer_spec()
    text = render_server_config(server, "srv_key", [(peer, "peer_pub")])
    assert "[interface]" in text
    assert "private_key = srv_key" in text
    assert "address = 10.8.0.1/24" in text
    assert "listen_port = 51820" in text
    assert "dns = 10.8.0.1" in text
    assert "[peer: alice]" in text
    assert "public_key = peer_pub" in text
    assert "allowed_ips = " in text


def test_render_server_config_deterministic_order():
    server = _server_spec()
    p1 = _peer_spec("alice")
    p2 = _peer_spec("bob")
    text = render_server_config(server, "k", [(p1, "a"), (p2, "b")])
    assert text.index("[peer: alice]") < text.index("[peer: bob]")


def test_render_peer_config_has_interface_and_server_peer():
    peer = _peer_spec()
    text = render_peer_config(peer, "peer_key", "srv_pub", ["10.8.0.0/24"])
    assert "[interface]" in text
    assert "private_key = peer_key" in text
    assert "address = 10.8.0.2/24" in text
    assert "[peer: server]" in text
    assert "public_key = srv_pub" in text
    assert "allowed_ips = 10.8.0.0/24" in text
    assert "persistent_keepalive_interval = 25" in text


def test_render_peer_config_endpoint_optional():
    peer = _peer_spec()
    with_ep = render_peer_config(peer, "k", "srv", ["10.8.0.0/24"], endpoint="1.2.3.4:51820")
    assert "endpoint = 1.2.3.4:51820" in with_ep
    without = render_peer_config(peer, "k", "srv", ["10.8.0.0/24"])
    assert "endpoint" not in without


def test_render_peer_config_private_key_absent_from_other_configs():
    """No private key bytes leak into a config that is not their owner's."""
    peer = _peer_spec()
    peer_text = render_peer_config(peer, "ALICE_PRIV", "srv_pub", ["10.8.0.0/24"])
    server_text = render_server_config(_server_spec(), "SRIV_PRIV", [(peer, "ALICE_PUB")])
    assert "ALICE_PRIV" not in server_text
    assert "SRIV_PRIV" not in peer_text


# -------------------------------------------------------------- generate


def _write_wg_spec(tmp_path, *, with_peer_key=True):
    """A valid wg.yaml: server + two peers (each with an explicit key)."""
    lines = [
        "key_dir: keys",
        "server:",
        "  private_key: server.private",
        "  address: 10.8.0.1/24",
        "  listen_port: 51820",
        "  dns: 10.8.0.1",
        "peers:",
        "  - name: alice",
        "    address: 10.8.0.2/24",
    ]
    if with_peer_key:
        lines.append("    private_key: alice.private")
    lines += [
        "  - name: bob",
        "    address: 10.8.0.3/24",
    ]
    if with_peer_key:
        lines.append("    private_key: bob.private")
    spec = tmp_path / "wg.yaml"
    spec.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(spec)


def test_generate_writes_server_and_peer_configs(tmp_path):
    spec = _write_wg_spec(tmp_path)
    out = str(tmp_path / "wg")
    result = generate(_load(spec), out)

    assert os.path.isfile(os.path.join(out, "wg0.conf"))
    assert os.path.isfile(os.path.join(out, "alice.conf"))
    assert os.path.isfile(os.path.join(out, "bob.conf"))
    assert set(result.peer_configs) == {"alice", "bob"}
    assert isinstance(result, WgOutput)


def test_generate_keys_are_chmod_0600(tmp_path):
    spec = _write_wg_spec(tmp_path)
    out = str(tmp_path / "wg")
    generate(_load(spec), out)
    key_dir = tmp_path / "keys"
    for key in key_dir.iterdir():
        mode = stat.S_IMODE(os.stat(str(key)).st_mode)
        assert mode == 0o600, f"{key.name} is {oct(mode)}, expected 0600"


def test_generate_reports_freshly_written_keys(tmp_path):
    spec = _write_wg_spec(tmp_path)
    out = str(tmp_path / "wg")
    result = generate(_load(spec), out)
    # Every key file was allocated on this first run.
    names = {os.path.basename(p) for p in result.keys_written}
    assert names == {"server.private", "alice.private", "bob.private"}


def test_generate_reuses_existing_keys(tmp_path):
    spec = _write_wg_spec(tmp_path)
    out1 = str(tmp_path / "wg1")
    out2 = str(tmp_path / "wg2")
    first = generate(_load(spec), out1)
    assert first.keys_written  # allocated on the first run
    second = generate(_load(spec), out2)
    assert second.keys_written == []  # all already present


def test_generate_no_private_key_leak(tmp_path):
    spec = _write_wg_spec(tmp_path)
    out = str(tmp_path / "wg")
    generate(_load(spec), out)

    wg0 = (tmp_path / "wg" / "wg0.conf").read_text(encoding="utf-8")
    alice = (tmp_path / "wg" / "alice.conf").read_text(encoding="utf-8")
    bob = (tmp_path / "wg" / "bob.conf").read_text(encoding="utf-8")

    server_priv = (tmp_path / "keys" / "server.private").read_text(encoding="utf-8").strip()
    alice_priv = (tmp_path / "keys" / "alice.private").read_text(encoding="utf-8").strip()
    bob_priv = (tmp_path / "keys" / "bob.private").read_text(encoding="utf-8").strip()

    # Each private key lives ONLY in its owner's config.
    assert server_priv in wg0 and server_priv not in alice and server_priv not in bob
    assert alice_priv in alice and alice_priv not in wg0 and alice_priv not in bob
    assert bob_priv in bob and bob_priv not in wg0 and bob_priv not in alice


def test_generate_no_key_material_in_non_owner_rendered_text(tmp_path):
    """Beyond the owner's file, no key bytes appear anywhere in the output dir."""
    spec = _write_wg_spec(tmp_path)
    out = str(tmp_path / "wg")
    generate(_load(spec), out)

    owners = {
        "wg0.conf": "server.private",
        "alice.conf": "alice.private",
        "bob.conf": "bob.private",
    }
    key_paths = {name: tmp_path / "keys" / key for name, key in owners.items()}
    for fname in (tmp_path / "wg").iterdir():
        owner_key = owners[fname.name]
        other = {n: p.read_text(encoding="utf-8").strip()
                 for n, p in key_paths.items() if n != fname.name}
        text = fname.read_text(encoding="utf-8")
        for value in other.values():
            assert value not in text, f"{owner_key} leaked into {fname.name}"


def test_generate_public_keys_are_consistent(tmp_path):
    """wg0's per-peer public_key matches that peer's own private key."""
    spec = _write_wg_spec(tmp_path)
    out = str(tmp_path / "wg")
    generate(_load(spec), out)

    wg0 = (tmp_path / "wg" / "wg0.conf").read_text(encoding="utf-8")
    alice_priv = (tmp_path / "keys" / "alice.private").read_text(encoding="utf-8").strip()
    expected = private_to_public(alice_priv)
    # The [peer: alice] block's public_key must equal the derived public key.
    block = wg0.split("[peer: alice]")[1].split("[")[0]
    assert f"public_key = {expected}" in block


def test_generate_is_deterministic_same_keys(tmp_path):
    """Same key material -> byte-identical rendered configs."""
    spec = _write_wg_spec(tmp_path)
    out1 = str(tmp_path / "wg1")
    out2 = str(tmp_path / "wg2")
    first = generate(_load(spec), out1)
    second = generate(_load(spec), out2)
    assert first.server_config == second.server_config
    assert first.peer_configs == second.peer_configs


def test_generate_bad_key_fails_cleanly(tmp_path):
    """A corrupt key file yields a NetctlError (clean exit 2), not a crash."""
    spec = _write_wg_spec(tmp_path)
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys" / "alice.private").write_text("NOT-A-KEY\n", encoding="utf-8")
    out = str(tmp_path / "wg")
    with pytest.raises(NetctlError):
        generate(_load(spec), out)


def test_generate_output_dir_is_created(tmp_path):
    spec = _write_wg_spec(tmp_path)
    out = str(tmp_path / "nested" / "wg")
    generate(_load(spec), out)
    assert os.path.isfile(os.path.join(out, "wg0.conf"))


# ------------------------------------------------------------------ helpers


def _load(path):
    from pengyos_netctl.config import load_wg_spec

    return load_wg_spec(path)
