"""Triage rules/model/fallback, and the observe -> triage -> report flow offline."""
import argparse
import datetime as dt
import json
from pathlib import Path

from pengyos_nightly import cli, report, state, triage

FIX = Path(__file__).parent / "fixtures"
IMG_PKG = {"current": "1.38.0", "editions": ["pengyos-core"], "host_only": False}
HOST_PKG = {"current": "2.45.1", "editions": ["pengyos-core"], "host_only": True}


def model(reply):
    calls = []

    def complete(messages):
        calls.append(messages)
        return reply() if callable(reply) else reply
    complete.calls = calls
    return complete


def test_host_only_never_asks_the_model():
    m = model('{"priority":"P0","reachable":"remote","rationale":"x"}')
    v = triage.triage_cve("CVE-1", "binutils", HOST_PKG, {}, {}, {}, m)
    assert v["priority"] == "P2" and v["source"] == "rule" and not m.calls


def test_model_answer_parsed_through_think_and_prose():
    reply = ('<think>hmm {"priority": "P0"}</think>Sure. {"priority": "p1", '
             '"reachable": "file", "rationale": "Crafted input to ash {braces} only."}')
    v = triage.triage_cve("CVE-2", "busybox", IMG_PKG, {"cvss": 5.1}, {}, {}, model(reply))
    assert v == {"priority": "P1", "reachable": "file", "source": "model",
                 "rationale": "Crafted input to ash {braces} only."}


def test_kev_floor_beats_the_model():
    v = triage.triage_cve("CVE-3", "busybox", IMG_PKG, {}, {"CVE-3": {}}, {},
                          model('{"priority":"P2","reachable":"none","rationale":"meh"}'))
    assert v["priority"] == "P1" and "CISA KEV" in v["rationale"]


def test_garbage_twice_falls_back():
    m = model("I think it's fine")
    v = triage.triage_cve("CVE-4", "busybox", IMG_PKG, {"cvss": 9.8}, {}, {}, m)
    assert len(m.calls) == 2 and v["source"] == "fallback" and v["priority"] == "P1"


def test_model_exception_falls_back():
    def boom(messages):
        raise TimeoutError("gateway")
    v = triage.triage_cve("CVE-5", "busybox", IMG_PKG, {"cvss": 4.0}, {}, {}, boom)
    assert v["source"] == "fallback" and v["priority"] == "P2"


def test_prompt_carries_facts_and_exposure():
    m = model('{"priority":"P2","reachable":"none","rationale":"ok"}')
    triage.triage_cve("CVE-2026-38754", "busybox", IMG_PKG,
                      {"description": "heap overflow in ifsbreakup", "cvss": 5.1}, {},
                      {"CVE-2026-38754": {"epss": 0.01}}, m)
    system, user = m.calls[0][0]["content"], json.loads(m.calls[0][1]["content"])
    assert "nftables default-deny" in system and "JSON" in system
    assert user["nvd_description"] == "heap overflow in ifsbreakup" and user["epss_probability"] == 0.01


def _args(tmp_path, **kw):
    base = dict(state_dir=str(tmp_path / "st"), repo=str(tmp_path / "repo"),
                build_root=str(tmp_path / "br"), max_triage=100, jobs=1, model="local",
                no_model=False, no_email=False, force_email=False, mark_reported=False,
                to=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _fake_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "buildroot-external/configs").mkdir(parents=True)
    (repo / "buildroot-external/package/nvidia-open").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "targets.toml").write_text(
        '[[targets]]\nname = "pengyos-core"\nbr_version = "2026.08"\n'
        'external_tree = "buildroot-external"\ndefconfig = "pengyos_core_defconfig"\n')
    (repo / "buildroot-external/configs/pengyos_core_defconfig").write_text(
        'BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE="7.2.5"\n')
    (repo / "buildroot-external/package/nvidia-open/nvidia-open.mk").write_text(
        "NVIDIA_OPEN_VERSION = 595.104.02\n")
    # The shipped agent is an authored pin with its own upstream: the observe
    # step has to read it out of the recipe, so the fake repo carries one.
    (repo / "buildroot-external/package/pengy").mkdir(parents=True)
    (repo / "buildroot-external/package/pengy/pengy.mk").write_text(
        "PENGY_VERSION = 1.10.0\n")
    (repo / "scripts/cosmic-components.tsv").write_text(
        "cosmic-comp\thttps://github.com/pop-os/cosmic-comp\ta55785993e8ef6aad38862cb1a9e1ccaad3c340d\tbuild\n")
    out = tmp_path / "br/output/pengyos-core"
    out.mkdir(parents=True)
    (out / ".config").write_text("")


def _fakes(tmp_path):
    def get(url):
        if "kernel.org" in url:
            return (FIX / "kernel-releases.json").read_text()
        if "cisa.gov" in url:
            return json.dumps({"vulnerabilities": [{"cveID": "CVE-2026-38754"}]})
        if "first.org" in url:
            return json.dumps({"data": []})
        raise AssertionError(url)

    def run_cmd(argv):
        url = argv[-1]
        if "buildroot" in url:
            return (FIX / "ls-remote-buildroot.txt").read_text()
        if "NVIDIA" in url:
            return (FIX / "ls-remote-nvidia.txt").read_text()
        if "PengyR" in url:
            return (FIX / "ls-remote-pengyr.txt").read_text()
        return (FIX / "ls-remote-cosmic-comp.txt").read_text()

    def run_pkgstats(buildroot, out, js, nvd):
        js.write_text((FIX / "pkgstats-core.json").read_text())
    return get, run_cmd, run_pkgstats


def test_full_flow_then_quiet_second_night(tmp_path):
    _fake_repo(tmp_path)
    get, run_cmd, run_pkgstats = _fakes(tmp_path)
    args = _args(tmp_path)
    obs = cli.observe(args, get=get, run_cmd=run_cmd, run_pkgstats=run_pkgstats)
    assert obs["errors"] == {}
    kinds = {(f["lane"], f["component"]) for f in obs["findings"]}
    assert ("routine", "linux") in kinds and ("major", "cosmic") in kinds and ("major", "nvidia-open") in kinds
    # The pin for the agent itself is reported too: 1.10.0 pinned, 1.11.1 out.
    pengyr = [f for f in obs["findings"] if f["component"] == "pengy"]
    assert pengyr and pengyr[0]["available"] == "1.11.1" and pengyr[0]["lane"] == "major"

    m = model('{"priority":"P2","reachable":"none","rationale":"not reachable"}')
    new = cli.do_triage(args, complete=m)
    by_key = {f"{c['cve']}|{c['package']}": c for c in new}
    assert by_key["CVE-2026-38754|busybox"]["priority"] == "P1"        # KEV floor
    assert all(c["priority"] == "P2" for c in new if c["package"] == "binutils")
    assert len(m.calls) < len(new)                                       # host-only skipped the model

    sent = []
    tuesday = dt.date(2026, 10, 6)
    r = cli.do_report(args, send=lambda *a, **k: sent.append(a) or True, today=tuesday)
    assert r["sent"] and "1 P1" in r["subject"] and "major" in r["subject"]
    html = Path(r["report"]).read_text()
    assert "CVE-2026-38754" in html and "cosmic" in html

    # Night two, nothing changed upstream: no model calls, no email.
    cli.observe(args, get=get, run_cmd=run_cmd, run_pkgstats=run_pkgstats)
    m2 = model("unused")
    assert cli.do_triage(args, complete=m2) == [] and not m2.calls
    r2 = cli.do_report(args, send=lambda *a, **k: sent.append(a) or True, today=tuesday + dt.timedelta(days=1))
    assert not r2["sent"] and r2["subject"].endswith("nothing new") and len(sent) == 1

    # Monday: the digest goes out anyway.
    r3 = cli.do_report(args, send=lambda *a, **k: sent.append(a) or True, today=dt.date(2026, 10, 12))
    assert r3["sent"] and "weekly digest" in r3["subject"]


def test_no_email_does_not_consume_the_report_queue(tmp_path):
    """The bug that silenced the loop for two nights.

    On 2026-10-03 a development run used --no-email. The old code marked every new
    CVE and finding as reported in that same branch, so the 19:00 cron run that
    night - and every night after - correctly concluded "nothing new" and stayed
    quiet. A flag meaning "don't send" must not also mean "someone was told".
    """
    _fake_repo(tmp_path)
    get, run_cmd, run_pkgstats = _fakes(tmp_path)
    args = _args(tmp_path, no_email=True)
    cli.observe(args, get=get, run_cmd=run_cmd, run_pkgstats=run_pkgstats)
    cli.do_triage(args, complete=model('{"priority":"P2","reachable":"none","rationale":"x"}'))

    sent = []
    r = cli.do_report(args, send=lambda *a, **k: sent.append(a) or True, today=dt.date(2026, 10, 6))
    assert r["sent"] is False and sent == []
    assert r["new_cves"] and r["new_findings"]              # there IS something to report

    st = state.load(Path(args.state_dir) / "state.json")
    assert st["findings"] == {}, "--no-email must not record findings as reported"
    assert not any(c.get("reported") for c in st["cves"].values()), \
        "--no-email must not mark CVE matches as reported"
    assert not st.get("last_report"), "--no-email must not record a report as delivered"
    # The report file is still written: --no-email is a preview, not a no-op.
    assert (Path(args.state_dir) / "reports" / "2026-10-06.html").read_text()

    # The next run, without the flag, sends the same content. Nothing was lost.
    real = _args(tmp_path)
    r2 = cli.do_report(real, send=lambda *a, **k: sent.append(a) or True, today=dt.date(2026, 10, 7))
    assert r2["sent"] and len(sent) == 1
    assert r2["new_cves"] == r["new_cves"] and r2["new_findings"] == r["new_findings"]
    st2 = state.load(Path(args.state_dir) / "state.json")
    assert st2["findings"] and all(c.get("reported") for c in st2["cves"].values())


def test_mark_reported_clears_the_queue_without_sending(tmp_path):
    """The explicit opt-in, so "I already read it on disk" is a choice you make."""
    _fake_repo(tmp_path)
    get, run_cmd, run_pkgstats = _fakes(tmp_path)
    args = _args(tmp_path, no_email=True, mark_reported=True)
    cli.observe(args, get=get, run_cmd=run_cmd, run_pkgstats=run_pkgstats)
    cli.do_triage(args, complete=model('{"priority":"P2","reachable":"none","rationale":"x"}'))
    sent = []
    r = cli.do_report(args, send=lambda *a, **k: sent.append(a) or True, today=dt.date(2026, 10, 6))
    assert r["sent"] is False and sent == []
    st = state.load(Path(args.state_dir) / "state.json")
    assert st["findings"] and all(c.get("reported") for c in st["cves"].values())
    # Genuinely cleared: the next night has nothing new to say.
    r2 = cli.do_report(args, send=lambda *a, **k: sent.append(a) or True, today=dt.date(2026, 10, 7))
    assert r2["new_cves"] == 0 and r2["new_findings"] == 0


def test_a_forced_report_is_never_blank(tmp_path):
    """--force-email on a quiet night must still say what is outstanding.

    This came from my own mistake: forcing a second report after the weekly digest
    had already gone out produced a 420-byte email whose entire content was
    "nothing new". A report you deliberately ask for should never be empty.
    """
    _fake_repo(tmp_path)
    get, run_cmd, run_pkgstats = _fakes(tmp_path)
    cli.observe(_args(tmp_path), get=get, run_cmd=run_cmd, run_pkgstats=run_pkgstats)
    cli.do_triage(_args(tmp_path),
                  complete=model('{"priority":"P1","reachable":"local","rationale":"x"}'))
    # Tuesday: not a digest day, and everything is already reported.
    quiet = _args(tmp_path, force_email=True)
    sent = []
    r = cli.do_report(quiet, send=lambda *a, **k: sent.append(a) or True, today=dt.date(2026, 10, 6))
    assert r["sent"] and len(sent) == 1
    assert "still open" in r["subject"] and "weekly digest" not in r["subject"]
    body = Path(r["report"]).read_text()
    assert "everything still open" in body and "nvd.nist.gov" in body
    assert "CVE matches tracked;" in body, "a forced report must state the standing load"


def test_source_error_is_reported_not_fatal(tmp_path):
    _fake_repo(tmp_path)
    get, run_cmd, run_pkgstats = _fakes(tmp_path)

    def bad_get(url):
        if "kernel.org" in url:
            raise OSError("no route to host")
        return get(url)
    obs = cli.observe(_args(tmp_path), get=bad_get, run_cmd=run_cmd, run_pkgstats=run_pkgstats)
    assert "kernel.org" in obs["errors"] and obs["findings"]


def test_resolved_cves_are_not_reported(tmp_path):
    _fake_repo(tmp_path)
    get, run_cmd, run_pkgstats = _fakes(tmp_path)
    args = _args(tmp_path, no_email=True)
    cli.observe(args, get=get, run_cmd=run_cmd, run_pkgstats=run_pkgstats)
    cli.do_triage(args, complete=model('{"priority":"P2","reachable":"none","rationale":"x"}'))
    obs_path = Path(args.state_dir) / "observations.json"
    obs = json.loads(obs_path.read_text())
    obs["packages"]["busybox"]["cves"] = []
    obs_path.write_text(json.dumps(obs))
    cli.do_triage(args, complete=model("unused"))
    st = state.load(Path(args.state_dir) / "state.json")
    assert st["cves"]["CVE-2026-38754|busybox"]["resolved"]


def test_send_execs_the_mailer_it_is_given(tmp_path):
    """The 2026-10-07 failure, reproduced as a test.

    The mailer on behoserver (~/.local/bin/pengyos-email) is a bash wrapper around the
    email skill. send() still prefixed it with `python3` - a leftover from when the
    mailer was a .py file - so bash syntax became a SyntaxError, the report step raised,
    and the nightly run died before `plan`. A shell mailer must actually work, and a
    delivered report must come back True or the queue never drains.
    """
    got = tmp_path / "got.txt"
    mailer = tmp_path / "pengyos-email"
    mailer.write_text('#!/bin/sh\nfor a in "$@"; do echo "$a" >> "%s"; done\n' % got)
    mailer.chmod(0o755)
    body = tmp_path / "r.html"
    sent = report.send("nightly subj", "<p>hi</p>", body, to="pat@example.com",
                       script=mailer)
    assert sent is True, "a delivered report must return True, not the CompletedProcess"
    lines = got.read_text().splitlines()
    assert lines[0] == "--quiet" and "--html" in lines
    assert lines[lines.index("--subject") + 1] == "nightly subj"
    assert lines[lines.index("--body-file") + 1] == str(body)
    assert lines[lines.index("--to") + 1] == "pat@example.com"
    assert body.read_text() == "<p>hi</p>"


def test_the_mailer_decides_how_it_is_run(tmp_path):
    """Shebang first, python3 only as the fallback."""
    calls = []
    run = lambda argv, **k: calls.append(argv)

    exe = tmp_path / "mailer.sh"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    report.send("s", "<p>x</p>", tmp_path / "a.html", script=exe, run=run)
    assert calls[0][0] == str(exe), "an executable mailer is exec'd, not fed to python3"

    calls.clear()
    noexec = tmp_path / "mailer2.sh"
    noexec.write_text("#!/bin/sh\nexit 0\n")
    report.send("s", "<p>x</p>", tmp_path / "b.html", script=noexec, run=run)
    assert calls[0][:2] == ["/bin/sh", str(noexec)], "no +x: the shebang still decides"

    calls.clear()
    plain = tmp_path / "mailer3"
    plain.write_text("print('x')\n")
    report.send("s", "<p>x</p>", tmp_path / "c.html", script=plain, run=run)
    assert calls[0][:2] == ["python3", str(plain)], "no shebang: python3 is the fallback"


def test_a_broken_mailer_fails_the_run_but_does_not_cost_the_night(tmp_path, monkeypatch):
    """A mailer that raises is a delivery failure, not a reason to abandon the run.

    On 2026-10-07 the report step raised and `plan` never ran, so the 21:00 act lane
    worked off a stale plan. The failure has to be loud (exit 1, a named reason) and
    contained (the queue stays unreported, the steps after it still run).
    """
    _fake_repo(tmp_path)
    get, run_cmd, run_pkgstats = _fakes(tmp_path)
    args = _args(tmp_path)
    cli.observe(args, get=get, run_cmd=run_cmd, run_pkgstats=run_pkgstats)
    cli.do_triage(args, complete=model('{"priority":"P2","reachable":"none","rationale":"x"}'))

    mailer = tmp_path / "pengyos-email"
    mailer.write_text("#!/bin/sh\necho 'smtp: connection refused' >&2\nexit 1\n")
    mailer.chmod(0o755)
    monkeypatch.setenv("PENGYOS_EMAIL_SCRIPT", str(mailer))

    r = cli.do_report(args, today=dt.date(2026, 10, 7))
    assert r["sent"] is False
    assert r["delivery_error"] and "connection refused" in r["delivery_error"], \
        "the mailer's own error line is what makes the log actionable"
    st = state.load(Path(args.state_dir) / "state.json")
    assert st["last_delivery_error"], "a failed delivery is a fault, recorded in state"
    assert st["findings"] == {}, "an undelivered report may not consume the queue"

    # A healthy delivery drains the queue and closes the fault record.
    good = tmp_path / "pengyos-email-ok"
    good.write_text("#!/bin/sh\nexit 0\n")
    good.chmod(0o755)
    monkeypatch.setenv("PENGYOS_EMAIL_SCRIPT", str(good))
    r2 = cli.do_report(_args(tmp_path), today=dt.date(2026, 10, 8))
    assert r2["sent"] is True and r2["delivery_error"] is None
    st2 = state.load(Path(args.state_dir) / "state.json")
    assert st2["last_delivery_error"] == "", "a delivered report closes the fault"
    assert st2["findings"], "a delivered report is what consumes the queue"


def test_pengyos_email_script_names_the_mailer(tmp_path, monkeypatch):
    """The mailer is machine-local: $PENGYOS_EMAIL_SCRIPT points at it, and a
    machine with nothing configured has no mailer rather than a broken default."""
    mailer = tmp_path / "pengyos-email"
    mailer.write_text("#!/bin/sh\n")
    monkeypatch.setenv("PENGYOS_EMAIL_SCRIPT", str(mailer))
    assert report.email_script() == mailer
    monkeypatch.setenv("PENGYOS_EMAIL_SCRIPT", str(tmp_path / "absent-mailer"))
    assert report.email_script() is None, "a named mailer that does not exist is no mailer"
    monkeypatch.delenv("PENGYOS_EMAIL_SCRIPT", raising=False)
    monkeypatch.setattr(report, "DEFAULT_EMAIL_SCRIPT", tmp_path / "no-launcher-here")
    assert report.email_script() is None


def test_no_mailer_means_report_only_and_not_reported(tmp_path, monkeypatch):
    """A report nobody received has not been reported: send() must say False and
    put nothing on the wire, so the caller leaves the queue intact."""
    monkeypatch.delenv("PENGYOS_EMAIL_SCRIPT", raising=False)
    monkeypatch.setattr(report, "DEFAULT_EMAIL_SCRIPT", tmp_path / "no-launcher-here")
    calls = []
    sent = report.send("subj", "<p>x</p>", tmp_path / "r.html",
                       run=lambda argv, **k: calls.append(argv))
    assert sent is False, "send() may not claim a delivery that did not happen"
    assert calls == [], "nothing may be invoked when no mailer is configured"


def test_digest_subject_carries_the_standing_load_not_just_whats_new():
    """A digest whose subject says "nothing new" while 67 P1 CVEs are open is a
    subject that trains the reader to delete it."""
    open_cves = [{"cve": "CVE-2026-1", "package": "ffmpeg", "priority": "P1"},
                 {"cve": "CVE-2026-2", "package": "tar", "priority": "P2"},
                 {"cve": "CVE-2026-3", "package": "gstreamer1", "priority": "P0"}]
    findings = [{"lane": "major", "component": "bolt"}, {"lane": "routine", "component": "linux"}]

    nightly = report.subject("2026-10-06", [], [], {}, False)
    assert nightly.endswith("nothing new"), "a quiet non-digest night stays quiet"

    digest = report.subject("2026-10-05", [], [], {}, True, open_cves, findings)
    assert "weekly digest" in digest
    assert "2 P0/P1 still open of 3 tracked" in digest
    assert "2 upgrades tracked" in digest
    assert "nothing new" not in digest

    # New items still lead, even on a digest.
    both = report.subject("2026-10-05", [{"cve": "CVE-1", "package": "x", "priority": "P0"}],
                          [{"lane": "major", "component": "bolt"}], {}, True, open_cves, findings)
    assert both.startswith("[P0] PengyOS weekly digest") and "1 P0" in both.split(":")[1]


def test_serious_cves_grouped_by_package_worst_first():
    cves = [{"cve": f"CVE-2026-{i}", "package": "ffmpeg", "version": "6.1.5", "priority": "P1",
             "cvss": 7.0 + i / 10, "editions": ["pengyos-desktop"], "reachable": "file",
             "rationale": "r"} for i in range(3)]
    cves.append({"cve": "CVE-2026-99", "package": "tar", "version": "1.35", "priority": "P0",
                 "cvss": 4.4, "kev": True, "editions": ["pengyos-core"], "reachable": "local",
                 "rationale": "r"})
    html = report.render("2026-10-03", cves, [], {})
    assert html.index("<h4") < html.index("tar 1.35") < html.index("ffmpeg 6.1.5")   # P0 first
    assert "ffmpeg 6.1.5 — 3 P1 (max CVSS 7.2)" in html and "CISA KEV" in html


def test_carried_fixes_are_reported_as_closed_not_open():
    """A patch shape leaves the version where it was, so pkg-stats keeps flagging the
    CVE. The report has to say "we carry the fix" or the same 7 libxml2 CVEs appear in
    every report from now on and the reader learns to distrust the number."""
    carried = [{"cve": "CVE-2026-86137", "package": "libxml2", "version": "2.15.3",
                "priority": "P1", "cvss": 7.8, "editions": ["pengyos-desktop"],
                "rationale": "r",
                "patched_by": {"date": "2026-10-05", "release": "26.10.8", "version": "2.15.3"}}]
    acted = [{"component": "libxml2", "current": "2.15.3", "shape": "patch_dir",
              "version": "26.10.8", "channel": "testing", "cves": ["CVE-2026-86137"],
              "date": "2026-10-05"}]
    html = report.render("2026-10-05", [], [], {}, [], [], True, carried, acted)
    assert "Closed by patches we carry (1)" in html
    assert "CVE-2026-86137" in html
    assert "Published by the loop (1)" in html and "26.10.8</b> on testing" in html
    assert "pkg-stats still flags these" in html
    assert "act on the testing channel" in html and "phase 1" not in html
