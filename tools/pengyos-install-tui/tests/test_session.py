"""The shared pages and rules (pengyos_install_tui.session)."""

from __future__ import annotations

import pytest
from conftest import DISKS, GH_KEYS, HOST_KEYS, HOST_PASSWORD, KEY, FakeFetch

from pengyos_install.image import Disk
from pengyos_install_tui.keys import KeyFetchError
from pengyos_install_tui.session import ACCOUNT, DISK, LOGIN, NAME, PENGY, SSH, Session


def make(manifest, fetcher=None, disks=DISKS):
    return Session(manifest, disks, fetch_keys=fetcher or FakeFetch())


def page(s, key):
    return next(p for p in s.pages() if p.key == key)


def test_server_pages_have_no_login_page(manifest):
    assert [p.key for p in make(manifest).pages()] == \
        ["disk", "name", "account", "ssh", "pengy", "review"]


def test_desktop_adds_the_login_page(desktop_manifest):
    assert "login" in [p.key for p in make(desktop_manifest).pages()]


def test_title_names_edition_and_version(manifest):
    assert make(manifest).title == "PengyOS Server 26.10.50"


def test_only_usable_disks_are_selectable(manifest):
    s = make(manifest)
    opts = s.options(DISK)
    assert [o.enabled for o in opts] == [False, True, False]
    assert opts[0].note == "the installer stick"
    assert "too small" in opts[2].note
    # The single usable disk is preselected.
    assert s.disk is DISKS[1] and s.chosen(DISK) == 1


def test_choosing_an_unusable_disk_is_refused(manifest):
    s = make(manifest)
    assert "cannot be used" in s.choose(DISK, 0)
    assert "from 1 to 3" in s.choose(DISK, 7)
    assert s.choose(DISK, 1) is None and s.disk.path == "/dev/vda"


def test_no_preselection_with_two_usable_disks(manifest):
    two = DISKS + [Disk("nvme0n1", "/dev/nvme0n1", 500_000_000_000, "WD")]
    s = make(manifest, disks=two)
    assert s.disk is None and s.finish(DISK) == ("", "choose a disk")


def test_mounted_disk_is_in_use(manifest):
    s = make(manifest, disks=[Disk("sdb", "/dev/sdb", 64_000_000_000, mounted=["/mnt"])])
    assert s.selectable() == [] and s.options(DISK)[0].note == "in use"


@pytest.mark.parametrize("key,value,needle", [
    ("hostname", "bad host", "invalid hostname"),
    ("hostname", "", "cannot be empty"),
    ("username", "Pat", "invalid username"),
    ("username", "root", "invalid username"),
    ("password", "short", "at least 6"),
    ("key_source", "-nope", "GitHub username, an http(s):// URL, or user@host"),
    ("key_source", "ftp://nas/keys", "GitHub username, an http(s):// URL, or user@host"),
    ("ssh_key", "not-a-key", "not an SSH public key"),
    ("pengy_base_url", "not a url", "invalid Pengy base URL"),
    ("pengy_model", "two words", "invalid Pengy model"),
])
def test_field_problems_come_from_the_engine(manifest, key, value, needle):
    assert needle in make(manifest).set(key, value)


def test_set_keeps_a_refused_value_and_finish_rechecks_it(manifest):
    s = make(manifest)
    s.set("hostname", "bad host")
    assert s.values["hostname"] == "bad host"
    assert s.finish(NAME)[0] == "hostname"
    s.set("hostname", "  box  ")
    assert s.values["hostname"] == "box" and s.finish(NAME) is None


def test_passwords_must_match(manifest):
    s = make(manifest)
    s.set("username", "pat")
    s.set("password", "hunter22")
    assert s.set("password2", "hunter23") == "the passwords do not match"
    assert s.finish(ACCOUNT) == ("password2", "the passwords do not match")
    s.set("password2", "hunter22")
    assert s.finish(ACCOUNT) is None


def test_ssh_fetches_github_keys_once(manifest, fetcher):
    s = make(manifest, fetcher)
    s.set("key_source", "patw")
    assert s.needs_network(SSH) and not s.field_active("key_password")
    assert s.finish(SSH) is None
    assert fetcher.calls == [("github.com/patw", "")] and s.authorized_keys == GH_KEYS
    assert not s.needs_network(SSH)
    assert s.finish(SSH) is None and len(fetcher.calls) == 1  # not fetched twice


def test_ssh_github_and_pasted_key_combine(manifest, fetcher):
    s = make(manifest, fetcher)
    s.set("key_source", "gh:patw")
    s.set("ssh_key", KEY)
    assert s.finish(SSH) is None
    assert s.authorized_keys.splitlines() == GH_KEYS.splitlines() + [KEY]
    assert dict(s.review())["SSH"] == "3 keys (2 from github.com/patw, 1 pasted)"


def test_ssh_keys_from_a_url(manifest, fetcher):
    s = make(manifest, fetcher)
    s.set("key_source", "http://nas.lan/keys")
    assert s.finish(SSH) is None
    assert fetcher.calls == [("http://nas.lan/keys", "")]
    assert "from http://nas.lan/keys" in dict(s.review())["SSH"]


def test_ssh_keys_from_another_machine(manifest, fetcher):
    s = make(manifest, fetcher)
    s.set("key_source", "pat@behoserver")
    assert s.field_active("key_password")
    label, hint = s.field_text(SSH.fields[1])
    assert label == "Password for pat@behoserver" and hint == ""
    assert s.finish(SSH) == ("key_password", "enter the password for pat@behoserver")
    assert fetcher.calls == []
    s.set("key_password", "wrong")
    assert s.finish(SSH) == ("key_password", "pat@behoserver refused the password")
    s.set("key_password", HOST_PASSWORD)
    assert s.needs_network(SSH)
    assert s.finish(SSH) is None and s.authorized_keys == HOST_KEYS
    assert dict(s.review())["SSH"] == "2 keys (2 from pat@behoserver, 1 line skipped)"
    # The password is never part of what gets installed.
    assert HOST_PASSWORD not in repr(s.answers())


def test_a_new_password_imports_again(manifest, fetcher):
    s = make(manifest, fetcher)
    s.set("key_source", "pat@behoserver")
    s.set("key_password", HOST_PASSWORD)
    s.finish(SSH)
    s.set("key_password", HOST_PASSWORD + "x")
    assert s.needs_network(SSH)


def test_ssh_fetch_failure_points_at_the_source_field(manifest):
    s = make(manifest, FakeFetch(error=KeyFetchError("could not reach github.com")))
    s.set("key_source", "patw")
    assert s.finish(SSH) == ("key_source", "could not reach github.com")
    assert s.authorized_keys == ""


def test_ssh_clearing_the_source_drops_its_keys(manifest, fetcher):
    s = make(manifest, fetcher)
    s.set("key_source", "patw")
    s.finish(SSH)
    s.set("key_source", "")
    s.set("ssh_key", KEY)
    assert s.finish(SSH) is None and s.authorized_keys == KEY


def test_skipping_ssh_warns_once(manifest):
    s = make(manifest)
    key, problem = s.finish(SSH)
    assert key == "ssh_key" and "only be used from its own keyboard" in problem
    assert s.finish(SSH) is None
    assert dict(s.review())["SSH"] == "no key: console login only"


def test_pengy_is_optional_but_key_needs_a_url(manifest):
    s = make(manifest)
    assert s.finish(PENGY) is None
    s.set("pengy_model", "qwen3:8b")
    assert s.finish(PENGY)[0] == "pengy_base_url"
    s.set("pengy_base_url", "http://10.0.0.5:11434/v1")
    assert s.finish(PENGY) is None
    a = s.answers()
    assert (a.pengy_base_url, a.pengy_model) == ("http://10.0.0.5:11434/v1", "qwen3:8b")


def test_desktop_login_choice(desktop_manifest):
    s = make(desktop_manifest)
    assert s.chosen(LOGIN) == 0 and s.answers().autologin is False
    s.choose(LOGIN, 1)
    assert s.answers().autologin is True
    assert dict(s.review())["Login"] == "automatic"


def test_answers_and_review(manifest, fetcher):
    s = make(manifest, fetcher)
    for k, v in [("hostname", "box"), ("username", "pat"), ("password", "hunter22"),
                 ("password2", "hunter22"), ("key_source", "patw")]:
        assert s.set(k, v) is None
    s.finish(SSH)
    a = s.answers()
    a.validate()
    assert (a.hostname, a.username, a.password, a.autologin) == ("box", "pat", "hunter22", False)
    review = dict(s.review())
    assert review["Disk"] == "/dev/vda  20.0 GB  QEMU"
    assert review["Pengy"].startswith("not set up")
    assert "Login" not in review


# ------------------------------------------------------------------- Wi-Fi
from conftest import WIFI_PASSWORD, FakeWifi  # noqa: E402
from pengyos_install_tui.session import WIFI, WIFI_PASSWORD as WIFI_PW_PAGE  # noqa: E402


def wifi_session(manifest, **kw):
    w = FakeWifi(**kw)
    return Session(manifest, DISKS, fetch_keys=FakeFetch(), wifi=w), w


def keys(s):
    return [p.key for p in s.pages()]


def test_no_wifi_pages_without_a_wifi_card(manifest):
    s, _ = wifi_session(manifest, interfaces=())
    assert "wifi" not in keys(s)
    assert dict(s.review())["Network"] == "wired"
    assert "wifi" not in keys(make(manifest))  # no backend at all


def test_wifi_pages_come_before_ssh(manifest):
    s, w = wifi_session(manifest, wired="10.0.0.9")
    assert keys(s) == ["disk", "name", "account", "wifi", "ssh", "pengy", "review"]
    assert s.scan() is None and w.scans == 1
    opts = s.options(WIFI)
    assert [o.label for o in opts] == ["No Wi-Fi", "Home", "Cafe", "Scan again"]
    assert opts[0].note == "wired: 10.0.0.9" and opts[1].note == "####  secured"
    assert s.is_rescan(WIFI, 3) and not s.is_rescan(WIFI, 2)


def test_a_secured_network_asks_for_its_password(manifest):
    s, w = wifi_session(manifest)
    s.scan()
    assert s.choose(WIFI, 1) is None and not s.needs_network(WIFI)
    assert keys(s)[4] == "wifi_password"
    assert s.finish(WIFI) is None and w.joined == []
    assert s.field_text(WIFI_PW_PAGE.fields[0]) == ("Password for Home", "")
    assert s.set("wifi_password", "short") == "Wi-Fi passwords are 8 to 63 characters"
    s.set("wifi_password", "wrong password")
    assert s.needs_network(WIFI_PW_PAGE)
    assert s.finish(WIFI_PW_PAGE) == ("wifi_password", "Home refused the password")
    s.set("wifi_password", WIFI_PASSWORD)
    assert s.finish(WIFI_PW_PAGE) is None and not s.needs_network(WIFI_PW_PAGE)
    assert dict(s.review())["Network"] == "Wi-Fi Home (wlp2s0, 192.168.1.77)"
    assert s.finish(WIFI_PW_PAGE) is None and len(w.joined) == 2  # not joined twice


def test_an_open_network_is_joined_from_the_list(manifest):
    s, w = wifi_session(manifest)
    s.scan()
    s.choose(WIFI, 2)
    assert "wifi_password" not in keys(s) and s.needs_network(WIFI)
    assert s.finish(WIFI) is None and w.joined == [("wlp2s0", "Cafe", "")]
    assert s.chosen(WIFI) == 2


def test_changing_your_mind_leaves_the_network(manifest):
    s, w = wifi_session(manifest)
    s.scan()
    s.choose(WIFI, 2)
    s.finish(WIFI)
    s.choose(WIFI, 0)
    assert w.forgotten == ["wlp2s0"] and "wifi_password" not in keys(s)
    assert dict(s.review())["Network"].startswith("none yet")


def test_scan_problems(manifest):
    s, _ = wifi_session(manifest, scan_error="Wi-Fi is switched off by the laptop's Wi-Fi switch")
    assert "Wi-Fi switch" in s.scan()
    assert [o.label for o in s.options(WIFI)] == ["No Wi-Fi", "Scan again"]
    s, _ = wifi_session(manifest, networks=[])
    assert "no Wi-Fi networks found" in s.scan()
