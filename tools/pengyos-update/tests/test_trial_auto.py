"""Trial boots that must end, failed versions remembered, unattended updates (spec 4-22)."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest

from pengyos_update import auto as autopol
from pengyos_update import cli
from pengyos_update.release import FeedEntry, UpdateError
from pengyos_update.trial import (
    health_checks,
    mark_failed,
    record_trial,
    settle_trial,
    wait_healthy,
)

NOW = dt.datetime(2026, 11, 10, 4, 0, tzinfo=dt.timezone.utc)


def entry(version, *channels, image="pengyos-server"):
    return FeedEntry(image, version, channels or ("stable",), f"{image}/{version}/manifest.json")


def read(path):
    return json.loads(Path(path).read_text())


# --- health checks -------------------------------------------------------------

def _exe(path, body="exit 0"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def test_health_checks_merge_by_name_and_etc_wins(tmp_path):
    usr, etc = tmp_path / "usr", tmp_path / "etc"
    _exe(usr / "10-critical-units")
    _exe(usr / "20-image-only")
    _exe(usr / "30-switched-off")
    mine = _exe(etc / "20-image-only", "exit 1")
    _exe(etc / "50-router")
    (etc / "30-switched-off").symlink_to("/dev/null")
    (etc / "README").write_text("docs")
    (etc / "40-not-executable").write_text("#!/bin/sh\n")
    got = health_checks((str(usr), str(etc)))
    assert [p.name for p in got] == ["10-critical-units", "20-image-only", "50-router"]
    assert got[1] == mine
    assert health_checks((str(tmp_path / "missing"),)) == []


def test_a_failing_check_reports_its_last_line(tmp_path):
    from pengyos_update.trial import run_check
    ok = _exe(tmp_path / "ok")
    bad = _exe(tmp_path / "bad", "echo checking; echo 'nftables.service is enabled but failed'; exit 1")
    slow = _exe(tmp_path / "slow", "sleep 5")
    assert run_check(ok) is None
    assert run_check(bad) == "bad: nftables.service is enabled but failed"
    assert "no answer" in run_check(slow, timeout=0.2)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_wait_healthy_retries_until_it_passes():
    clock, calls = _Clock(), []

    def runner(check):
        calls.append(clock.t)
        return None if clock.t >= 30 else "wan0 has no address yet"
    assert wait_healthy([Path("x")], total=180, interval=10, runner=runner,
                        clock=clock, sleep=clock.sleep, say=lambda m: None) == []
    assert calls == [0, 10, 20, 30]


def test_wait_healthy_gives_up_after_the_timeout():
    clock = _Clock()
    out = wait_healthy([Path("x"), Path("y")], total=25, interval=10,
                       runner=lambda c: f"{c.name} failed", clock=clock, sleep=clock.sleep,
                       say=lambda m: None)
    assert out == ["x failed", "y failed"] and clock.t == 25


def test_every_check_runs_once_even_with_no_time():
    clock, ran = _Clock(), []
    wait_healthy([Path("x")], total=0, runner=lambda c: ran.append(c) or "no",
                 clock=clock, sleep=clock.sleep, say=lambda m: None)
    assert len(ran) == 1


# --- trial records and failures ---------------------------------------------------

def test_a_trial_that_never_confirmed_is_remembered_as_failed(tmp_path):
    state = str(tmp_path / "state.json")
    record_trial(state, "26.11.4", "26.11.3", "b")
    # Still waiting for its boot: GRUB has not consumed pengyos_try.
    assert settle_trial(state, booted="a", committed="a", pending_try="b") is None
    assert read(state)["trial"]["version"] == "26.11.4"
    # Booted (pengyos_try consumed) and we are back on a: it failed.
    assert settle_trial(state, booted="a", committed="a", pending_try="") == "26.11.4"
    s = read(state)
    assert s["trial"] is None and "did not finish starting" in s["failed"]["26.11.4"]["reason"]
    # Idempotent: the next boot finds nothing to settle.
    assert settle_trial(state, booted="a", committed="a", pending_try="") is None


def test_a_kept_trial_is_just_dropped(tmp_path):
    state = str(tmp_path / "state.json")
    record_trial(state, "26.11.4", "26.11.3", "b")
    assert settle_trial(state, booted="b", committed="b", pending_try="") is None
    s = read(state)
    assert s["trial"] is None and not s.get("failed")


def test_mark_failed_keeps_other_failures(tmp_path):
    state = str(tmp_path / "state.json")
    mark_failed(state, "26.11.1", "x")
    mark_failed(state, "26.11.4", "y")
    assert set(read(state)["failed"]) == {"26.11.1", "26.11.4"}


def test_a_failed_trial_is_no_longer_called_staged(tmp_path):
    # Found in QEMU: after a failed trial the login message still said
    # "26.11.60 is installed — restart to start it", but a restart boots the old slot.
    state = str(tmp_path / "state.json")
    cli.save_state(state, staged="26.11.4", current="26.11.3")
    record_trial(state, "26.11.4", "26.11.3", "b")
    assert settle_trial(state, booted="a", committed="a", pending_try="") == "26.11.4"
    s = read(state)
    assert s["staged"] is None
    msg = cli.login_message(s, "26.11.3")
    assert "did not start" in msg and "restart to start it" not in msg
    # A different staged version is not touched.
    cli.save_state(state, staged="26.11.5")
    mark_failed(state, "26.11.4", "again")
    assert read(state)["staged"] == "26.11.5"


# --- confirm --boot --------------------------------------------------------------

class _Layout:
    def __init__(self, booted):
        self.booted = booted
        self.other = "b" if booted == "a" else "a"
        self.slots = {"a": "/dev/sda2", "b": "/dev/sda3"}
        self.esp = "/dev/sda1"
        self.partuuids = {"a": "aaa", "b": "bbb"}


class _Esp:
    sets: list = []

    def __init__(self, dev):
        self.boot_dir = Path("/nonexistent")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def set_env(self, **kv):
        _Esp.sets.append(kv)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A fake A/B machine for confirm: booted slot, grubenv, health checks."""
    m = {"booted": "a", "env": {"pengyos_slot": "a"}, "version": "26.11.4",
         "rebooted": [], "health": tmp_path / "health"}
    _Esp.sets = []
    monkeypatch.setattr(cli, "require_root", lambda: None)
    monkeypatch.setattr(cli, "lock", lambda: None)
    monkeypatch.setattr(cli, "detect_layout", lambda: _Layout(m["booted"]))
    monkeypatch.setattr(cli, "read_env", lambda esp: dict(m["env"]))
    monkeypatch.setattr(cli, "Esp", _Esp)
    monkeypatch.setattr(cli, "write_boot_menu", lambda *a, **k: False)
    monkeypatch.setattr(cli, "record_slots", lambda *a, **k: None)
    monkeypatch.setattr(cli, "identity", lambda: ("pengyos-server", m["version"], {}))
    monkeypatch.setattr(cli, "read_config", lambda *a: {"feed": "f", "channel": "stable",
                                                         "health_timeout": "0"})
    monkeypatch.setattr(cli, "write_confirmed", lambda: m.setdefault("confirmed", True))
    m["state"], m["motd"] = str(tmp_path / "state.json"), str(tmp_path / "motd")
    m["health"].mkdir()

    def confirm(*extra):
        return cli.main(["confirm", *extra, "--state", m["state"], "--motd", m["motd"],
                         "--health-dir", str(m["health"]),
                         f"--reboot-cmd=sh -c 'echo rebooted >> {tmp_path / 'reboots'}'"])
    m["confirm"] = confirm
    m["reboots"] = tmp_path / "reboots"
    return m


def test_a_healthy_trial_is_kept(machine):
    machine.update(booted="b", env={"pengyos_slot": "a", "pengyos_try": ""})
    record_trial(machine["state"], "26.11.4", "26.11.3", "b")
    _exe(machine["health"] / "10-ok")
    assert machine["confirm"]("--boot") == 0
    assert _Esp.sets == [{"pengyos_slot": "b", "pengyos_try": ""}]
    assert read(machine["state"])["trial"] is None
    assert machine.get("confirmed") and not machine["reboots"].exists()


def test_an_unhealthy_trial_is_failed_and_restarted_into_the_old_slot(machine):
    machine.update(booted="b", env={"pengyos_slot": "a", "pengyos_try": ""})
    record_trial(machine["state"], "26.11.4", "26.11.3", "b")
    _exe(machine["health"] / "10-critical-units", "echo 'nftables.service is enabled but failed'; exit 1")
    assert machine["confirm"]("--boot") == 1
    assert _Esp.sets == [], "the old slot stays the default"
    s = read(machine["state"])
    assert "nftables.service is enabled but failed" in s["failed"]["26.11.4"]["reason"]
    assert s["trial"] is None
    assert machine["reboots"].read_text() == "rebooted\n"
    assert not machine.get("confirmed")


def test_a_committed_system_is_never_health_checked_or_restarted(machine):
    _exe(machine["health"] / "10-always-fails", "exit 1")
    assert machine["confirm"]("--boot") == 0
    assert not machine["reboots"].exists() and _Esp.sets == []
    assert machine.get("confirmed")


def test_confirm_by_hand_keeps_a_trial_without_checks(machine):
    machine.update(booted="b", env={"pengyos_slot": "a", "pengyos_try": ""})
    mark_failed(machine["state"], "26.11.4", "earlier")
    _exe(machine["health"] / "10-always-fails", "exit 1")
    assert machine["confirm"]() == 0
    assert _Esp.sets == [{"pengyos_slot": "b", "pengyos_try": ""}]
    assert not read(machine["state"])["failed"], "kept by hand: no longer failed"


def test_the_old_slot_records_a_trial_that_never_came_back(machine):
    machine.update(version="26.11.3")
    record_trial(machine["state"], "26.11.4", "26.11.3", "b")
    assert machine["confirm"]("--boot") == 0
    s = read(machine["state"])
    assert "26.11.4" in s["failed"] and s["trial"] is None
    motd = Path(machine["motd"]).read_text()
    assert "26.11.4 did not start on this machine" in motd and "still running 26.11.3" in motd


# --- apply refuses a failed version ---------------------------------------------

def test_apply_refuses_a_version_that_failed_here(tmp_path, monkeypatch):
    state = str(tmp_path / "state.json")
    mark_failed(state, "26.11.4", "health check failed: 50-router: wan0 has no address")
    monkeypatch.setattr(cli, "require_root", lambda: None)
    monkeypatch.setattr(cli, "lock", lambda: None)
    monkeypatch.setattr(cli, "identity", lambda: ("pengyos-server", "26.11.3", {}))
    monkeypatch.setattr(cli, "read_config", lambda *a: {"feed": "f", "channel": "stable"})
    monkeypatch.setattr(cli, "detect_layout", lambda: _Layout("a"))
    monkeypatch.setattr(cli, "read_env", lambda esp: {"pengyos_slot": "a"})
    monkeypatch.setattr(cli, "find_update", lambda *a: entry("26.11.4"))
    args = cli.build_parser().parse_args(["apply", "--state", state])
    with pytest.raises(UpdateError, match=r"did not start on this machine.*--version 26\.11\.4"):
        cli._apply(args, cli.Progress(None))


# --- unattended: policy ------------------------------------------------------------

def test_parse_at_and_soak():
    assert autopol.parse_at("4:05") == "04:05" and autopol.parse_at("23:59") == "23:59"
    for bad in ("24:00", "4", "04:60", "noon"):
        with pytest.raises(UpdateError):
            autopol.parse_at(bad)
    assert autopol.parse_soak("0") == 0
    with pytest.raises(UpdateError):
        autopol.parse_soak("-1")
    assert autopol.settings({}) == {"enabled": False, "at": "04:00", "soak_days": 3}
    assert autopol.settings({"auto": "on", "auto_at": "bad", "auto_soak_days": "x"})["enabled"]


def test_seen_is_stamped_once_and_pruned():
    feed = [entry("26.11.3"), entry("26.11.4"), entry("26.11.5", "testing"),
            entry("26.11.9", image="pengyos-desktop")]
    old = {"26.11.3": "2026-11-01T00:00:00Z", "26.10.9": "2026-10-01T00:00:00Z"}
    seen = autopol.record_seen(old, feed, "pengyos-server", "stable", NOW)
    assert seen == {"26.11.3": "2026-11-01T00:00:00Z", "26.11.4": "2026-11-10T04:00:00Z"}
    assert "26.11.5" in autopol.record_seen({}, feed, "pengyos-server", "testing", NOW)


def test_pick_waits_for_the_soak_and_takes_the_newest_soaked():
    feed = [entry("26.11.3"), entry("26.11.4"), entry("26.11.5")]
    seen = {"26.11.4": "2026-11-06T00:00:00Z", "26.11.5": "2026-11-09T00:00:00Z"}
    got, waiting, until = autopol.pick(feed, "pengyos-server", "stable", "26.11.3", {}, seen, 3, NOW)
    assert got.version == "26.11.4" and waiting.version == "26.11.5"
    assert until == "2026-11-12T00:00:00Z"
    got, waiting, until = autopol.pick(feed, "pengyos-server", "stable", "26.11.4", {}, seen, 3, NOW)
    assert got is None and waiting.version == "26.11.5"
    got, _, _ = autopol.pick(feed, "pengyos-server", "stable", "26.11.4", {}, seen, 0, NOW)
    assert got.version == "26.11.5"
    # Never seen before: the soak starts now.
    got, waiting, _ = autopol.pick(feed, "pengyos-server", "stable", "26.11.4", {}, {}, 3, NOW)
    assert got is None and waiting.version == "26.11.5"


def test_pick_skips_failed_versions_but_not_newer_ones():
    feed = [entry("26.11.4"), entry("26.11.5")]
    seen = {"26.11.4": "2026-11-01T00:00:00Z", "26.11.5": "2026-11-01T00:00:00Z"}
    state = {"failed": {"26.11.5": {"at": "x", "reason": "y"}}}
    got, _, _ = autopol.pick(feed, "pengyos-server", "stable", "26.11.3", state, seen, 3, NOW)
    assert got.version == "26.11.4"
    got, waiting, _ = autopol.pick(feed, "pengyos-server", "stable", "26.11.4", state, seen, 3, NOW)
    assert got is None and waiting is None


def test_pick_ignores_other_channels_and_images():
    feed = [entry("26.11.5", "testing"), entry("26.11.6", image="pengyos-server-nvidia")]
    seen = {"26.11.5": "2026-01-01T00:00:00Z", "26.11.6": "2026-01-01T00:00:00Z"}
    assert autopol.pick(feed, "pengyos-server", "stable", "26.11.3", {}, seen, 0, NOW)[0] is None
    assert autopol.pick(feed, "pengyos-server", "testing", "26.11.3", {}, seen, 0, NOW)[0].version == "26.11.5"


# --- unattended: auto on/off ------------------------------------------------------

def test_auto_on_and_off_write_the_config_and_the_timer(tmp_path, monkeypatch, capsys):
    conf, dropin = tmp_path / "update.conf", tmp_path / "timer.d" / "50-pengyos-time.conf"
    conf.write_text("# mine\nchannel = stable\n")
    calls = []
    monkeypatch.setattr(cli, "require_root", lambda: None)
    monkeypatch.setattr(cli, "systemctl", lambda *a, **k: calls.append(a))
    common = ["--config", str(conf), "--dropin", str(dropin), "--state", str(tmp_path / "s.json")]
    assert cli.main(["auto", "on", "--at", "3:30", "--soak", "5", *common]) == 0
    text = conf.read_text()
    assert "# mine" in text and "auto = on" in text
    assert "auto_at = 03:30" in text and "auto_soak_days = 5" in text
    assert dropin.read_text().endswith("[Timer]\nOnCalendar=\nOnCalendar=*-*-* 03:30:00\n")
    assert calls == [("daemon-reload",), ("enable", "--now", "pengyos-update-auto.timer")]
    assert "warning" not in capsys.readouterr().out

    calls.clear()
    assert cli.main(["auto", "off", *common]) == 0
    assert "auto = off" in conf.read_text() and not dropin.exists()
    assert ("disable", "--now", "pengyos-update-auto.timer") in calls

    assert cli.main(["auto", *common]) == 0
    assert "unattended updates: off" in capsys.readouterr().out
    assert cli.main(["auto", "on", "--at", "25:00", *common]) == 1


def test_auto_on_a_testing_machine_warns(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "update.conf"
    conf.write_text("channel = testing\n")
    monkeypatch.setattr(cli, "require_root", lambda: None)
    monkeypatch.setattr(cli, "systemctl", lambda *a, **k: None)
    assert cli.main(["auto", "on", "--config", str(conf), "--dropin", str(tmp_path / "d.conf")]) == 0
    assert "warning: this machine is on the testing channel" in capsys.readouterr().out


# --- unattended: the run -----------------------------------------------------------

FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:pengyos="https://pengyos.catbee.ca/ns/update/1">
  <entry><pengyos:image>pengyos-server</pengyos:image><pengyos:version>26.11.4</pengyos:version>
    <pengyos:channel>stable</pengyos:channel>
    <pengyos:manifest>pengyos-server/26.11.4/manifest.json</pengyos:manifest></entry>
</feed>"""


@pytest.fixture
def autorun(tmp_path, monkeypatch):
    conf = tmp_path / "update.conf"
    conf.write_text("feed = https://x/feed.xml\nchannel = stable\nauto = on\nauto_soak_days = 3\n")
    r = {"env": {"pengyos_slot": "a"}, "applied": [], "state": str(tmp_path / "state.json"),
         "reboots": tmp_path / "reboots", "conf": conf}
    monkeypatch.setattr(cli, "require_root", lambda: None)
    monkeypatch.setattr(cli, "identity", lambda: ("pengyos-server", "26.11.3", {}))
    monkeypatch.setattr(cli, "detect_layout", lambda: _Layout("a"))
    monkeypatch.setattr(cli, "read_env", lambda esp: dict(r["env"]))
    monkeypatch.setattr(cli, "fetch", lambda url: FEED.encode())
    monkeypatch.setattr(cli, "write_motd", lambda *a, **k: None)

    def fake_apply(args, progress):
        r["applied"].append(args.version)
        if r.get("apply_error"):
            raise UpdateError(r["apply_error"])
    monkeypatch.setattr(cli, "_apply", fake_apply)

    def run():
        return cli.main(["auto-run", "--config", str(conf), "--state", r["state"],
                         f"--reboot-cmd=sh -c 'echo rebooted >> {r['reboots']}'"])
    r["run"] = run
    return r


def _seen(path, version, days_ago):
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    Path(path).write_text(json.dumps({"seen": {version: autopol._iso(t)}}))


def test_auto_run_waits_out_the_soak(autorun):
    assert autorun["run"]() == 0
    s = read(autorun["state"])
    assert s["auto"]["result"] == "waiting" and "26.11.4" in s["seen"]
    assert autorun["applied"] == [] and not autorun["reboots"].exists()


def test_auto_run_installs_a_soaked_release_and_restarts(autorun):
    _seen(autorun["state"], "26.11.4", 4)
    assert autorun["run"]() == 0
    assert autorun["applied"] == ["26.11.4"]
    assert read(autorun["state"])["auto"]["result"] == "applied"
    assert autorun["reboots"].read_text() == "rebooted\n"


def test_auto_run_does_not_restart_after_a_failed_install(autorun):
    _seen(autorun["state"], "26.11.4", 4)
    autorun["apply_error"] = "downloading https://x failed"
    assert autorun["run"]() == 1
    s = read(autorun["state"])
    assert s["auto"]["result"] == "error" and "downloading" in s["auto"]["message"]
    assert not autorun["reboots"].exists()


def test_auto_run_never_reinstalls_a_failed_version(autorun):
    _seen(autorun["state"], "26.11.4", 10)
    mark_failed(autorun["state"], "26.11.4", "did not finish starting")
    assert autorun["run"]() == 0
    assert autorun["applied"] == [] and read(autorun["state"])["auto"]["result"] == "nothing-newer"


def test_auto_run_skips_a_trial_or_a_staged_update(autorun):
    _seen(autorun["state"], "26.11.4", 10)
    autorun["env"] = {"pengyos_slot": "b"}            # this boot (a) is an unconfirmed trial
    assert autorun["run"]() == 0
    autorun["env"] = {"pengyos_slot": "a", "pengyos_try": "b"}  # staged, waiting for a restart
    assert autorun["run"]() == 0
    assert autorun["applied"] == [] and read(autorun["state"])["auto"]["result"] == "skipped"


def test_auto_run_does_nothing_when_off(autorun):
    autorun["conf"].write_text("channel = stable\n")
    _seen(autorun["state"], "26.11.4", 10)
    assert autorun["run"]() == 0
    assert autorun["applied"] == [] and "auto" not in read(autorun["state"])


# --- the login message -------------------------------------------------------------

def test_login_message_puts_a_failure_before_available():
    state = {"failed": {"26.11.4": {"at": "2026-11-10T04:07:00Z", "reason": "health check failed"}},
             "available": {"version": "26.11.4"}}
    msg = cli.login_message(state, "26.11.3")
    assert "26.11.4 did not start on this machine" in msg and "is available" not in msg
    # A failure older than what runs now is history, not news.
    assert cli.login_message(state, "26.11.5") is None
    state["auto"] = {"result": "error", "at": "t", "message": "no network"}
    assert "unattended update failed (t): no network" in cli.login_message({**state, "failed": {}}, "26.11.5")


def test_status_shows_unattended_and_failures(tmp_path, monkeypatch, capsys):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"failed": {"26.11.4": {"at": "t", "reason": "r"}},
                                "auto": {"at": "t2", "result": "nothing-newer", "message": "m"}}))
    monkeypatch.setattr(cli, "identity", lambda: ("pengyos-server", "26.11.3", {}))
    monkeypatch.setattr(cli, "read_config", lambda *a: {"feed": "f", "channel": "stable", "auto": "on"})
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    assert cli.main(["status", "--state", str(path)]) == 0
    out = capsys.readouterr().out
    assert "unattended: on, daily at 04:00, soak 3 day(s)" in out
    assert "last run t2: nothing-newer — m" in out and "failed:   26.11.4 did not start here" in out
    assert cli.main(["status", "--json", "--state", str(path)]) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["auto"]["enabled"] and j["failed"]["26.11.4"]["reason"] == "r"


def test_the_shipped_health_check_and_trial_helper_parse():
    """The image's shell pieces are at least valid sh."""
    repo = Path(__file__).resolve().parents[3]
    base = repo / "buildroot-external/board/server/rootfs-overlay/usr/lib/pengyos"
    for f in (base / "update-health.d/10-critical-units", base / "update-trial-reboot"):
        assert os.access(f, os.X_OK), f
        assert os.system(f"sh -n {f}") == 0, f
