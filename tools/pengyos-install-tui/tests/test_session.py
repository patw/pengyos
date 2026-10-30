"""The shared pages and rules (pengyos_install_tui.session)."""

from __future__ import annotations

import pytest
from conftest import DISKS, GH_KEYS, KEY, FakeGitHub

from pengyos_install.image import Disk
from pengyos_install_tui.keys import KeyFetchError
from pengyos_install_tui.session import ACCOUNT, DISK, LOGIN, NAME, PENGY, SSH, Session


def make(manifest, github=None, disks=DISKS):
    return Session(manifest, disks, fetch_keys=github or FakeGitHub())


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
    ("github_user", "-nope", "not a GitHub username"),
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


def test_ssh_fetches_github_keys_once(manifest, github):
    s = make(manifest, github)
    s.set("github_user", "patw")
    assert s.needs_network(SSH)
    assert s.finish(SSH) is None
    assert github.calls == ["patw"] and s.authorized_keys == GH_KEYS
    assert not s.needs_network(SSH)
    assert s.finish(SSH) is None and github.calls == ["patw"]  # not fetched twice


def test_ssh_github_and_pasted_key_combine(manifest, github):
    s = make(manifest, github)
    s.set("github_user", "patw")
    s.set("ssh_key", KEY)
    assert s.finish(SSH) is None
    assert s.authorized_keys.splitlines() == GH_KEYS.splitlines() + [KEY]
    ssh = dict(s.review())["SSH"]
    assert ssh == "3 keys (2 from github.com/patw, 1 pasted)"


def test_ssh_fetch_failure_points_at_the_github_field(manifest):
    s = make(manifest, FakeGitHub(error=KeyFetchError("could not reach github.com")))
    s.set("github_user", "patw")
    assert s.finish(SSH) == ("github_user", "could not reach github.com")
    assert s.authorized_keys == ""


def test_ssh_clearing_the_github_user_drops_its_keys(manifest, github):
    s = make(manifest, github)
    s.set("github_user", "patw")
    s.finish(SSH)
    s.set("github_user", "")
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


def test_answers_and_review(manifest, github):
    s = make(manifest, github)
    for k, v in [("hostname", "box"), ("username", "pat"), ("password", "hunter22"),
                 ("password2", "hunter22"), ("github_user", "patw")]:
        assert s.set(k, v) is None
    s.finish(SSH)
    a = s.answers()
    a.validate()
    assert (a.hostname, a.username, a.password, a.autologin) == ("box", "pat", "hunter22", False)
    review = dict(s.review())
    assert review["Disk"] == "/dev/vda  20.0 GB  QEMU"
    assert review["Pengy"].startswith("not set up")
    assert "Login" not in review
