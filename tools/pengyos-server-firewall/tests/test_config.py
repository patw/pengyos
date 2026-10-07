"""Tests for the YAML/JSON spec loader (:mod:`pengyos_netctl.config`)."""

import json

import pytest

from pengyos_netctl.config import (
    PeerSpec,
    PortsConfig,
    ServerSpec,
    WgSpec,
    load_config,
    load_ports_config,
    load_wg_spec,
)
from pengyos_netctl.errors import SpecError


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# ------------------------------------------------------------- load_config


def test_load_yaml(tmp_path):
    path = _write(
        tmp_path,
        "a.yaml",
        "listen_port: 51820\nssh_port: 22\n",
    )
    data = load_config(path)
    assert data["listen_port"] == 51820
    assert data["ssh_port"] == 22


def test_load_json(tmp_path):
    path = _write(tmp_path, "a.json", json.dumps({"ssh_port": 22}))
    assert load_config(path) == {"ssh_port": 22}


def test_load_missing_raises():
    with pytest.raises(SpecError, match="spec not found"):
        load_config("/nonexistent/definitely/not/here.yaml")


def test_load_non_mapping_raises(tmp_path):
    path = _write(tmp_path, "a.yaml", "- 1\n- 2\n")
    with pytest.raises(SpecError, match="top-level mapping"):
        load_config(path)


def test_load_empty_raises(tmp_path):
    path = _write(tmp_path, "a.yaml", "")
    with pytest.raises(SpecError, match="empty"):
        load_config(path)


# ---------------------------------------------------------------- PortsConfig


def test_ports_config_allow_list_includes_ssh_and_wireguard():
    cfg = PortsConfig(
        {
            "listen_port": 51820,
            "ssh_port": 22,
            "allowed_tcp": [443, 8080],
            "allowed_udp": [1194],
        }
    )
    allow = cfg.allow_list()
    assert allow["tcp"] == [22, 443, 8080]
    assert allow["udp"] == [1194, 51820]


def test_ports_config_accepts_single_value():
    cfg = PortsConfig({"listen_port": 51820, "ssh_port": 22, "allowed_tcp": 8080})
    assert cfg.allow_list()["tcp"] == [22, 8080]


def test_ports_config_requires_listen_port():
    with pytest.raises(SpecError, match="listen_port"):
        PortsConfig({"ssh_port": 22})


def test_ports_config_requires_ssh_port():
    with pytest.raises(SpecError, match="ssh_port"):
        PortsConfig({"listen_port": 51820})


def test_ports_config_listen_and_ssh_must_differ():
    with pytest.raises(SpecError, match="must differ"):
        PortsConfig({"listen_port": 22, "ssh_port": 22})


def test_ports_config_bad_port():
    with pytest.raises(SpecError, match="port"):
        PortsConfig({"listen_port": 70000, "ssh_port": 22})


def test_ports_config_duplicate_tcp():
    with pytest.raises(SpecError, match="duplicate"):
        PortsConfig({"listen_port": 51820, "ssh_port": 22, "allowed_tcp": [443, 443]})


def test_ports_config_from_file(tmp_path):
    path = _write(
        tmp_path,
        "ports.yaml",
        "listen_port: 51820\nssh_port: 22\nallowed_tcp: [443]\n",
    )
    cfg = load_ports_config(path)
    assert cfg.allow_list()["tcp"] == [22, 443]


# ------------------------------------------------------------------ WgSpec


def _wg_spec():
    return {
        "key_dir": "keys",
        "server": {
            "private_key": "server.private",
            "address": "10.8.0.1/24",
            "listen_port": 51820,
            "dns": "10.8.0.1",
        },
        "peers": [
            {"name": "alice", "address": "10.8.0.2/24", "private_key": "alice.private"},
            {"name": "bob", "address": "10.8.0.3/24"},
        ],
    }


def test_wg_spec_server_and_peers():
    spec = WgSpec(_wg_spec())
    assert isinstance(spec.server, ServerSpec)
    assert spec.server.listen_port == 51820
    assert spec.server.dns == "10.8.0.1"
    assert [p.name for p in spec.peers] == ["alice", "bob"]


def test_wg_spec_resolves_relative_key_paths():
    spec = WgSpec(_wg_spec())
    assert spec.server.private_key.endswith("keys/server.private")
    assert spec.peers[0].private_key.endswith("keys/alice.private")


def test_wg_spec_defaults_allowed_ips_to_own_address():
    spec = WgSpec(_wg_spec())
    bob = spec.peers[1]  # bob has no allowed_ips
    assert bob.allowed_ips == ["10.8.0.3/32"]


def test_wg_spec_absolute_key_path_left_alone():
    data = _wg_spec()
    data["server"]["private_key"] = "/etc/wg/server.private"
    spec = WgSpec(data)
    assert spec.server.private_key == "/etc/wg/server.private"


def test_wg_spec_requires_server():
    with pytest.raises(SpecError, match="server"):
        WgSpec({"peers": [{"name": "a", "address": "10.8.0.2/24"}]})


def test_wg_spec_requires_at_least_one_peer():
    with pytest.raises(SpecError, match="peers"):
        WgSpec(
            {
                "key_dir": "keys",
                "server": {"private_key": "s", "address": "10.8.0.1/24"},
            }
        )


def test_wg_spec_duplicate_peer_names():
    data = _wg_spec()
    data["peers"].append({"name": "alice", "address": "10.8.0.9/24"})
    with pytest.raises(SpecError, match="duplicate peer name"):
        WgSpec(data)


def test_wg_spec_duplicate_addresses():
    data = _wg_spec()
    data["peers"].append({"name": "dupe", "address": "10.8.0.2/24"})
    with pytest.raises(SpecError, match="duplicate peer address"):
        WgSpec(data)


def test_peer_spec_requires_name_and_address():
    with pytest.raises(SpecError, match="name"):
        PeerSpec({"address": "10.8.0.2/24"}, "keys")
    with pytest.raises(SpecError, match="address"):
        PeerSpec({"name": "x"}, "keys")
