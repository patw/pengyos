"""The rules that separate a broadcast signal from a crash, a logout and an OOM.

Every timeline here is hand-built: the analysis is pure, so no journal is needed.
The two shapes that matter most are the ones that look identical on screen —
"everything died at once because someone signalled the group" and "everything
died at once because I logged out" — and they differ by exactly one line
(`Stopping User Manager for UID n`).
"""

from __future__ import annotations

import json

import pytest

from pengyos_postmortem import analyse, journal
from pengyos_postmortem.cli import main

T = 1_792_960_000.0          # arbitrary epoch, stable across runs


def line(ts, pid, comm, message, *, unit=None, boot="boot-a"):
    rec = {
        "__REALTIME_TIMESTAMP": str(int(ts * 1_000_000)),
        "_PID": str(pid),
        "_COMM": comm,
        "MESSAGE": message,
        "_BOOT_ID": boot,
        "PRIORITY": "6",
    }
    if unit:
        rec["_SYSTEMD_UNIT"] = unit
    return json.dumps(rec)


def entries(*specs):
    return [e for e in (journal.parse_line(line(*s)) for s in specs) if e]


# ------------------------------------------------------------------ parsing ----

def test_parse_line_reads_the_fields_the_analysis_needs():
    e = journal.parse_line(line(T, 928, "cosmic-session", "hello", unit="session-c1.scope"))
    assert e is not None
    assert e.ts == pytest.approx(T)
    assert e.pid == 928 and e.comm == "cosmic-session"
    assert e.unit == "session-c1.scope" and e.boot_id == "boot-a"
    assert e.who == "cosmic-session(928)"


def test_parse_line_decodes_a_non_utf8_message():
    # journald emits non-UTF-8 payloads as an array of byte values.
    rec = json.dumps({"__REALTIME_TIMESTAMP": str(int(T * 1e6)), "_PID": "1",
                      "_COMM": "k", "MESSAGE": list(b"bad \xff byte")})
    e = journal.parse_line(rec)
    assert e is not None and "bad" in e.message and "\ufffd" in e.message


@pytest.mark.parametrize("raw", ["", "not json", "[]", '{"_PID": "1"}'])
def test_parse_line_rejects_unusable_lines(raw):
    assert journal.parse_line(raw) is None


def test_build_argv_always_asks_for_json_and_one_boot():
    argv = journal.build_argv("-1", "2026-10-25 16:00", None)
    assert "--output=json" in argv and "--boot=-1" in argv
    assert argv[argv.index("--since") + 1] == "2026-10-25 16:00"
    assert "--until" not in argv


# ------------------------------------------------------------------- bursts ----

def test_deaths_within_the_window_are_one_burst():
    es = entries(
        (T + 0.00, 962, "ssh-agent", "exiting on signal 15"),
        (T + 0.01, 928, "cosmic-session", "EXITING: received SIGTERM request to terminate"),
        (T + 0.02, 991, "oo7-daemon", "Received SIGTERM, shutting down"),
    )
    bursts = analyse.collect_bursts(es, window=2.0)
    assert len(bursts) == 1
    assert len(bursts[0].processes) == 3
    assert bursts[0].termination_signals == 3


def test_deaths_beyond_the_window_are_separate_bursts():
    es = entries(
        (T + 0.0, 10, "a", "exiting on signal 15"),
        (T + 30.0, 11, "b", "exiting on signal 15"),
    )
    assert len(analyse.collect_bursts(es, window=2.0)) == 2


def test_a_parent_announcing_its_children_counts_each_child_once():
    es = entries(
        (T + 0.00, 928, "cosmic-session", "process 'cosmic-osd' terminated with signal 15"),
        (T + 0.01, 928, "cosmic-session", "process 'cosmic-bg' terminated with signal 15"),
        (T + 0.02, 928, "cosmic-session", "process 'cosmic-idle' terminated with signal 15"),
    )
    burst = analyse.collect_bursts(es)[0]
    # One reporter, three victims: the pid is the reporter's here, and the count is
    # what matters — a session teardown must not look like one process.
    assert len(burst.victims) == 3


def test_the_kernel_style_message_names_the_victim_pid():
    es = entries(
        (T, 1, "kernel", "Out of memory: Killed process 51410 (cosmic-osd) total-vm:1234kB"),
    )
    victim = analyse.collect_bursts(es)[0].victims[0]
    assert (victim.pid, victim.comm) == (51410, "cosmic-osd")


def test_crash_signals_are_not_termination_signals():
    es = entries(
        (T + 0.0, 100, "app", "Process 100 received SIGSEGV"),
        (T + 0.1, 101, "app2", "Process 101 received SIGSEGV"),
        (T + 0.2, 102, "app3", "Process 102 received SIGSEGV"),
    )
    burst = analyse.collect_bursts(es)[0]
    assert len(burst.processes) == 3
    assert burst.termination_signals == 0


# --- shapes found on a real boot (2026-10-25, behopc) --------------------------

def test_a_policy_line_with_SIGTERM_in_it_is_not_a_death():
    """earlyoom announces its thresholds with the word SIGTERM; nobody died."""
    es = entries(
        (T + 0.0, 642, "earlyoom", "sending SIGTERM when mem <=  4.00% and swap <= 10.00%,"),
        (T + 0.1, 642, "earlyoom",
         "Preferring to kill process names that match regex '(^|/)(firefox|chrome)$'"),
        (T + 0.2, 642, "earlyoom",
         "Will avoid killing process names that match regex '(^|/)(cosmic-comp|systemd)$'"),
    )
    assert analyse.collect_bursts(es) == []
    assert analyse.analyse(es).kind == "unknown"


def test_boot_time_hibernation_memory_is_not_a_suspend():
    es = entries((T, 1, "kernel",
                  "PM: hibernation: Registered nosave memory: [mem 0x00000000-0x00000fff]"))
    assert analyse.collect_exclusions(es)["suspend/resume"] == []


def test_a_real_suspend_is_detected():
    es = entries((T, 1, "kernel", "PM: suspend entry (deep)"))
    assert analyse.collect_exclusions(es)["suspend/resume"]


def test_systemd_coredump_names_the_victim_not_the_reporter():
    es = entries(
        (T, 51635, "systemd-coredum",
         "Process 51410 (cosmic-osd) of user 1000 terminated abnormally with signal 6/ABRT, "
         "processing..."),
    )
    victim = analyse.collect_bursts(es)[0].victims[0]
    assert (victim.pid, victim.comm) == (51410, "cosmic-osd")
    assert victim.termination is False          # ABRT is a crash, not a kill


def test_systemd_unit_exit_is_a_victim_keyed_by_unit():
    es = entries(
        (T, 991, "systemd", "cosmic-osd.service: Main process exited, code=killed, "
                             "status=11/SEGV"),
        (T + 0.1, 991, "systemd", "cosmic-bg.service: Main process exited, code=killed, "
                                   "status=15/TERM"),
    )
    burst = analyse.collect_bursts(es)[0]
    assert {c for _, c in burst.processes} == {"cosmic-osd.service", "cosmic-bg.service"}
    assert burst.termination_signals == 1       # only the TERM one


def test_a_pidless_announcement_merges_with_the_pidful_one():
    es = entries(
        (T + 0.0, 928, "cosmic-session", "process 'cosmic-osd' terminated with signal 15"),
        (T + 0.1, 51410, "cosmic-osd", "exiting on signal 15"),
    )
    processes = analyse.collect_bursts(es)[0].processes
    assert (51410, "cosmic-osd") in processes
    assert (None, "cosmic-osd") not in processes


def test_scope_stops_are_reported_as_the_silent_majority():
    es = entries(
        (T + 0.0, 962, "ssh-agent", "exiting on signal 15"),
        (T + 0.1, 928, "cosmic-session", "EXITING: received SIGTERM request to terminate"),
        (T + 0.2, 991, "oo7-daemon", "Received SIGTERM, shutting down"),
        (T + 0.3, 916, "systemd", "Stopped app-flatpak-com.valvesoftware.Steam-1.scope."),
        (T + 0.4, 916, "systemd", "Stopped app-flatpak-org.mozilla.firefox-2.scope."),
    )
    rep = analyse.analyse(es)
    assert any("2 more scopes were stopped" in d for d in rep.detail)
    # A scope stop is not itself a victim announcement.
    assert len(rep.bursts[0].processes) == 3


def test_a_signal_systemd_only_received_is_not_a_death():
    """systemd logs SIGRTMIN from plymouthd at every boot; nothing died."""
    es = entries(
        (T + 0.0, 1, "systemd", "Received SIGRTMIN+20 from PID 494 (plymouthd)."),
        (T + 0.1, 1, "systemd", "Received SIGRTMIN+21 from PID 494 (plymouthd)."),
    )
    assert analyse.collect_bursts(es) == []


def test_a_quoted_exec_line_yields_the_program_name():
    """cosmic-session quotes the whole environment; the victim is the last token."""
    es = entries((T, 928, "cosmic-session",
                  "process ' WAYLAND_DISPLAY=wayland-1 DISPLAY=:0 COSMIC_VERSION=1.9.0 "
                  "cosmic-osd ' terminated with signal 15"))
    assert analyse.collect_bursts(es)[0].victims[0].comm == "cosmic-osd"


def test_a_quoted_absolute_path_yields_the_basename():
    es = entries((T, 928, "cosmic-session",
                  "process ' COSMIC_SESSION_SOCK=12 /usr/bin/cosmic-comp ' "
                  "terminated with signal 15"))
    assert analyse.collect_bursts(es)[0].victims[0].comm == "cosmic-comp"


# ------------------------------------------------------------------ verdicts ----

def _broadcast_case(**extra):
    """Today's shape: many victims in one instant, manager never asked to stop."""
    return entries(
        (T + 0.00, 962, "ssh-agent", "exiting on signal 15"),
        (T + 0.01, 928, "cosmic-session", "EXITING: received SIGTERM request to terminate"),
        (T + 0.02, 991, "oo7-daemon", "Received SIGTERM, shutting down"),
        (T + 0.03, 934, "wireplumber", "exiting on signal 15"),
        (T + 1.20, 1, "systemd", "user@1000.service: Deactivated successfully."),
        *extra,
    )


def test_broadcast_when_many_die_at_once_and_the_manager_was_never_asked():
    rep = analyse.analyse(_broadcast_case())
    assert rep.kind == "broadcast"
    assert "one sender, many victims" in rep.headline
    assert any("no stop request" in d for d in rep.detail)
    assert [s["uid"] for s in rep.session.manager_signalled] == [1000]


def test_a_logout_is_not_a_broadcast():
    """Same pile of deaths, but systemd was *asked* to stop the manager first."""
    es = entries(
        (T + 0.00, 1, "systemd", "Stopping User Manager for UID 1000..."),
        (T + 0.02, 962, "ssh-agent", "exiting on signal 15"),
        (T + 0.03, 928, "cosmic-session", "EXITING: received SIGTERM request to terminate"),
        (T + 0.04, 991, "oo7-daemon", "Received SIGTERM, shutting down"),
        (T + 0.50, 1, "systemd", "user@1000.service: Deactivated successfully."),
    )
    rep = analyse.analyse(es)
    assert rep.kind == "logout"
    assert rep.session.manager_signalled == []


def test_a_stop_request_outside_the_grace_window_still_counts_as_signalled():
    es = entries(
        (T + 0.00, 1, "systemd", "Stopping User Manager for UID 1000..."),
        (T + 120.0, 1, "systemd", "user@1000.service: Deactivated successfully."),
    )
    rep = analyse.analyse(es)
    # 2 minutes later is not the same stop; the manager got a signal of its own.
    assert [s["uid"] for s in rep.session.manager_signalled] == [1000]


def test_oom_is_reported_with_the_kernel_line():
    es = entries(
        (T + 0.0, 1, "kernel", "Out of memory: Killed process 4242 (firefox) total-vm:9kB"),
        (T + 0.1, 642, "earlyoom", "sending SIGTERM to process 4242 uid 1000"),
    )
    rep = analyse.analyse(es)
    assert rep.kind == "out-of-memory"
    assert "firefox" in rep.detail[0]


def test_one_app_dying_alone_is_single_process():
    es = entries((T, 4242, "firefox", "exiting on signal 15"))
    rep = analyse.analyse(es)
    assert rep.kind == "single-process"


def test_crash_burst_is_not_called_a_broadcast():
    es = entries(
        (T + 0.0, 100, "a", "received SIGSEGV"),
        (T + 0.1, 101, "b", "received SIGSEGV"),
        (T + 0.2, 102, "c", "received SIGSEGV"),
    )
    assert analyse.analyse(es).kind == "single-process"


def test_broadcast_without_a_user_session_still_reports_the_burst():
    es = entries(
        (T + 0.0, 10, "a", "exiting on signal 15"),
        (T + 0.1, 11, "b", "exiting on signal 15"),
        (T + 0.2, 12, "c", "exiting on signal 15"),
    )
    rep = analyse.analyse(es)
    assert rep.kind == "broadcast"
    assert any("no session record" in d for d in rep.detail)


def test_nothing_happened_is_unknown_not_a_guess():
    rep = analyse.analyse(entries((T, 1, "systemd", "Reached target Multi-User System.")))
    assert rep.kind == "unknown"


def test_one_boot_id_is_reported_as_no_reboot():
    rep = analyse.analyse(_broadcast_case())
    assert rep.session.boots == ["boot-a"]


def test_exclusions_are_listed_even_when_empty():
    rep = analyse.analyse(_broadcast_case())
    assert set(rep.exclusions) >= {"out of memory", "kernel fault", "gpu fault"}
    assert all(v == [] for v in rep.exclusions.values())


def test_exclusion_rules_fire_on_real_shapes():
    es = entries(
        (T + 0, 1, "kernel", "kernel BUG at mm/slub.c:424!"),
        (T + 1, 1, "kernel", "NVRM: Xid (PCI:0000:01:00): 79, GPU has fallen off the bus"),
        (T + 2, 1, "kernel", "PM: suspend entry (deep)"),
    )
    found = analyse.collect_exclusions(es)
    assert found["kernel fault"] and found["gpu fault"] and found["suspend/resume"]


# -------------------------------------------------- the streaming filter ----

# One real journal line per rule. A journal can be gigabytes, so the stream is
# filtered as it is read; if a rule grows a pattern this list does not cover, the
# test fails, and a clean "no evidence" can never come from a dropped line.
RULE_LINES = [
    "exiting on signal 15",
    "EXITING: received SIGTERM request to terminate",
    "Received SIGTERM, shutting down",
    "process ' /usr/bin/cosmic-comp ' terminated with signal 15",
    "Out of memory: Killed process 51410 (cosmic-osd) total-vm:1234kB",
    "Process 51410 (cosmic-osd) of user 1000 terminated abnormally with signal 6/ABRT, "
    "processing...",
    "cosmic-osd.service: Main process exited, code=killed, status=11/SEGV",
    "New session 'c5' of user 'beholder' with class 'user' and type 'wayland'",
    "Session c1 logged out. Waiting for processes to exit.",
    "Stopping User Manager for UID 1000...",
    "user@1000.service: Deactivated successfully.",
    "Stopped cosmic-bg.scope.",
    "kernel BUG at mm/slub.c:424!",
    "NVRM: Xid (PCI:0000:01:00): 79, GPU has fallen off the bus",
    "PM: suspend entry (deep)",
    "watchdog: BUG: soft lockup - CPU#0 stuck for 22s!",
    "systemd[1]: Shutting down.",
    "earlyoom[642]: sending SIGTERM when mem <=  4.00% and swap <= 10.00%",
]


def test_every_rule_survives_the_memory_filter():
    for line in RULE_LINES:
        assert analyse.interesting(line), f"the filter would drop a line a rule needs: {line}"


def test_the_filter_drops_noise():
    noise = [
        "Reached target Multi-User System.",
        "iwlwifi 0000:00:14.3: RFIm is deactivated, reason = 5",
        "Started app-flatpak-com.discordapp.Discord-15425633.scope.",
        "Failed to create watcher for com.system76.CosmicTk",
    ]
    for line in noise:
        assert not analyse.interesting(line), f"the filter kept noise: {line}"


# ---------------------------------------------------------------------- cli ----

def _fake_read(es):
    def _read(boot="0", since=None, until=None, keep=None):
        return es
    return _read


def test_cli_json_is_machine_readable(monkeypatch, capsys):
    monkeypatch.setattr(journal, "read", _fake_read(_broadcast_case()))
    assert main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "pengyos-postmortem"
    assert payload["verdict"]["kind"] == "broadcast"
    assert payload["boot"]["entries"] == 5
    assert payload["limits"]


def test_cli_text_names_the_verdict_and_the_limits(monkeypatch, capsys):
    monkeypatch.setattr(journal, "read", _fake_read(_broadcast_case()))
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "VERDICT: broadcast signal" in out
    assert "RULED OUT" in out and "WHAT THIS DOES NOT PROVE" in out
    assert "one boot id" in out


def test_cli_handles_a_window_with_nothing_relevant(monkeypatch, capsys):
    """A journal full of lines no rule cares about is a finding, not an error."""
    monkeypatch.setattr(journal, "read", _fake_read([]))
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "nothing ended and nothing faulted" in out
    assert "no rule-relevant entries" in out


def test_cli_reports_a_journal_it_cannot_read(monkeypatch, capsys):
    def boom(*a, **k):
        raise journal.JournalError("journalctl returned no entries")
    monkeypatch.setattr(journal, "read", boom)
    assert main([]) == 2
    assert "journalctl returned no entries" in capsys.readouterr().err


def test_cli_passes_the_window_and_boot_through(monkeypatch, capsys):
    seen = {}

    def spy(boot="0", since=None, until=None, keep=None):
        seen.update(boot=boot, since=since, until=until, keep=keep)
        return _broadcast_case()

    monkeypatch.setattr(journal, "read", spy)
    assert main(["--boot", "-1", "--since", "2026-10-25 16:00"]) == 0
    assert seen == {"boot": "-1", "since": "2026-10-25 16:00", "until": None,
                    "keep": analyse.interesting}
