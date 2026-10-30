"""The full-screen installer's behaviour (pengyos_install_tui.screen.App), driven
by key names and read back from rendered frames — no terminal needed."""

from __future__ import annotations

import pytest
from conftest import DISKS, GH_KEYS, HOST_KEYS, HOST_PASSWORD, FakeFetch

from pengyos_install.image import Disk, ImageInstallError
from pengyos_install_tui.keys import KeyFetchError
from pengyos_install_tui.screen import App, _Editor
from pengyos_install_tui.session import Session


class FakeInstall:
    def __init__(self, error: Exception | None = None):
        self.calls, self.error = [], error

    def __call__(self, target, answers, **kw):
        self.calls.append((target, answers, kw))
        kw["report"]("write", 0.5, "writing the system")
        if self.error:
            raise self.error
        kw["report"]("done", 1.0, "done")


def make_app(manifest, *, disks=DISKS, fetcher=None, install=None, reboot=False):
    s = Session(manifest, disks, fetch_keys=fetcher or FakeFetch())
    return App(s, install=install or FakeInstall(), payload="/p", reboot=reboot)


def press(app, *keys):
    for k in keys:
        app.key(k)
        while app.pending is not None:  # what the driver does between frames
            job, app.pending = app.pending, None
            job()


def type_(app, text):
    press(app, *text)


def screen(app, h=24, w=80):
    return "\n".join(app.render(h, w).text())


def fill_to_review(app, *, source="patw", password=""):
    press(app, "enter")                         # disk (preselected)
    press(app, "enter")                         # name: keep "pengyos"
    type_(app, "pat"); press(app, "enter")
    type_(app, "hunter22"); press(app, "enter")
    type_(app, "hunter22"); press(app, "enter")
    type_(app, source)
    press(app, "tab")
    type_(app, password)
    press(app, "tab", "enter")                  # ssh
    if not source:
        press(app, "enter")                     # acknowledge the skip warning
    press(app, "enter", "enter", "enter")       # pengy: all empty


def test_editor_keys():
    ed = _Editor("abc")
    for k in ("left", "left", "X", "home", "Y", "end", "backspace", "delete"):
        assert ed.key(k)
    assert (ed.text, ed.pos) == ("YaXb", 4)
    assert not ed.key("f5")
    ed.key("ctrl-u")
    assert (ed.text, ed.pos) == ("", 0)


def test_editor_scrolls_and_masks():
    ed = _Editor("0123456789")
    assert ed.view(5, False) == ("6789", 4)
    assert ed.view(20, True) == ("*" * 10, 10)


def test_first_page_lists_disks_and_highlights_the_usable_one(manifest):
    app = make_app(manifest)
    text = screen(app)
    assert "PengyOS Server 26.10.50 installer" in text and "Step 1 of 6" in text
    assert "Choose a disk" in text
    assert "(the installer stick)" in text and "too small" in text
    frame = app.render(24, 80)
    selected = [t for _, _, t, st in frame.draws if st == "select"]
    assert len(selected) == 1 and "/dev/vda" in selected[0]


def test_arrows_skip_unusable_disks(manifest):
    two = DISKS + [Disk("nvme0n1", "/dev/nvme0n1", 500_000_000_000, "WD")]
    app = make_app(manifest, disks=two)
    assert app.row == 1
    press(app, "down")
    assert app.row == 3
    press(app, "down")
    assert app.row == 1  # wraps over the stick and the tiny disk


def test_number_keys_count_usable_disks_only(manifest):
    two = DISKS + [Disk("nvme0n1", "/dev/nvme0n1", 500_000_000_000, "WD")]
    app = make_app(manifest, disks=two)
    text = screen(app)
    assert "1. /dev/vda" in text and "2. /dev/nvme0n1" in text
    assert "   /dev/sda" in text and "3." not in text
    press(app, "2")
    assert app.row == 3
    press(app, "enter")
    assert app.s.disk.path == "/dev/nvme0n1"


def test_a_long_model_name_is_cut_before_the_note(manifest):
    disks = [Disk("sda", "/dev/sda", 8 * 10**9, "X" * 80, is_boot=True), DISKS[1]]
    assert "(the installer stick)" in screen(make_app(manifest, disks=disks))


def test_full_install(manifest, fetcher):
    inst = FakeInstall()
    app = make_app(manifest, fetcher=fetcher, install=inst)
    fill_to_review(app)
    text = screen(app)
    assert "Ready to install" in text
    assert "2 keys (2 from github.com/patw)" in text
    assert "EVERYTHING on /dev/vda will be erased." in text
    type_(app, "nope"); press(app, "enter")
    assert "type 'erase'" in screen(app) and inst.calls == []
    press(app, "ctrl-u"); type_(app, "erase"); press(app, "enter")
    ((target, answers, kw),) = inst.calls
    assert target == "/dev/vda" and kw["confirm"] is True and kw["payload_dir"] == "/p"
    assert (answers.hostname, answers.username, answers.password) == ("pengyos", "pat", "hunter22")
    assert answers.authorized_keys == GH_KEYS
    assert app.mode == "done"
    text = screen(app)
    assert "PengyOS is installed" in text and "ssh pat@pengyos" in text
    assert "Press Enter to finish" in text
    press(app, "enter")
    assert app.result == 0


def test_reboot_wording(manifest):
    app = make_app(manifest, reboot=True)
    fill_to_review(app)
    type_(app, "erase"); press(app, "enter")
    assert "Remove the USB stick, then press Enter to restart." in screen(app)


def test_progress_page_while_installing(manifest):
    seen = []
    app = make_app(manifest)
    app.redraw = lambda: seen.append(screen(app))
    fill_to_review(app)
    type_(app, "erase"); press(app, "enter")
    assert any("Installing on /dev/vda" in s and "writing the system" in s for s in seen)
    assert any(" 48%" in s for s in seen)  # write phase, half way: 0.12 + 0.73 / 2


def test_passwords_are_masked(manifest):
    app = make_app(manifest)
    press(app, "enter", "enter")
    type_(app, "pat"); press(app, "enter")
    type_(app, "hunter22")
    text = screen(app)
    assert "hunter22" not in text and "********" in text


def test_invalid_field_keeps_the_cursor_there(manifest):
    app = make_app(manifest)
    press(app, "enter", "ctrl-u")
    type_(app, "bad host"); press(app, "enter")
    assert app.page.key == "name" and "invalid hostname" in screen(app)
    press(app, "backspace")  # editing clears the message
    assert "invalid hostname" not in screen(app)


def test_password_mismatch_is_shown_on_the_repeat_field(manifest):
    app = make_app(manifest)
    press(app, "enter", "enter")
    type_(app, "pat"); press(app, "enter")
    type_(app, "hunter22"); press(app, "enter")
    type_(app, "hunter23"); press(app, "enter")
    assert app.page.key == "account" and app.focus == 2
    assert "do not match" in screen(app)


def test_skipping_ssh_needs_a_second_enter(manifest):
    app = make_app(manifest)
    press(app, "enter", "enter")
    type_(app, "pat"); press(app, "enter")
    type_(app, "hunter22"); press(app, "enter")
    type_(app, "hunter22"); press(app, "enter")
    press(app, "tab", "tab", "enter")
    assert app.page.key == "ssh" and "own keyboard" in screen(app)
    press(app, "enter", "enter")
    assert app.page.key == "pengy"


def test_github_fetch_shows_a_notice_then_the_error(manifest):
    gh = FakeFetch(error=KeyFetchError("could not reach github.com (down)"))
    app = make_app(manifest, fetcher=gh)
    press(app, "enter", "enter")
    type_(app, "pat"); press(app, "enter")
    type_(app, "hunter22"); press(app, "enter")
    type_(app, "hunter22"); press(app, "enter")
    type_(app, "patw"); press(app, "tab", "tab")
    app.key("enter")  # not running the job yet: the driver draws this frame first
    assert "Fetching keys from github.com/patw" in screen(app)
    job, app.pending = app.pending, None
    job()
    assert app.page.key == "ssh" and "could not reach github.com" in screen(app)
    assert app.focus == 0


def test_esc_goes_back_and_keeps_answers(manifest):
    app = make_app(manifest)
    press(app, "enter", "ctrl-u")
    type_(app, "box"); press(app, "enter")
    assert app.page.key == "account"
    press(app, "esc")
    assert app.page.key == "name" and "box" in screen(app)
    press(app, "esc")
    assert app.page.key == "disk"


def test_esc_on_the_first_page_asks_to_quit(manifest):
    app = make_app(manifest)
    press(app, "esc")
    assert app.mode == "quit?" and "Nothing has been changed" in screen(app)
    press(app, "n")
    assert app.mode == "page" and app.result is None
    press(app, "ctrl-c", "y")
    assert app.result == 1


def test_failure_returns_to_the_summary(manifest):
    inst = FakeInstall(error=ImageInstallError("sgdisk failed: no such device"))
    app = make_app(manifest, install=inst)
    fill_to_review(app)
    type_(app, "erase"); press(app, "enter")
    assert app.mode == "failed" and "sgdisk failed" in screen(app)
    press(app, "ctrl-c")
    assert "partly written" in screen(app)
    press(app, "n", "enter")
    assert app.mode == "page" and app.page.key == "review"
    assert app.editor("confirm").text == ""  # must type erase again


def test_unexpected_error_is_shown_not_raised(manifest):
    app = make_app(manifest, install=FakeInstall(error=RuntimeError("boom")))
    fill_to_review(app)
    type_(app, "erase"); press(app, "enter")
    assert "RuntimeError: boom" in screen(app)


def test_keys_are_ignored_while_installing(manifest):
    app = make_app(manifest)
    app.mode = "installing"
    press(app, "ctrl-c", "enter")
    assert app.mode == "installing" and app.result is None


def test_no_disk(manifest):
    app = make_app(manifest, disks=DISKS[:1])
    assert app.mode == "nodisk" and "No disk to install to" in screen(app)
    press(app, "enter")
    assert app.result == 1


def test_desktop_login_page(desktop_manifest):
    app = make_app(desktop_manifest)
    fill_to_review(app)
    assert app.page.key == "login"
    press(app, "down", "enter")
    assert app.page.key == "review" and "automatic" in screen(app)


@pytest.mark.parametrize("h,w", [(24, 80), (40, 160), (67, 240)])
@pytest.mark.parametrize("edition", ["manifest", "desktop_manifest"])
def test_every_screen_fits(request, edition, h, w):
    app = make_app(request.getfixturevalue(edition))
    pages = set()
    orig = app.key

    def checked(k):
        orig(k)
        frame = app.render(h, w)
        pages.add((app.mode, app.page.key))
        for r, c, t, _ in frame.draws:
            assert r < h and c + len(t) <= w, (app.mode, app.page.key, r, c, t)

    app.key = checked
    fill_to_review(app)
    if app.page.key == "login":
        press(app, "enter")
    press(app, "ctrl-c", "n")
    type_(app, "erase"); press(app, "enter")
    checked("resize")  # the done page, drawn after the install job returned
    assert ("done", "review") in pages and len(pages) >= 6


def test_too_small_screen_says_so(manifest):
    assert "at least 80x24" in screen(make_app(manifest), 20, 64)


def test_keys_from_another_machine(manifest, fetcher):
    app = make_app(manifest, fetcher=fetcher)
    press(app, "enter", "enter")
    type_(app, "pat"); press(app, "enter")
    type_(app, "hunter22"); press(app, "enter")
    type_(app, "hunter22"); press(app, "enter")
    type_(app, "pat@behoserver"); press(app, "tab")
    assert "Password for pat@behoserver" in screen(app)
    press(app, "tab", "enter")                    # no password yet
    assert app.focus == 1 and "enter the password for pat@behoserver" in screen(app)
    type_(app, "wrong"); press(app, "tab")
    app.key("enter")
    assert "Connecting to pat@behoserver" in screen(app)
    job, app.pending = app.pending, None
    job()
    assert app.focus == 1 and "refused the password" in screen(app)
    assert "wrong" not in screen(app)             # masked
    press(app, *["backspace"] * 5)
    type_(app, HOST_PASSWORD); press(app, "tab", "enter")
    assert app.page.key == "pengy"
    assert fetcher.calls == [("pat@behoserver", "wrong"), ("pat@behoserver", HOST_PASSWORD)]
    press(app, "enter", "enter", "enter")
    assert "2 keys (2 from pat@behoserver, 1 line skipped)" in screen(app)
    assert app.s.answers().authorized_keys == HOST_KEYS


# ------------------------------------------------------------------- Wi-Fi
from conftest import WIFI_PASSWORD, FakeWifi  # noqa: E402


def wifi_app(manifest, **kw):
    w = FakeWifi(**kw)
    s = Session(manifest, DISKS, fetch_keys=FakeFetch(), wifi=w)
    return App(s, install=FakeInstall(), payload="/p"), w


def to_wifi(app):
    press(app, "enter", "enter")
    type_(app, "pat"); press(app, "enter")
    type_(app, "hunter22"); press(app, "enter")
    type_(app, "hunter22")
    app.key("enter")  # the scan is a job: the driver draws "Looking ..." first


def test_wifi_scan_join_and_wrong_password(manifest):
    app, w = wifi_app(manifest, wired="")
    to_wifi(app)
    assert app.page.key == "wifi" and "Looking for Wi-Fi networks" in screen(app)
    job, app.pending = app.pending, None
    job()
    text = screen(app)
    assert "1. No Wi-Fi" in text and "no network cable connected" in text
    assert "2. Home" in text and "secured" in text and "4. Scan again" in text
    press(app, "down", "enter")                     # Home
    assert app.page.key == "wifi_password" and "Password for Home" in screen(app)
    type_(app, "wrong password")
    app.key("enter")
    assert "Connecting to Home" in screen(app)
    job, app.pending = app.pending, None
    job()
    assert app.page.key == "wifi_password" and "refused the password" in screen(app)
    press(app, "ctrl-u"); type_(app, WIFI_PASSWORD); press(app, "enter")
    assert app.page.key == "ssh"
    press(app, "esc", "esc")                        # back to the list: Home is kept
    assert app.page.key == "wifi" and app.row == 1


def test_wifi_rescan_and_skip(manifest):
    app, w = wifi_app(manifest, wired="10.0.0.9")
    to_wifi(app)
    press(app)  # nothing typed; run the pending scan
    job, app.pending = app.pending, None
    job()
    press(app, "up", "enter")                       # "Scan again" (wraps from row 0)
    assert w.scans == 2 and app.page.key == "wifi"
    press(app, "1", "enter")                        # No Wi-Fi
    assert app.page.key == "ssh" and w.joined == []
    press(app, "tab", "tab", "enter", "enter", "enter", "enter", "enter")
    assert "wired (10.0.0.9)" in screen(app)


def test_wifi_screens_fit(manifest):
    app, w = wifi_app(manifest)
    to_wifi(app)
    press(app)
    for _ in range(2):
        for r, c, t, _s in app.render(24, 80).draws:
            assert r < 24 and c + len(t) <= 80
        press(app, "down", "enter")
