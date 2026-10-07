"""Tests for the baseline systemd-networkd units and resolved drop-in.

The configs live in ``networkd/``. These tests pin the
structure and the baseline policy of those files. They run without root
and without network access; real bring-up is covered by opt-in
integration tests behind ``PENGYOS_IT=1``.
"""
import ipaddress
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETWORKD_DIR = os.path.join(REPO_ROOT, "networkd")
RESOLVED_DIR = os.path.join(NETWORKD_DIR, "resolved")

DEFAULT_NETWORK = os.path.join(NETWORKD_DIR, "10-default.network")
TEST_NETDEV = os.path.join(NETWORKD_DIR, "20-pengyos-test.netdev")
TEST_NETWORK = os.path.join(NETWORKD_DIR, "20-pengyos-test.network")
RESOLVED_DROPIN = os.path.join(RESOLVED_DIR, "90-pengyos.conf")

# Prefixes that belong to container/VM tooling. The baseline default
# unit must never claim these interfaces.
VIRTUAL_PREFIXES = {"docker*", "veth*", "tap*", "tun*", "virbr*", "br-*", "wg*"}

SECTION_RE = re.compile(r"\[([A-Za-z][A-Za-z0-9_]*)\]$")
KEY_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse_systemd_ini(text):
    """Parse a systemd .network/.netdev/resolved.conf style file.

    Returns ``{section: {key: [value, ...]}}``. Duplicate keys are legal
    in systemd unit files, so each key maps to a list of values (in
    order). Raises ``ValueError`` on any line that is not a blank line,
    a ``#``/``;`` comment, a ``[Section]`` header, or ``Key = value``.
    """
    sections = {}
    current = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        m = SECTION_RE.match(line)
        if m:
            current = m.group(1)
            sections.setdefault(current, {})
            continue
        m = KEY_RE.match(line)
        if not (m and current is not None):
            raise ValueError(f"line {lineno}: malformed: {line!r}")
        key, value = m.group(1), m.group(2).strip()
        sections[current].setdefault(key, []).append(value)
    if current is None:
        raise ValueError("no [Section] header found")
    return sections


def _values(sections, section, key):
    return sections[section][key]


def _single(sections, section, key):
    values = _values(sections, section, key)
    assert len(values) == 1, f"{section}/{key} must be set exactly once"
    return values[0]


def _match_names(conf):
    """Match.Name globs; systemd allows several per line (space-separated)."""
    return [n for v in _values(conf, "Match", "Name") for n in v.split()]


def assert_baseline_default_network(conf):
    """Policy pinning for 10-default.network. Raises AssertionError if the
    config no longer matches the baseline."""
    assert "Match" in conf, "[Match] section required"
    assert "Network" in conf, "[Network] section required"
    names = _match_names(conf)
    assert names, "[Match]/Name must be set"
    assert not set(names) & VIRTUAL_PREFIXES, (
        f"default unit must not match virtual interfaces: "
        f"{set(names) & VIRTUAL_PREFIXES}"
    )
    assert _single(conf, "Network", "DHCP") == "yes", (
        "baseline requires DHCP=yes in [Network]"
    )
    assert _single(conf, "Network", "IPv6AcceptRA") == "yes"
    assert _single(conf, "DHCPv4", "UseDNS") == "yes"
    assert _single(conf, "IPv6AcceptRA", "UseDNS") == "yes"


# ---------------------------------------------------------------- fixtures


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ------------------------------------------------------------------- tests


def test_network_units_present():
    assert os.path.isfile(DEFAULT_NETWORK), "10-default.network missing"
    assert os.path.isfile(TEST_NETDEV), "20-pengyos-test.netdev missing"
    assert os.path.isfile(TEST_NETWORK), "20-pengyos-test.network missing"


def test_resolved_dropin_present():
    assert os.path.isfile(RESOLVED_DROPIN), "resolved drop-in missing"


def test_all_network_units_parse():
    files = [
        f
        for f in sorted(os.listdir(NETWORKD_DIR))
        if f.endswith((".network", ".netdev"))
    ]
    assert files, "no .network/.netdev files found in networkd/"
    for name in files:
        conf = parse_systemd_ini(_read(os.path.join(NETWORKD_DIR, name)))
        if name.endswith(".network"):
            assert "Match" in conf, f"{name}: [Match] required"
            assert "Network" in conf, f"{name}: [Network] required"


def test_netdev_files_parse_with_kind():
    conf = parse_systemd_ini(_read(TEST_NETDEV))
    assert "NetDev" in conf
    name = _single(conf, "NetDev", "Name")
    kind = _single(conf, "NetDev", "Kind")
    assert re.fullmatch(r"[a-z0-9_-]{1,15}", name), "invalid ifname"
    # Baseline only ships the offline dummy device; anything else needs
    # its own review before being added.
    assert kind == "dummy", f"baseline ships only dummy netdevs, got {kind!r}"


def test_default_unit_matches_physical_prefixes():
    conf = parse_systemd_ini(_read(DEFAULT_NETWORK))
    names = set(_match_names(conf))
    assert "en*" in names, "must match predictable wired names (en*)"
    assert "wlan*" in names, "must match wireless names (wlan*)"
    assert not names & VIRTUAL_PREFIXES


def test_default_unit_policy_pinning():
    conf = parse_systemd_ini(_read(DEFAULT_NETWORK))
    assert_baseline_default_network(conf)


def test_default_unit_without_dhcp_fails_pinning():
    text = _read(DEFAULT_NETWORK).replace("DHCP=yes", "DHCP=off")
    conf = parse_systemd_ini(text)
    with pytest.raises(AssertionError, match="DHCP"):
        assert_baseline_default_network(conf)


def test_default_unit_claiming_docker_fails_pinning():
    # Insert into the [Match] section (a Name= at file end would parse
    # under the last section, [IPv6AcceptRA]).
    text = _read(DEFAULT_NETWORK).replace(
        "Name=en* eth* wlan* wwan*", "Name=en* eth* wlan* wwan* docker*"
    )
    conf = parse_systemd_ini(text)
    with pytest.raises(AssertionError, match="virtual"):
        assert_baseline_default_network(conf)


def test_test_netdev_pairs_with_test_network():
    netdev = parse_systemd_ini(_read(TEST_NETDEV))
    network = parse_systemd_ini(_read(TEST_NETWORK))
    ifname = _single(netdev, "NetDev", "Name")
    assert _values(network, "Match", "Name") == [ifname], (
        "20-pengyos-test.network must match the netdev's Name"
    )
    addresses = _values(network, "Network", "Address")
    assert len(addresses) == 1
    # strict=False: systemd-networkd accepts an address with host bits
    # set, so the test mirrors that lenient behaviour.
    net = ipaddress.ip_network(addresses[0], strict=False)
    assert isinstance(net, ipaddress.IPv4Network)
    assert net.is_private, "test interface address must be RFC 1918"
    assert net.prefixlen < 32, "test interface address must be a subnet"


def test_resolved_dropin_policy_pinning():
    conf = parse_systemd_ini(_read(RESOLVED_DROPIN))
    assert "Resolve" in conf
    assert _single(conf, "Resolve", "DNSStubListener") == "yes"
    assert _single(conf, "Resolve", "CacheFromNetwork") == "no"
    # The baseline must never point the resolver at the stub listener
    # itself (127.0.0.53) -- that is a loop.
    dns = conf.get("DNS", {}).get("DNS", [])
    assert "127.0.0.53" not in dns, "self-referential upstream DNS (loop)"
    fallback = conf.get("DNS", {}).get("FallbackDNS", [])
    assert fallback, "drop-in should pin an explicit FallbackDNS"
    for value in fallback:
        for ip in value.split():
            assert ipaddress.ip_address(ip) != ipaddress.ip_address(
                "127.0.0.53"
            ), "FallbackDNS must not be the stub listener (loop)"


def test_resolved_dropin_allows_explicit_upstream():
    # A drop-in that pins real upstream resolvers is accepted by the
    # same pinning logic (regression guard for the loop check).
    text = "[Resolve]\nDNSStubListener=yes\nCacheFromNetwork=no\nDNS=1.1.1.1\n"
    conf = parse_systemd_ini(text)
    assert "127.0.0.53" not in conf.get("DNS", {}).get("DNS", [])


def test_parser_rejects_malformed_line():
    with pytest.raises(ValueError, match="malformed"):
        parse_systemd_ini("[Match]\nName=en*\nthis is not valid\n")


def test_parser_rejects_key_before_section():
    with pytest.raises(ValueError):
        parse_systemd_ini("Name=en*\n[Match]\n")


def test_parser_rejects_bad_section_name():
    with pytest.raises(ValueError):
        parse_systemd_ini("[not a section]\nKey=value\n")


def test_parser_allows_duplicate_keys():
    conf = parse_systemd_ini("[Match]\nName=en*\nName=wlan*\n")
    assert conf["Match"]["Name"] == ["en*", "wlan*"]
