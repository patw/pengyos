"""Join a Wi-Fi network from the install stick (Server: wpa_supplicant + networkd).

A laptop server may have no Ethernet port, and the SSH page's key import needs
the network, so the installer can join Wi-Fi. It writes the same file the
installed system uses, ``/etc/wpa_supplicant/wpa_supplicant-nl80211-<iface>.conf``,
and starts ``wpa_supplicant-nl80211@<iface>.service``; networkd gives the
interface an address (``20-pengyos-wireless.network``). The install engine
copies that file to the new system and enables the unit there, so the machine
comes back up on the same network after the reboot.

Hidden networks and WPA-Enterprise are left to ``wpa_cli`` after the install.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

__all__ = ["Network", "Wifi", "WifiError", "WrongPassword", "parse_scan", "conf_text",
           "password_problem"]

WPA_DIR = "/etc/wpa_supplicant"
CTRL_DIR = "/run/wpa_supplicant"
UNIT = "wpa_supplicant-nl80211@{}.service"


class WifiError(Exception):
    """Joining or scanning failed; the message says why, for the user."""


class WrongPassword(WifiError):
    """The network turned the password down."""


@dataclass(frozen=True)
class Network:
    ssid: str
    signal: float  # dBm
    secured: bool

    @property
    def bars(self) -> str:
        n = 4 if self.signal >= -55 else 3 if self.signal >= -67 else 2 if self.signal >= -75 else 1
        return "#" * n + "." * (4 - n)


def _unescape(ssid: str) -> str:
    """iw prints bytes it cannot show as \\xHH."""
    raw = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), ssid)
    return raw.encode("latin-1", "replace").decode("utf-8", "replace")


def parse_scan(text: str) -> list[Network]:
    """``iw dev <iface> scan`` output -> visible networks, strongest first, one
    per name (hidden networks, which have no name, are left out)."""
    found: dict[str, Network] = {}
    cur: dict | None = None

    def flush() -> None:
        if cur and cur.get("ssid"):
            n = Network(cur["ssid"], cur.get("signal", -100.0), cur.get("secured", False))
            if n.ssid not in found or n.signal > found[n.ssid].signal:
                found[n.ssid] = n

    for line in text.splitlines():
        if line.startswith("BSS "):
            flush()
            cur = {}
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("signal:"):
            try:
                cur["signal"] = float(s.split()[1])
            except (IndexError, ValueError):
                pass
        elif s.startswith("SSID:"):
            cur["ssid"] = _unescape(s[5:].strip())
        elif s.startswith("capability:") and "Privacy" in s:
            cur["secured"] = True
        elif s.startswith(("RSN:", "WPA:")):
            cur["secured"] = True
    flush()
    return sorted(found.values(), key=lambda n: -n.signal)


def password_problem(password: str) -> str | None:
    """None if *password* can be a WPA passphrase (8-63 printable ASCII) or a
    raw 64-hex-digit key, else why not."""
    if re.fullmatch(r"[0-9a-fA-F]{64}", password):
        return None
    if not 8 <= len(password) <= 63:
        return "Wi-Fi passwords are 8 to 63 characters"
    if not all(" " <= c <= "~" for c in password):
        return "Wi-Fi passwords use plain ASCII letters, digits and symbols only"
    return None


def conf_text(ssid: str, password: str) -> str:
    """The wpa_supplicant config for one network (an empty *password*: open)."""
    lines = [
        "# Written by the PengyOS installer: the Wi-Fi network joined while installing.",
        "# Change networks with `sudo wpa_cli`, or edit this file and restart",
        "# wpa_supplicant-nl80211@<interface>.service.",
        f"ctrl_interface=DIR={CTRL_DIR} GROUP=wheel",
        "update_config=1",
        "",
        "network={",
        # Hex: any name, quotes and all, without escaping rules.
        f"\tssid={ssid.encode('utf-8').hex()}",
    ]
    if not password:
        lines.append("\tkey_mgmt=NONE")
    else:
        # WPA2 and WPA3 (SAE) with the same passphrase; management frame
        # protection when the router offers it (WPA3 requires it).
        lines.append("\tkey_mgmt=WPA-PSK SAE")
        if re.fullmatch(r"[0-9a-fA-F]{64}", password):
            lines.append(f"\tpsk={password.lower()}")
        else:
            lines.append(f'\tpsk="{password}"')
        lines.append("\tieee80211w=1")
    lines += ["}", ""]
    return "\n".join(lines)


def _run_default(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


class Wifi:
    """The live system's Wi-Fi. Every path and command is injectable (tests)."""

    def __init__(self, *, run: Callable = _run_default, sys_net: str = "/sys/class/net",
                 sys_rfkill: str = "/sys/class/rfkill", wpa_dir: str = WPA_DIR,
                 sleep: Callable[[float], None] = time.sleep,
                 connect_timeout: float = 30, address_timeout: float = 25) -> None:
        self.run, self.sleep = run, sleep
        self.sys_net, self.sys_rfkill, self.wpa_dir = Path(sys_net), Path(sys_rfkill), Path(wpa_dir)
        self.connect_timeout, self.address_timeout = connect_timeout, address_timeout

    # ------------------------------------------------------------- the hardware
    def interfaces(self) -> list[str]:
        if not self.sys_net.is_dir():
            return []
        return sorted(d.name for d in self.sys_net.iterdir()
                      if (d / "wireless").exists() or (d / "phy80211").exists())

    def address(self, iface: str) -> str:
        r = self.run(["ip", "-4", "-o", "addr", "show", "dev", iface])
        m = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)", r.stdout or "")
        return m.group(1) if m else ""

    def wired_address(self) -> str:
        """The first IPv4 address on a wired interface ('' if none)."""
        if not self.sys_net.is_dir():
            return ""
        for d in sorted(self.sys_net.iterdir()):
            if d.name == "lo" or (d / "wireless").exists() or (d / "phy80211").exists():
                continue
            if not (d / "device").exists():  # virtual: bridges, veths, ...
                continue
            addr = self.address(d.name)
            if addr:
                return addr
        return ""

    def _unblock(self) -> None:
        """Clear a software rfkill block (a Fn key pressed long ago); a hardware
        switch cannot be cleared from here."""
        if not self.sys_rfkill.is_dir():
            return
        for d in self.sys_rfkill.iterdir():
            try:
                if (d / "type").read_text().strip() != "wlan":
                    continue
                if (d / "hard").read_text().strip() == "1":
                    raise WifiError("Wi-Fi is switched off by the laptop's Wi-Fi switch "
                                    "or key; turn it on and scan again")
                if (d / "soft").read_text().strip() == "1":
                    (d / "soft").write_text("0")
            except OSError:
                continue

    # ----------------------------------------------------------------- scanning
    def scan(self, iface: str) -> list[Network]:
        self._unblock()
        self.run(["ip", "link", "set", iface, "up"])
        for attempt in range(4):
            try:
                r = self.run(["iw", "dev", iface, "scan"], timeout=30)
            except FileNotFoundError:
                raise WifiError("iw is not available on this installer") from None
            except subprocess.TimeoutExpired:
                raise WifiError("the Wi-Fi scan did not finish; scan again") from None
            if r.returncode == 0:
                return parse_scan(r.stdout)
            err = (r.stderr or "").strip()
            if "busy" in err.lower() and attempt < 3:
                self.sleep(2)  # wpa_supplicant (or the kernel) is scanning already
                continue
            if "rf-kill" in err.lower():
                raise WifiError("Wi-Fi is switched off (rfkill); turn it on and scan again")
            raise WifiError(f"the Wi-Fi scan failed: {err.splitlines()[-1] if err else r.returncode}")
        raise WifiError("the Wi-Fi card stayed busy; scan again")  # pragma: no cover

    # ------------------------------------------------------------------ joining
    def conf_path(self, iface: str) -> Path:
        return self.wpa_dir / f"wpa_supplicant-nl80211-{iface}.conf"

    def _wpa_cli(self, iface: str, *args: str) -> str:
        r = self.run(["wpa_cli", "-p", CTRL_DIR, "-i", iface, *args])
        return r.stdout or ""

    def connect(self, iface: str, ssid: str, password: str) -> str:
        """Join *ssid*; return the address DHCP gave. On failure nothing is left
        configured (the engine would otherwise copy a broken network)."""
        if password and (problem := password_problem(password)):
            raise WifiError(problem)
        conf = self.conf_path(iface)
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(conf_text(ssid, password))
        conf.chmod(0o600)
        try:
            r = self.run(["systemctl", "restart", UNIT.format(iface)])
            if r.returncode != 0:
                raise WifiError(f"could not start wpa_supplicant: {(r.stderr or '').strip()}")
            waited = 0.0
            while True:
                state = re.search(r"^wpa_state=(\S+)", self._wpa_cli(iface, "status"), re.M)
                if state and state.group(1) == "COMPLETED":
                    break
                if "TEMP-DISABLED" in self._wpa_cli(iface, "list_networks"):
                    raise WrongPassword(f"{ssid} refused the password")
                if waited >= self.connect_timeout:
                    raise WifiError(f"could not join {ssid}; is it in range?")
                self.sleep(1)
                waited += 1
            waited = 0.0
            while not (addr := self.address(iface)):
                if waited >= self.address_timeout:
                    raise WifiError(f"joined {ssid} but got no address from its DHCP server")
                self.sleep(1)
                waited += 1
            return addr
        except BaseException:
            self.forget(iface)
            raise

    def forget(self, iface: str) -> None:
        """Leave the network and remove its config."""
        self.run(["systemctl", "stop", UNIT.format(iface)])
        try:
            self.conf_path(iface).unlink()
        except FileNotFoundError:
            pass
