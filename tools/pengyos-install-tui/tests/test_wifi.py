"""Joining Wi-Fi from the install stick (pengyos_install_tui.wifi), with fake
sysfs, a fake `iw`/`wpa_cli`/`systemctl`/`ip`, and no sleeping."""

from __future__ import annotations

import subprocess

import pytest

from pengyos_install_tui.wifi import (
    Network, Wifi, WifiError, WrongPassword, conf_text, parse_scan, password_problem,
)

SCAN = """\
BSS 02:00:00:00:00:00(on wlp2s0)
\tfreq: 2412
\tcapability: ESS Privacy ShortSlotTime (0x0411)
\tsignal: -71.00 dBm
\tSSID: Home
\tRSN:\t * Version: 1
BSS 02:00:00:00:00:01(on wlp2s0)
\tcapability: ESS ShortSlotTime (0x0401)
\tsignal: -60.00 dBm
\tSSID: Cafe
BSS 02:00:00:00:00:02(on wlp2s0)
\tcapability: ESS Privacy (0x0411)
\tsignal: -45.00 dBm
\tSSID: Home
\tRSN:\t * Version: 1
BSS 02:00:00:00:00:03(on wlp2s0)
\tsignal: -30.00 dBm
\tSSID:
BSS 02:00:00:00:00:04(on wlp2s0)
\tsignal: -80.00 dBm
\tSSID: caf\\xc3\\xa9 \\x22quoted\\x22
\tWPA:\t * Version: 1
"""


def test_parse_scan_dedupes_sorts_and_drops_hidden():
    nets = parse_scan(SCAN)
    assert [n.ssid for n in nets] == ["Home", "Cafe", 'café "quoted"']
    home, cafe, odd = nets
    assert home.signal == -45.0 and home.secured  # the stronger of the two "Home"s
    assert not cafe.secured and odd.secured
    assert home.bars == "####" and cafe.bars == "###." and odd.bars == "#..."


@pytest.mark.parametrize("pw,ok", [
    ("12345678", True), ("x" * 63, True), ("a" * 64, True), ("A1" * 32, True),
    ("1234567", False), ("x" * 64, False), ("pässword", False), ("tab\there", False),
])
def test_password_problem(pw, ok):
    assert (password_problem(pw) is None) is ok


def test_conf_text():
    secured = conf_text('My "Net"', 'p@ss"word')
    assert "\tssid=4d7920224e657422\n" in secured  # hex: quotes need no escaping
    assert '\tpsk="p@ss"word"\n' in secured        # wpa_supplicant reads to the last quote
    assert "\tkey_mgmt=WPA-PSK SAE\n" in secured and "\tieee80211w=1\n" in secured
    assert "ctrl_interface=DIR=/run/wpa_supplicant GROUP=wheel" in secured
    assert "\tpsk=" + "ab" * 32 + "\n" in conf_text("n", "AB" * 32)  # a raw key, unquoted
    open_ = conf_text("Cafe", "")
    assert "\tkey_mgmt=NONE\n" in open_ and "psk" not in open_


class Sys:
    """Fake commands. `states` is what successive `wpa_cli status` calls report."""

    def __init__(self, tmp_path, *, scan_results=None, states=("COMPLETED",), flags="",
                 address="192.168.1.77", restart_rc=0):
        self.calls = []
        self.scan_results = list(scan_results or [(0, SCAN, "")])
        self.states, self.flags = list(states), flags
        self.address, self.restart_rc = address, restart_rc
        net = tmp_path / "net"
        for name, kind in (("lo", None), ("enp1s0", "eth"), ("wlp2s0", "wifi"), ("docker0", "virt")):
            d = net / name
            d.mkdir(parents=True)
            if kind in ("eth", "wifi"):
                (d / "device").mkdir()
            if kind == "wifi":
                (d / "phy80211").mkdir()
        self.net = net
        self.rfkill = tmp_path / "rfkill"
        self.wpa = tmp_path / "wpa"

    def rf(self, name, kind="wlan", soft="0", hard="0"):
        d = self.rfkill / name
        d.mkdir(parents=True)
        (d / "type").write_text(kind + "\n")
        (d / "soft").write_text(soft + "\n")
        (d / "hard").write_text(hard + "\n")
        return d

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        out = lambda rc=0, o="", e="": subprocess.CompletedProcess(argv, rc, o, e)  # noqa: E731
        if argv[:2] == ["iw", "dev"]:
            rc, o, e = self.scan_results.pop(0)
            return out(rc, o, e)
        if argv[0] == "systemctl":
            return out(self.restart_rc if argv[1] == "restart" else 0, "", "boom")
        if argv[0] == "wpa_cli" and argv[-1] == "status":
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return out(0, f"bssid=02:00\nwpa_state={state}\n")
        if argv[0] == "wpa_cli" and argv[-1] == "list_networks":
            return out(0, f"network id / ssid / bssid / flags\n0\tHome\tany\t{self.flags}\n")
        if argv[:3] == ["ip", "-4", "-o"]:
            dev = argv[-1]
            if dev == "wlp2s0" and self.address:
                return out(0, f"3: wlp2s0    inet {self.address}/24 brd x scope global\n")
            if dev == "enp1s0":
                return out(0, "2: enp1s0    inet 10.0.0.9/24 brd x scope global\n")
            return out(0, "")
        return out()

    def wifi(self, **kw):
        return Wifi(run=self, sys_net=str(self.net), sys_rfkill=str(self.rfkill),
                    wpa_dir=str(self.wpa), sleep=lambda s: None, **kw)


def test_interfaces_and_wired_address(tmp_path):
    sys = Sys(tmp_path)
    w = sys.wifi()
    assert w.interfaces() == ["wlp2s0"]
    assert w.wired_address() == "10.0.0.9"  # enp1s0; not lo, the Wi-Fi or docker0


def test_scan_unblocks_soft_rfkill_and_retries_when_busy(tmp_path):
    sys = Sys(tmp_path, scan_results=[(240, "", "command failed: Device or resource busy (-16)"),
                                      (0, SCAN, "")])
    soft = sys.rf("rfkill0", soft="1")
    sys.rf("rfkill1", kind="bluetooth", soft="1")
    nets = sys.wifi().scan("wlp2s0")
    assert [n.ssid for n in nets] == ["Home", "Cafe", 'café "quoted"']
    assert (soft / "soft").read_text() == "0"
    assert (sys.rfkill / "rfkill1" / "soft").read_text().strip() == "1"  # not Wi-Fi: left alone
    assert ["ip", "link", "set", "wlp2s0", "up"] in sys.calls
    assert sum(c[:2] == ["iw", "dev"] for c in sys.calls) == 2


def test_scan_hardware_switch(tmp_path):
    sys = Sys(tmp_path)
    sys.rf("rfkill0", hard="1")
    with pytest.raises(WifiError, match="Wi-Fi switch"):
        sys.wifi().scan("wlp2s0")


def test_scan_failure(tmp_path):
    sys = Sys(tmp_path, scan_results=[(1, "", "command failed: Operation not supported (-95)")])
    with pytest.raises(WifiError, match="scan failed: command failed: Operation not supported"):
        sys.wifi().scan("wlp2s0")


def test_connect_writes_the_installed_systems_file(tmp_path):
    sys = Sys(tmp_path, states=["SCANNING", "ASSOCIATING", "COMPLETED"])
    w = sys.wifi()
    assert w.connect("wlp2s0", "Home", "correct horse") == "192.168.1.77"
    conf = sys.wpa / "wpa_supplicant-nl80211-wlp2s0.conf"
    assert conf.stat().st_mode & 0o777 == 0o600
    assert 'psk="correct horse"' in conf.read_text()
    assert ["systemctl", "restart", "wpa_supplicant-nl80211@wlp2s0.service"] in sys.calls


def test_wrong_password_leaves_nothing_behind(tmp_path):
    sys = Sys(tmp_path, states=["SCANNING"], flags="[TEMP-DISABLED]")
    with pytest.raises(WrongPassword, match="Home refused the password"):
        sys.wifi().connect("wlp2s0", "Home", "wrong password")
    assert not (sys.wpa / "wpa_supplicant-nl80211-wlp2s0.conf").exists()
    assert ["systemctl", "stop", "wpa_supplicant-nl80211@wlp2s0.service"] in sys.calls


def test_out_of_range_and_no_dhcp(tmp_path):
    sys = Sys(tmp_path, states=["SCANNING"])
    with pytest.raises(WifiError, match="could not join Home"):
        sys.wifi(connect_timeout=3).connect("wlp2s0", "Home", "correct horse")
    sys = Sys(tmp_path / "2", address="")
    with pytest.raises(WifiError, match="no address from its DHCP server"):
        sys.wifi(address_timeout=3).connect("wlp2s0", "Home", "correct horse")
    assert not (sys.wpa / "wpa_supplicant-nl80211-wlp2s0.conf").exists()


def test_bad_password_is_refused_before_anything_runs(tmp_path):
    sys = Sys(tmp_path)
    with pytest.raises(WifiError, match="8 to 63"):
        sys.wifi().connect("wlp2s0", "Home", "short")
    assert sys.calls == []


def test_open_network(tmp_path):
    sys = Sys(tmp_path)
    sys.wifi().connect("wlp2s0", "Cafe", "")
    assert "key_mgmt=NONE" in (sys.wpa / "wpa_supplicant-nl80211-wlp2s0.conf").read_text()


def test_network_bars():
    assert Network("x", -90, False).bars == "#..."
