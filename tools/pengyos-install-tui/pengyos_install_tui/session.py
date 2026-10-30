"""The installer's pages, fields and rules — pure, no terminal and no disk I/O.

Both front-ends (:mod:`~pengyos_install_tui.screen`, the full-screen one, and
:mod:`~pengyos_install_tui.plain`, the line-by-line fallback) render the same
:class:`Session`: the same pages in the same order, the same validators, the
same review. A front-end only decides how a page looks and how keys move
between fields; whether an answer is acceptable is always decided here, by the
engine's own :meth:`pengyos_install.image.ProvisionAnswers.validate`.

Pages::

    disk -> name -> account -> [wifi -> [wifi_password]] -> ssh -> pengy
         -> [login, Desktop only] -> review

The Wi-Fi pages appear only where there is a Wi-Fi interface (a laptop), and the
password page only for a secured network. They come before the SSH page, whose
key import needs the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pengyos_install import image as engine
from pengyos_install_tui.wifi import Network, WifiError
from pengyos_install_tui.keys import (
    Imported, KeyFetchError, KeySource, PasswordRefused, fetch_keys, parse_source,
)

__all__ = ["Field", "Page", "Option", "Session", "CONFIRM_WORD", "DEFAULTS"]

#: What the user types on the review page to erase the disk and install.
CONFIRM_WORD = "erase"

#: Answers filled in before the user types anything.
DEFAULTS = {"hostname": "pengyos"}


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    hint: str = ""
    secret: bool = False
    optional: bool = False


@dataclass(frozen=True)
class Option:
    """One row of a choice page (a disk, or yes/no for automatic login)."""

    label: str
    note: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class Page:
    key: str
    title: str
    intro: str
    fields: tuple[Field, ...] = ()
    #: True for pages whose answer is one row of a list (see Session.options).
    choice: bool = False


DISK = Page(
    "disk", "Choose a disk",
    "PengyOS will be installed on the disk you choose. Everything on that disk "
    "will be erased. The USB stick this installer runs from is listed but cannot "
    "be chosen.",
    choice=True,
)
NAME = Page(
    "name", "Name this computer",
    "The name it answers to on your network and shows at its prompt.",
    (Field("hostname", "Computer name", "letters, digits and '-'"),),
)
ACCOUNT = Page(
    "account", "Create your account",
    "Your everyday login. It can use sudo, and the password is also set for root.",
    (
        Field("username", "Username", "lowercase, e.g. pat"),
        Field("password", "Password", "at least 6 characters", secret=True),
        Field("password2", "Repeat password", secret=True),
    ),
)
SSH = Page(
    "ssh", "SSH access",
    "SSH on PengyOS accepts keys only, never passwords. Import public keys from a "
    "GitHub user, a URL, or user@host (another machine's authorized_keys, copied "
    "with its password); paste one; or leave all empty to log in here only.",
    (
        Field("key_source", "Import keys from", "GitHub user, http(s):// URL or user@host",
              optional=True),
        Field("key_password", "Password", "only for user@host", secret=True, optional=True),
        Field("ssh_key", "Or paste a key", "ssh-ed25519 AAAA... you@host", optional=True),
    ),
)
PENGY = Page(
    "pengy", "Set up Pengy",
    "Pengy is the assistant that ships with PengyOS. Point it at an "
    "OpenAI-compatible endpoint now, or leave the URL empty to set it up later.",
    (
        Field("pengy_base_url", "Base URL", "e.g. http://192.168.1.20:11434/v1",
              optional=True),
        Field("pengy_api_key", "API key", "local endpoints need none",
              secret=True, optional=True),
        Field("pengy_model", "Model", "e.g. qwen3:8b", optional=True),
    ),
)
WIFI = Page(
    "wifi", "Wi-Fi",
    "Join a Wi-Fi network if this machine will not have a network cable. The "
    "installed system joins it again at every boot.",
    choice=True,
)
WIFI_PASSWORD = Page(
    "wifi_password", "Wi-Fi password",
    "The password of the network you chose (8 to 63 characters).",
    (Field("wifi_password", "Password", secret=True),),
)
LOGIN = Page(
    "login", "Logging in",
    "A Desktop starts at its login screen. It can skip it and log you straight "
    "in, which is handy on a machine nobody else uses.",
    choice=True,
)
REVIEW = Page(
    "review", "Ready to install",
    "Check the summary. Installing ERASES EVERYTHING on the chosen disk.",
    (Field("confirm", f"Type '{CONFIRM_WORD}' to install"),),
)

LOGIN_OPTIONS = (
    Option("Ask for the password", "recommended"),
    Option("Log in automatically"),
)


def _answers_problem(**kw) -> str | None:
    """The engine's verdict on one or two answers, with the rest set to valid
    placeholders: None, or the reason the value is refused."""
    base = dict(hostname="pengyos", username="user", password="xxxxxx")
    base.update(kw)
    try:
        engine.ProvisionAnswers(**base).validate()
    except engine.ImageInstallError as exc:
        return str(exc)
    return None


class Session:
    """Everything the user has answered so far, and the rules for each answer.

    ``fetch_keys(source, password) -> Imported`` is injected so tests never
    reach the network.
    """

    def __init__(self, manifest: engine.Manifest, disks: list[engine.Disk], *,
                 fetch_keys: Callable[[KeySource, str], Imported] = fetch_keys,
                 wifi=None) -> None:
        self.edition = manifest.edition
        self.version = manifest.version
        self.disks = list(disks)
        self.fetch_keys = fetch_keys
        self.values: dict[str, str] = dict(DEFAULTS)
        self.disk: engine.Disk | None = None
        self.autologin = False
        #: Keys imported on the ssh page, and where from.
        self.imported: Imported | None = None
        self.imported_from: KeySource | None = None
        #: (source text, password) of that import, to import again on a change.
        self._fetched_for: tuple[str, str] | None = None
        #: Set once the user was warned that skipping SSH means console only.
        self.ssh_skip_warned = False
        #: The live system's Wi-Fi (a pengyos_install_tui.wifi.Wifi), if it has an
        #: interface; the Wi-Fi pages exist only then.
        self.wifi = wifi
        self.wifi_iface = (wifi.interfaces() or [""])[0] if wifi else ""
        #: Networks from the last scan (None: not scanned yet), the one chosen
        #: (None: no Wi-Fi), the wired address seen at that scan.
        self.networks: list[Network] | None = None
        self.network: Network | None = None
        self.wired = ""
        #: (ssid, password) currently joined, and the address it gave.
        self._joined: tuple[str, str] | None = None
        self.wifi_address = ""
        choices = self.selectable()
        if len(choices) == 1:
            self.disk = choices[0]

    # ----------------------------------------------------------- the pages
    @property
    def title(self) -> str:
        return f"PengyOS {self.edition.capitalize()} {self.version}"

    def pages(self) -> list[Page]:
        pages = [DISK, NAME, ACCOUNT]
        if self.wifi_iface:
            pages.append(WIFI)
            if self.network and self.network.secured:
                pages.append(WIFI_PASSWORD)
        pages += [SSH, PENGY]
        if self.edition == "desktop":
            pages.append(LOGIN)
        pages.append(REVIEW)
        return pages

    # ------------------------------------------------------- choice pages
    def disk_note(self, d: engine.Disk) -> str:
        if d.is_boot:
            return "the installer stick"
        if d.mounted:
            return "in use"
        if d.size_bytes < engine.MIN_DISK:
            return f"too small, needs {engine.Disk('', '', engine.MIN_DISK).size_label}"
        return "removable" if d.removable else ""

    def _disk_ok(self, d: engine.Disk) -> bool:
        return not d.is_boot and not d.mounted and d.size_bytes >= engine.MIN_DISK

    def selectable(self) -> list[engine.Disk]:
        return [d for d in self.disks if self._disk_ok(d)]

    def options(self, page: Page) -> list[Option]:
        if page.key == "disk":
            return [Option(f"{d.path:<14} {d.size_label:>9}  {d.model or '-'}",
                           self.disk_note(d), self._disk_ok(d)) for d in self.disks]
        if page.key == "login":
            return list(LOGIN_OPTIONS)
        if page.key == "wifi":
            wired = f"wired: {self.wired}" if self.wired else "no network cable connected"
            rows = [Option("No Wi-Fi", wired)]
            rows += [Option(n.ssid, f"{n.bars}  {'secured' if n.secured else 'open'}")
                     for n in self.networks or []]
            return rows + [Option("Scan again")]
        raise KeyError(page.key)

    def is_rescan(self, page: Page, index: int) -> bool:
        """True for the Wi-Fi page's "Scan again" row."""
        return page.key == "wifi" and index == len(self.options(page)) - 1

    def scan(self) -> str | None:
        """Look for Wi-Fi networks (slow: a front-end shows a notice first).
        None, or why it failed (the list is then empty)."""
        self.wired = self.wifi.wired_address()
        try:
            self.networks = self.wifi.scan(self.wifi_iface)
        except WifiError as exc:
            self.networks = []
            return str(exc)
        if not self.networks:
            return "no Wi-Fi networks found; scan again, or move closer to the router"
        return None

    def chosen(self, page: Page) -> int | None:
        """Index of the current answer on a choice page (None: not chosen yet)."""
        if page.key == "disk":
            return self.disks.index(self.disk) if self.disk in self.disks else None
        if page.key == "wifi":
            nets = self.networks or []
            return nets.index(self.network) + 1 if self.network in nets else 0
        return 1 if self.autologin else 0

    def choose(self, page: Page, index: int) -> str | None:
        """Answer a choice page with row *index*: None, or why it is refused."""
        opts = self.options(page)
        if not 0 <= index < len(opts):
            return f"choose a number from 1 to {len(opts)}"
        if not opts[index].enabled:
            return f"that disk cannot be used ({opts[index].note})"
        if page.key == "disk":
            self.disk = self.disks[index]
        elif page.key == "wifi":
            if self.is_rescan(page, index):
                return "scan again"
            self.network = self.networks[index - 1] if index else None
            if self.network is None or self._joined and self._joined[0] != self.network.ssid:
                self._leave()
        else:
            self.autologin = index == 1
        return None

    def _leave(self) -> None:
        if self._joined:
            self.wifi.forget(self.wifi_iface)
        self._joined, self.wifi_address = None, ""

    def _join(self, password: str) -> tuple[str, str] | None:
        """Join the chosen network: None, or (field, problem)."""
        ssid = self.network.ssid
        try:
            self.wifi_address = self.wifi.connect(self.wifi_iface, ssid, password)
        except WifiError as exc:
            self._joined, self.wifi_address = None, ""
            return ("wifi_password" if self.network.secured else "", str(exc))
        self._joined = (ssid, password)
        return None

    # --------------------------------------------------------- form pages
    def check(self, key: str, value: str) -> str | None:
        """Validate one field on its own: None, or the reason it is refused."""
        v = self.values
        if not value:
            if key in ("hostname", "username", "password", "password2", "wifi_password"):
                return "this cannot be empty"
            return None
        if key == "hostname":
            return _answers_problem(hostname=value)
        if key == "username":
            return _answers_problem(username=value)
        if key == "password":
            return _answers_problem(password=value)
        if key == "password2":
            return None if value == v.get("password", "") else "the passwords do not match"
        if key == "key_source":
            return None if parse_source(value) else \
                "type a GitHub username, an http(s):// URL, or user@host"
        if key == "ssh_key":
            return _answers_problem(authorized_keys=value)
        if key == "pengy_base_url":
            return _answers_problem(pengy_base_url=value)
        if key == "pengy_api_key":
            return _answers_problem(pengy_base_url="http://x", pengy_api_key=value)
        if key == "pengy_model":
            return _answers_problem(pengy_base_url="http://x", pengy_model=value)
        if key == "wifi_password":
            from pengyos_install_tui.wifi import password_problem

            return password_problem(value)
        if key == "confirm":
            return None if value.strip().lower() == CONFIRM_WORD else \
                f"type '{CONFIRM_WORD}' to install, or go back"
        return None

    def set(self, key: str, value: str) -> str | None:
        """Store *value* for *key* and return its problem (None if valid).

        The value is kept even when it is refused, so a front-end shows what was
        typed; :meth:`finish` re-checks every field before a page completes."""
        value = value.strip() if key not in ("password", "password2", "key_password",
                                             "wifi_password") else value
        self.values[key] = value
        return self.check(key, value)

    def needs_network(self, page: Page) -> bool:
        """True when finishing *page* will fetch from the network (the front-end
        shows a 'fetching' notice first)."""
        if page.key == "wifi":
            return bool(self.network and not self.network.secured
                        and self._joined != (self.network.ssid, ""))
        if page.key == "wifi_password":
            return bool(self.network) and \
                self._joined != (self.network.ssid, self.values.get("wifi_password", ""))
        return page.key == "ssh" and self._import_wanted() not in (None, self._fetched_for)

    def key_source(self) -> KeySource | None:
        return parse_source(self.values.get("key_source", ""))

    def field_text(self, f: Field) -> tuple[str, str]:
        """The (label, hint) to show for *f* given the answers so far."""
        if f.key == "wifi_password" and self.network:
            return (f"Password for {self.network.ssid}", "")
        if f.key == "key_password":
            src = self.key_source()
            if src and src.needs_password:
                return (f"Password for {src.label}", "")
        return (f.label, f.hint)

    def field_active(self, key: str) -> bool:
        """False for a field the current answers make irrelevant (the plain
        front-end skips those): the password unless keys come from user@host."""
        if key == "key_password":
            src = self.key_source()
            return bool(src and src.needs_password)
        return True

    def _import_wanted(self) -> tuple[str, str] | None:
        src = self.key_source()
        if src is None:
            return None
        password = self.values.get("key_password", "") if src.needs_password else ""
        return (self.values.get("key_source", ""), password)

    def finish(self, page: Page) -> tuple[str, str] | None:
        """Cross-field rules for a whole page, once each field is valid.

        Returns None when the page is complete, else ``(field_key, problem)``
        naming the field to put the cursor back on.
        """
        v = self.values
        if page.key == "disk" and self.disk is None:
            return ("", "choose a disk")
        for f in page.fields:
            problem = self.check(f.key, v.get(f.key, ""))
            if problem:
                return (f.key, problem)
        if page.key == "ssh":
            src, wanted = self.key_source(), self._import_wanted()
            if src is None:
                self.imported = self.imported_from = self._fetched_for = None
            elif src.needs_password and not v.get("key_password"):
                return ("key_password", f"enter the password for {src.label}")
            elif wanted != self._fetched_for:
                try:
                    self.imported = self.fetch_keys(src, wanted[1])
                except KeyFetchError as exc:
                    self.imported = self.imported_from = self._fetched_for = None
                    field = "key_password" if isinstance(exc, PasswordRefused) else "key_source"
                    return (field, str(exc))
                self.imported_from, self._fetched_for = src, wanted
            if not self.authorized_keys and not self.ssh_skip_warned:
                self.ssh_skip_warned = True
                # On the page's last field, so one more Enter skips.
                return ("ssh_key", "without a key this machine can only be used "
                        "from its own keyboard; press Enter again to skip")
        if page.key == "wifi" and self.network and not self.network.secured \
                and self._joined != (self.network.ssid, ""):
            return self._join("")
        if page.key == "wifi_password" and self._joined != (self.network.ssid, v["wifi_password"]):
            return self._join(v["wifi_password"])
        if page.key == "pengy" and not v.get("pengy_base_url"):
            if v.get("pengy_api_key") or v.get("pengy_model"):
                return ("pengy_base_url", "enter the base URL, or clear the key and model")
        return None

    # ------------------------------------------------------------ results
    @property
    def authorized_keys(self) -> str:
        lines = [self.imported.keys if self.imported else "", self.values.get("ssh_key", "")]
        return "\n".join(line for line in lines if line)

    def answers(self) -> engine.ProvisionAnswers:
        v = self.values
        base_url = v.get("pengy_base_url", "")
        return engine.ProvisionAnswers(
            hostname=v.get("hostname", ""), username=v.get("username", ""),
            password=v.get("password", ""), authorized_keys=self.authorized_keys,
            autologin=self.autologin and self.edition == "desktop",
            pengy_base_url=base_url,
            pengy_api_key=v.get("pengy_api_key", "") if base_url else "",
            pengy_model=v.get("pengy_model", "") if base_url else "",
        )

    def _network_summary(self) -> str:
        if self._joined:
            return f"Wi-Fi {self._joined[0]} ({self.wifi_iface}, {self.wifi_address})"
        if self.wired:
            return f"wired ({self.wired})"
        if self.wifi_iface:
            return "none yet (join Wi-Fi later with `sudo wpa_cli`)"
        return "wired"

    def review(self) -> list[tuple[str, str]]:
        """The review page's summary, as (label, value) rows."""
        a, d = self.answers(), self.disk
        keys = [k for k in a.authorized_keys.splitlines() if k.strip()]
        if not keys:
            ssh = "no key: console login only"
        else:
            sources = []
            if self.imported:
                skipped = (f", {self.imported.skipped} line"
                           f"{'s' if self.imported.skipped != 1 else ''} skipped"
                           if self.imported.skipped else "")
                sources.append(f"{self.imported.count} from {self.imported_from.label}{skipped}")
            if self.values.get("ssh_key"):
                sources.append("1 pasted")
            ssh = f"{len(keys)} key{'s' if len(keys) != 1 else ''} ({', '.join(sources)})"
        if a.pengy_base_url:
            pengy = a.pengy_base_url + (f"  model {a.pengy_model}" if a.pengy_model else "")
        else:
            pengy = "not set up (configure it later)"
        rows = [
            ("Disk", f"{d.path}  {d.size_label}  {d.model}".rstrip() if d else "-"),
            ("Computer", a.hostname),
            ("User", a.username),
            ("SSH", ssh),
            ("Network", self._network_summary()),
            ("Pengy", pengy),
        ]
        if self.edition == "desktop":
            rows.append(("Login", "automatic" if a.autologin else "password required"))
        return rows
