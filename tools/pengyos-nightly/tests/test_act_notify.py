"""The act lane must tell the operator when it **refuses**, not just when it is quiet.

The claim under test:

    a night the act lane refuses sends one mail naming the step that refused,
    the changes it blocked and the CVE matches left open;
    a healthy night, a dry run, a published night and --no-email send none;
    a broken mailer never changes the act result or the exit code.

Why: the act lane's refusals landed in the cron log, in actions.json and in `status`,
and reaching them meant asking. On 2026-10-18 a 6-change batch closing 11 CVE matches
was refused at clean_tree by a PengyTasker job holding /data/PengyOS dirty, and the
operator's morning question was "did the act loop run at all?".

git runs for real. make, the gate and release.sh are fakes, so nothing here can build
or publish anything, and the mailer is always a captured lambda.
"""

import argparse
import datetime as dt
import json
import subprocess
from pathlib import Path

from pengyos_nightly import act as A, cli, report as R

import test_batch as B

TODAY = dt.date(2026, 10, 18)


def _args(tmp_path, state_dir, repo, *, apply=True, no_email=False, to=None):
    return argparse.Namespace(state_dir=str(state_dir), repo=str(repo),
                              build_root=str(tmp_path), site=str(B._site(tmp_path)),
                              only=None, max=0, apply=apply, no_email=no_email, to=to)


def _seed(tmp_path, actions, *, cfg=None):
    """A state dir with a plan of `actions`, as the 19:00 run would leave it."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "nightly.toml").write_text(cfg or (
        f'act_enabled = true\npush = false\nmax_actions = 12\nmax_releases = 1\n'
        f'key_file = "{B._key(tmp_path)}"\nweb_url = "http://10.0.23.6:5050/"\n'))
    (state_dir / "plan.json").write_text(json.dumps(
        {"date": TODAY.isoformat(), "actions": actions, "refused": []}, sort_keys=True))
    (state_dir / "state.json").write_text(json.dumps({}))
    return state_dir


def _sent(tmp_path):
    """The refusal report the run wrote to disk, if any."""
    p = tmp_path / "state" / "reports" / f"{TODAY.isoformat()}-act.html"
    return p.read_text() if p.exists() else None


# --- positive: a refusal sends exactly one, and it says something usable -------

def test_a_clean_tree_refusal_mails_the_operator(tmp_path, capsys):
    """The 2026-10-18 case, end to end through do_act."""
    repo = B._repo(tmp_path)
    (repo / "VERSION").write_text("someone's edit\n")        # the tree is dirty
    action = B._patch_action()
    state_dir = _seed(tmp_path, [action])
    sent = []
    args = _args(tmp_path, state_dir, repo)

    result = cli.do_act(args, today=TODAY,
                        send=lambda s, b, f, **k: sent.append((s, b, f)) or f.write_text(b) or True)
    out = capsys.readouterr().out

    assert "clean_tree" in result["error"], result
    assert not result["published"]
    assert len(sent) == 1, f"one refusal, one mail: {sent}"
    subj, body, _ = sent[0]
    assert "clean_tree" in subj and "1 change(s) published nothing" in subj, subj
    assert "2026-10-18" in subj, subj
    assert action["id"] in body, "the mail names the change the guard blocked"
    assert "http://10.0.23.6:5050/" in body, "the mail links the decision page"
    assert "refusal notice sent" in out, out
    assert _sent(tmp_path), "the notice is on disk whether the mailer worked or not"


def test_a_kill_switch_refusal_mails_the_operator(tmp_path):
    """The guard is a refusal too; the mail must name the guard, not 'clean_tree'."""
    repo = B._repo(tmp_path)
    state_dir = _seed(tmp_path, [B._patch_action()],
                      cfg='act_enabled = false\npush = false\nmax_actions = 12\n'
                          f'max_releases = 1\nkey_file = "{B._key(tmp_path)}"\n')
    sent = []
    cli.do_act(_args(tmp_path, state_dir, repo), today=TODAY,
               send=lambda s, b, f, **k: sent.append((s, b)) or f.write_text(b) or True)
    assert len(sent) == 1, sent
    assert "kill switch" in sent[0][0], sent[0][0]


def test_a_build_failure_mails_the_operator(tmp_path):
    """A batch that gets all the way to the build and publishes nothing is the most
    expensive refusal there is; it must be the loudest, not the quietest."""
    repo = B._repo(tmp_path)
    state_dir = _seed(tmp_path, [B._patch_action()])
    sent = []
    args = _args(tmp_path, state_dir, repo)
    result = cli.do_act(args, today=TODAY, run=B.Fake(fail=("release.sh",)),
                        send=lambda s, b, f, **k: sent.append((s, b)) or f.write_text(b) or True)
    assert not result["published"], result
    assert len(sent) == 1, sent
    assert "CVE" in sent[0][0], "the subject says what the night cost"


def test_the_notice_counts_the_cves_the_batch_would_have_closed(tmp_path):
    repo = B._repo(tmp_path)
    actions = [B._bump_action(component=f"pkg{i}", current="1.0.0", target="1.0.1",
                              closes=[f"CVE-2026-000{i}"]) for i in range(6)]
    state_dir = _seed(tmp_path, actions)
    (repo / "VERSION").write_text("dirty\n")
    sent = []
    cli.do_act(_args(tmp_path, state_dir, repo), today=TODAY,
               send=lambda s, b, f, **k: sent.append((s, b)) or f.write_text(b) or True)
    subj, body = sent[0][0], sent[0][1]
    assert "6 change(s)" in subj, subj
    assert "6 CVE match(es) left open" in subj, subj
    for a in actions:
        assert a["id"] in body, a["id"]


# --- negative: the four nights that must stay silent --------------------------

def test_a_night_with_nothing_allowed_stays_silent(tmp_path, capsys):
    """Most nights the correct answer is 'nothing to do'. Mailing that nightly would
    train the operator to ignore the one mail that matters."""
    repo = B._repo(tmp_path)
    state_dir = _seed(tmp_path, [])
    sent = []
    result = cli.do_act(_args(tmp_path, state_dir, repo), today=TODAY,
                        send=lambda s, b, f, **k: sent.append(s) or True)
    assert result.get("nothing_allowed"), result
    assert sent == [], "a healthy night sends nothing"
    assert _sent(tmp_path) is None


def test_a_dry_run_sends_nothing(tmp_path):
    """`act` without --apply changes nothing on disk; it must not mail either."""
    repo = B._repo(tmp_path)
    (repo / "VERSION").write_text("dirty\n")
    state_dir = _seed(tmp_path, [B._patch_action()])
    sent = []
    cli.do_act(_args(tmp_path, state_dir, repo, apply=False), today=TODAY,
               send=lambda s, b, f, **k: sent.append(s) or True)
    assert sent == [], "a dry run is not a night that happened"


def test_a_dry_run_with_a_failing_plan_sends_nothing(tmp_path):
    """The load-bearing version: a dry run whose result carries an **error** must still
    stay silent. `act` without --apply is a question asked of the tree, not a night that
    ran, and a mail per question is a mail per experiment."""
    repo = B._repo(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)          # plan.json deliberately absent
    sent = []
    result = cli.do_act(_args(tmp_path, state_dir, repo, apply=False), today=TODAY,
                        send=lambda s, b, f, **k: sent.append(s) or True)
    assert result.get("error") == "no plan", result
    assert sent == [], "an error on a dry run is not a refusal that happened"
    assert _sent(tmp_path) is None


def test_no_email_suppresses_the_notice_but_not_the_record(tmp_path):
    repo = B._repo(tmp_path)
    (repo / "VERSION").write_text("dirty\n")
    state_dir = _seed(tmp_path, [B._patch_action()])
    sent = []
    result = cli.do_act(_args(tmp_path, state_dir, repo, no_email=True), today=TODAY,
                        send=lambda s, b, f, **k: sent.append(s) or True)
    assert "clean_tree" in result["error"]
    assert sent == [], "--no-email means nothing goes out on the wire"
    log = json.loads((state_dir / "actions.json").read_text())["actions"]
    assert log and log[-1]["published"] is False and "clean_tree" in log[-1]["error"], log


def test_a_published_night_sends_no_refusal_notice(tmp_path):
    """The positive control for the positive control: a good night is not a refusal."""
    repo = B._repo(tmp_path)
    state_dir = _seed(tmp_path, [B._bump_action()])
    sent = []
    result = cli.do_act(_args(tmp_path, state_dir, repo), today=TODAY, run=B.Fake(),
                        send=lambda s, b, f, **k: sent.append(s) or f.write_text(b) or True)
    assert result["published"], result
    assert sent == [], "a night that shipped does not also complain"
    assert _sent(tmp_path) is None


# --- a broken mailer must not become a second, different failure --------------

def test_a_broken_mailer_leaves_the_act_result_alone(tmp_path, capsys):
    repo = B._repo(tmp_path)
    (repo / "VERSION").write_text("dirty\n")
    state_dir = _seed(tmp_path, [B._patch_action()])
    args = _args(tmp_path, state_dir, repo)

    def broken(subject, body, f, **kw):
        # report.send() runs the mailer with text=True, so stderr arrives as str.
        exc = subprocess.CalledProcessError(2, ["/home/beholder/.local/bin/pengyos-email"])
        exc.output = ""
        exc.stderr = "smtp: connection refused\n"
        raise exc

    result = cli.do_act(args, today=TODAY, send=broken)
    out = capsys.readouterr().out
    assert "clean_tree" in result["error"], "the refusal is still the story"
    assert "NOT delivered" in out and "connection refused" in out, out
    assert _sent(tmp_path), "the notice is on disk even when the mailer failed"
    log = json.loads((state_dir / "actions.json").read_text())["actions"]
    assert "clean_tree" in log[-1]["error"], log


def test_the_subject_names_the_step_for_every_refusal_shape():
    """The subject line is the only part read at a glance; it must never say 'refused'
    when the code knows which guard fired."""
    for step in ("clean_tree", "kill switch", "key_present", "site_remote", "guard",
                 "pre-flight", "release"):
        r = {"error": f"{step}: {step}: something happened", "planned": ["a"], "cves": ["CVE-1"]}
        assert step in R.act_subject("2026-10-18", r), step
    # No error string: fall back to the journal's last failed step.
    r = {"journal": [{"step": "guards", "ok": True}, {"step": "boot", "ok": False}]}
    assert "boot" in R.act_subject("2026-10-18", r), R.act_subject("2026-10-18", r)


def test_the_notice_renders_a_batch_that_dropped_packages(tmp_path):
    """A pre-flight drop is the detail the operator needs to act on; it must survive
    the renderer, and a string-shaped drop must not crash it."""
    body = R.render_act_failure("2026-10-18", {
        "error": "pre-flight: every package in the batch failed its pre-flight",
        "planned": ["security-ffmpeg-6.1.5"], "editions": ["pengyos-desktop"],
        "cves": ["CVE-2026-1"], "dropped": ["security-ffmpeg-6.1.5"]})
    assert "security-ffmpeg-6.1.5" in body and "pengyos-desktop" in body
    body = R.render_act_failure("2026-10-18", {
        "error": "clean_tree: the working tree has uncommitted changes",
        "planned": [], "dropped": [{"patch": "p/0001-x.patch", "reason": "already present"}]})
    assert "0001-x.patch" in body and "already present" in body
