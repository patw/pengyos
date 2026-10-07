"""Tests for the phase-3 act lane: a batch of changes, **one** release.

The claim under test is a specific one, and it is the whole point of batching:

    several changes -> one branch, one VERSION, one gate, one build,
                       one boot check, one signature, one published image

and the safety claims that have to survive it: a package that fails its pre-flight
is dropped rather than sinking the batch, a build failure publishes nothing, two
changes that want the same file are refused before a branch exists, and a second
run on the same night cannot publish a second image.

git runs for real. make, the gate and release.sh are fakes, so nothing here can
build or publish anything.
"""

import argparse
import datetime as dt
import json
import subprocess
import time
from pathlib import Path

from pengyos_nightly import act as A, cli, guards as G

import test_triage_report as flow

TODAY = dt.date(2026, 10, 6)


def _repo(tmp_path):
    """A real git repo on `main`, with the files a batch touches."""
    repo = tmp_path / "repo"
    (repo / "buildroot-external" / "configs" / "layers").mkdir(parents=True)
    for pkg, ver in (("libmtp", "1.1.22"), ("alsa-ucm-conf", "1.2.16")):
        (repo / "buildroot-external" / "package" / pkg).mkdir(parents=True)
        (repo / "buildroot-external" / "package" / pkg / f"{pkg}.mk").write_text(
            f"{pkg.upper()}_VERSION = {ver}\n")
        (repo / "buildroot-external" / "package" / pkg / f"{pkg}.hash").write_text("# sha\n")
    # A wider set of authored recipes, for the tests that run a large batch.
    for i in range(15):
        d = repo / "buildroot-external" / "package" / f"pkg{i}"
        d.mkdir(parents=True)
        (d / f"pkg{i}.mk").write_text(f"PKG{i}_VERSION = 1.0.0\n")
        (d / f"pkg{i}.hash").write_text("# sha\n")
    # VERSION is the already-published value, so the batch has a real change to commit:
    # next_version() sees 26.10.8 in the feed and bumps to 26.10.9.
    (repo / "VERSION").write_text("26.10.8\n")
    (repo / "targets.toml").write_text(
        '[[targets]]\nname = "pengyos-desktop"\nbr_version = "2026.08"\n'
        'external_tree = "buildroot-external"\n'
        '[[targets]]\nname = "pengyos-server"\nbr_version = "2026.08"\n'
        'external_tree = "buildroot-external"\n')
    (repo / "buildroot-external" / "configs" / "layers" / "core.config").write_text(
        'BR2_ROOTFS_OVERLAY="x"\n')
    # A stub for the recompose step: the batch runs it once when a layer changed, and
    # the test asserts it ran exactly once. The real script is not needed here.
    (repo / "scripts").mkdir(exist_ok=True)
    (repo / "scripts" / "compose-defconfigs.py").write_text("# stub\n")
    run = subprocess.run
    for cmd in [["git", "init", "-q", "-b", "main", str(repo)],
                ["git", "-C", str(repo), "config", "user.email", "t@t"],
                ["git", "-C", str(repo), "config", "user.name", "t"],
                ["git", "-C", str(repo), "add", "-A"],
                ["git", "-C", str(repo), "commit", "-q", "-m", "seed"]]:
        run(cmd, check=True, capture_output=True)
    return repo


def _patch_action(**kw):
    """A stock package: upstream fix commits carried on the pinned version."""
    a = {"id": "security-libxml2-2.15.3", "lane": "security", "component": "libxml2",
         "current": "2.15.3", "target": "2.15.3", "bump_kind": "none",
         "shape": "patch_dir", "editions": ["pengyos-desktop", "pengyos-server"],
         "allowed": True, "closes": ["CVE-2026-86137"], "patches": ["0001-x.patch"],
         "risks": [], "cves": [{"cve": "CVE-2026-86137", "priority": "P1", "cvss": 7.8,
                                "affected": True, "affected_note": "2.15.3 < 2.15.4",
                                "fix_commit": {"sha": "76fe08d97de8", "host": "github.com",
                                               "url": "https://github.com/GNOME/libxml2/"
                                                      "commit/76fe08d97de8"}}],
         "edits": [{"path": "buildroot-external/patches/libxml2/2.15.3/0001-x.patch",
                    "kind": "create", "new": "From 76fe08d\n", "note": "CVE-2026-86137"},
                   {"path": "buildroot-external/configs/layers/core.config",
                    "kind": "ensure", "new": 'BR2_GLOBAL_PATCH_DIR="x"', "note": "dir"}]}
    return {**a, **kw}


def _bump_action(component="libmtp", current="1.1.22", target="1.1.23", **kw):
    """A recipe we author: a version bump in our own .mk."""
    return {"id": f"routine-{component}-{target}", "lane": "routine",
            "component": component, "current": current, "target": target,
            "bump_kind": "patch", "shape": "recipe_bump", "editions": ["pengyos-server"],
            "allowed": True, "closes": [], "patches": [], "risks": [], "cves": [],
            "edits": [{"path": f"buildroot-external/package/{component}/{component}.mk",
                       "kind": "replace", "old": f"{component.upper()}_VERSION = {current}",
                       "new": f"{component.upper()}_VERSION = {target}", "note": "bump"}],
            **kw}


def _cfg(**kw):
    c = {"act_enabled": True, "push": False, "lanes": ["security", "routine"],
         "priorities": ["P0", "P1"], "max_actions": 12, "max_releases": 1,
         "cooldown_days": 7, "allow_patch_shape": True, "skip_cosmic": True,
         "key_file": "", "secrets": ""}
    return {**c, **kw}


class Fake:
    """Stand-in for make / the gate / release.sh; git runs for real."""

    def __init__(self, fail=()):
        self.calls, self.fail = [], tuple(fail)

    def __call__(self, argv, **kw):
        argv = [str(a) for a in argv]
        if argv[0] == "git":
            opts = {"capture_output": True, "text": True}
            opts.update(kw)
            return subprocess.run(argv, **opts)
        self.calls.append(argv)
        joined = " ".join(argv)
        rc = 1 if any(f in joined for f in self.fail) else 0
        return subprocess.CompletedProcess(argv, rc, stdout="ok " + joined, stderr="")

    def asked(self, needle):
        return any(needle in " ".join(c) for c in self.calls)

    def count(self, needle):
        return sum(1 for c in self.calls if needle in " ".join(c))


def _key(tmp_path):
    k = tmp_path / "release.key"
    k.write_text("pengyos-key-v1 ed25519 abc\n")
    return str(k)


def _site(tmp_path):
    site = tmp_path / "site"
    v = site / "update" / "pengyos-desktop" / "26.10.8"
    v.mkdir(parents=True)
    (v / "manifest.json").write_text("{}")
    return site


def _run(tmp_path, actions, cfg=None, fail=(), apply=True, key=None):
    repo = _repo(tmp_path)
    site = _site(tmp_path)
    state = tmp_path / "state"
    cfg = _cfg(**({"key_file": key or _key(tmp_path)})) if cfg is None else cfg
    fake = Fake(fail)
    result = A.run_batch(actions, cfg, repo, tmp_path, site, state,
                         apply=apply, run=fake, today=TODAY)
    return result, fake, repo, site, state, cfg


def _branch(repo):
    return subprocess.run(["git", "-C", str(repo), "branch", "--show-current"],
                          capture_output=True, text=True).stdout.strip()


def _dirty(repo):
    return subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip()


def _commits(repo, rev="HEAD"):
    return subprocess.run(["git", "-C", str(repo), "log", *rev.split(), "--format=%s"],
                          capture_output=True, text=True).stdout.splitlines()


def _seed(repo):
    """main's pre-batch tip: everything reachable from it is not part of the batch."""
    return subprocess.run(["git", "-C", str(repo), "rev-list", "--max-parents=0", "main"],
                          capture_output=True, text=True).stdout.split()[-1]


# --- the batch shape ----------------------------------------------------------

def test_a_batch_of_three_publishes_one_image(tmp_path, monkeypatch):
    monkeypatch.setenv("PENGYOS_SITE_REMOTE", "miniserv:/srv/site/")
    actions = [_patch_action(), _bump_action(),
               _bump_action(component="alsa-ucm-conf", current="1.2.16", target="1.2.16.1")]
    result, fake, repo, site, state, cfg = _run(
        tmp_path, actions, cfg=_cfg(push=True, key_file=_key(tmp_path)))

    assert result["published"] and result["applied"]
    assert result["applied_actions"] == [a["id"] for a in actions]
    # One branch, one version, one gate, one release.
    assert result["branch"] == "nightly/2026-10-06-batch"
    assert result["version"] == "26.10.9", "26.10.8 is already published, so this is 26.10.9"
    assert fake.count("pengyos_gate.py") == 1, "the gate runs once for the batch"
    assert fake.count("release.sh") == 1, "one signed image, not one per change"
    assert fake.count("compose-defconfigs.py") == 1, "the layers are recomposed once"
    # One commit per change, plus VERSION. The recompose the layer edit forces is
    # asserted on the journal instead of the commit log: a commit only exists if the
    # recompose actually produced a changed defconfig, which is the real question.
    msgs = _commits(repo, f"--all --not {_seed(repo)}")
    nightly = [m for m in msgs if m.startswith("nightly ")]
    assert len(nightly) == 4, msgs
    assert any("set VERSION" in m for m in nightly), nightly
    for component in ("libxml2", "libmtp", "alsa-ucm-conf"):
        assert any(component in m for m in nightly), nightly
    steps = [j["step"] for j in result["journal"]]
    assert steps.count("compose-defconfigs") == 1, steps
    assert steps.count("pre-flight") == 3 and steps.count("gate_first") == 1, steps
    assert _branch(repo) == "main"
    merged = subprocess.run(["git", "-C", str(repo), "log", "--merges", "--oneline"],
                            capture_output=True, text=True).stdout
    assert "nightly/2026-10-06-batch" in merged


def test_release_runs_once_for_the_union_of_editions_and_stays_on_testing(tmp_path):
    actions = [_patch_action(), _bump_action()]          # desktop+server, and server
    result, fake, *_ = _run(tmp_path, actions, cfg=_cfg(key_file=_key(tmp_path)))
    releases = [c for c in fake.calls if any("release.sh" in a for a in c)]
    assert len(releases) == 1
    argv = releases[0]
    assert argv.count("pengyos-desktop") == 1 and argv.count("pengyos-server") == 1, \
        "the union of editions, each once"
    assert "--channel" in argv and argv[argv.index("--channel") + 1] == "testing"
    joined = " ".join(argv)
    assert "update-promote" not in joined and "--channel stable" not in joined
    assert result["editions"] == ["pengyos-desktop", "pengyos-server"]


def test_the_batch_notes_name_every_change_and_its_evidence(tmp_path):
    actions = [_patch_action(), _bump_action()]
    result, *_ = _run(tmp_path, actions, cfg=_cfg(key_file=_key(tmp_path)))
    notes = (tmp_path / "state" / "notes-batch-2026-10-06.md").read_text()
    assert "nightly batch of 2 change(s)" in notes
    assert "CVE-2026-86137" in notes and "upstream 76fe08d97de8" in notes
    assert "libmtp 1.1.22 -> 1.1.23" in notes
    assert "Editions rebuilt: pengyos-desktop, pengyos-server" in notes


# --- a bad candidate must not spend the night ---------------------------------

def test_a_package_that_fails_its_preflight_is_dropped_and_the_rest_ship(tmp_path):
    actions = [_patch_action(), _bump_action()]
    result, fake, repo, site, state, cfg = _run(tmp_path, actions,
                                                fail=["libxml2-dirclean"],
                                                cfg=_cfg(key_file=_key(tmp_path)))
    assert result["published"], "one bad cherry-pick does not sink the batch"
    assert result["dropped"] == ["security-libxml2-2.15.3"]
    assert result["applied_actions"] == ["routine-libmtp-1.1.23"]
    assert "libxml2" in result["patch_failures"]
    assert not (repo / "buildroot-external/patches/libxml2").exists(), \
        "a dropped change must not stay in the tree that got published"
    assert fake.count("release.sh") == 1

    # And the drop is remembered, so the planner refuses that candidate by name
    # tomorrow instead of spending another night discovering it again.
    st = {"cves": {}, "findings": {}, "actions": {}}
    A.record_batch(result, actions, state, st, TODAY, run=fake)
    ok, why = G.patch_does_not_apply("libxml2", st)
    assert not ok and "patch_does_not_apply" in why, why
    assert G.patch_does_not_apply("libmtp", st)[0], \
        "a package that shipped must not be marked as a known-bad candidate"


def test_when_every_package_fails_nothing_is_built_or_published(tmp_path):
    actions = [_patch_action(), _bump_action()]
    result, fake, repo, *_ = _run(tmp_path, actions, fail=["-dirclean"],
                                  cfg=_cfg(key_file=_key(tmp_path)))
    assert "pre-flight" in result["error"]
    assert not result["published"]
    assert not fake.asked("release.sh"), "a batch with nothing left must not build"
    assert _branch(repo) == "main" and not _dirty(repo)


def test_a_build_or_boot_failure_publishes_nothing_and_records_the_block(tmp_path):
    actions = [_patch_action(), _bump_action()]
    result, fake, repo, site, state, cfg = _run(tmp_path, actions, fail=["release.sh"],
                                                cfg=_cfg(key_file=_key(tmp_path)))
    assert "release" in result["error"] and not result["published"]
    assert "batch_failure" in result
    assert _branch(repo) == "main", "main does not move when nothing was published"
    st = {"cves": {}, "findings": {}, "actions": {}}
    A.record_batch(result, actions, state, st, TODAY, run=fake)
    log = json.loads((state / "actions.json").read_text())["actions"]
    assert log[-1]["kind"] == "batch" and not log[-1]["published"]


# --- the release cap: the blast radius stays one image ------------------------

def test_a_second_run_the_same_night_cannot_publish_a_second_image(tmp_path):
    actions = [_patch_action(), _bump_action()]
    cfg = _cfg(key_file=_key(tmp_path))
    repo = _repo(tmp_path)
    site = _site(tmp_path)
    state = tmp_path / "state"

    fake1 = Fake()
    r1 = A.run_batch(actions, cfg, repo, tmp_path, site, state, apply=True,
                     run=fake1, today=TODAY)
    assert r1["published"] and fake1.count("release.sh") == 1
    A.record_batch(r1, actions, state, {"cves": {}, "findings": {}, "actions": {}},
                   TODAY, run=fake1)

    assert A.published_today(state, TODAY) == 1, \
        "one image, even though the batch wrote one entry per change"
    fake2 = Fake()
    r2 = A.run_batch(actions, cfg, repo, tmp_path, site, state, apply=True,
                     run=fake2, today=TODAY)
    assert "release_cap" in r2["error"]
    assert not fake2.asked("release.sh") and not fake2.asked("-dirclean")


def test_release_cap_guard_itself(tmp_path):
    assert G.release_cap(0, 1)[0] is True
    ok, why = G.release_cap(1, 1)
    assert ok is False and "release_cap" in why


# --- two changes that want the same file are a human's decision --------------

def test_two_actions_editing_the_same_file_are_refused_before_a_branch(tmp_path):
    a = _bump_action()
    b = _bump_action(id="routine-libmtp-1.1.24", target="1.1.24",
                     edits=[{"path": "buildroot-external/package/libmtp/libmtp.mk",
                             "kind": "replace", "old": "LIBMTP_VERSION = 1.1.22",
                             "new": "LIBMTP_VERSION = 1.1.24", "note": "other bump"}])
    ok, why = G.batch_conflict([a, b])
    assert ok is False and "batch_conflict" in why and "libmtp.mk" in why

    result, fake, repo, *_ = _run(tmp_path, [a, b], cfg=_cfg(key_file=_key(tmp_path)))
    assert "batch_conflict" in result["error"]
    assert fake.calls == [], "refused before a branch, a build or a publish"
    assert _branch(repo) == "main" and not _dirty(repo)


def test_independent_changes_do_not_trip_the_conflict_guard():
    assert G.batch_conflict([_patch_action(), _bump_action()])[0] is True


# --- dry run and the planner's cap -------------------------------------------

def test_dry_run_of_a_batch_changes_nothing(tmp_path):
    result, fake, repo, *_ = _run(tmp_path, [_patch_action(), _bump_action()], apply=False)
    assert not result["applied"] and not result["published"]
    assert _branch(repo) == "main" and not _dirty(repo)
    assert fake.calls == [], "a dry run must not run make, the gate or release.sh"


def test_the_batch_is_sized_by_max_actions_not_left_open(tmp_path):
    """`cap` sizes the batch; it is no longer a one-action-a-night rule."""
    assert G.cap(11, 12)[0] is True, "an 12th candidate still fits"
    ok, why = G.cap(12, 12)
    assert ok is False and "cap" in why


def test_do_act_batches_up_to_max_actions_and_leaves_the_rest_queued(tmp_path, capsys):
    """15 allowed candidates, max_actions 12 -> a 12-change batch, the rest queued."""
    repo = _repo(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "nightly.toml").write_text(
        f'act_enabled = true\npush = false\nmax_actions = 12\nmax_releases = 1\n'
        f'key_file = "{_key(tmp_path)}"\n')
    actions = [_bump_action(component=f"pkg{i}", current="1.0.0", target="1.0.1")
               for i in range(15)]
    (state_dir / "plan.json").write_text(json.dumps(
        {"date": "2026-10-06", "actions": actions, "refused": []}, sort_keys=True))

    args = argparse.Namespace(state_dir=str(state_dir), repo=str(repo),
                              build_root=str(tmp_path), site=str(_site(tmp_path)),
                              only=None, max=0, apply=False)
    result = cli.do_act(args, today=TODAY)   # dry run: the batch is chosen, nothing runs
    out = capsys.readouterr().out
    assert len(result["planned"]) == 12, out
    assert "batch of 12 change(s)" in out, out
    assert "batching the first 12" in out and "next night" in out, out
    assert result["journal"][0]["step"] == "dry run"


def test_a_twelve_change_batch_still_publishes_one_image(tmp_path):
    """The whole point of phase 3, at the size the policy now allows."""
    actions = [_bump_action(component=f"pkg{i}", current="1.0.0", target="1.0.1")
               for i in range(12)]
    result, fake, repo, site, state, cfg = _run(tmp_path, actions,
                                                cfg=_cfg(key_file=_key(tmp_path)))
    assert result["published"]
    assert len(result["applied_actions"]) == 12
    assert fake.count("release.sh") == 1, "twelve changes, one signed image"
    assert fake.count("pengyos_gate.py") == 1
    assert fake.count("-dirclean") == 12, "each package is still pre-flighted on its own"
    assert not fake.asked("compose-defconfigs.py"), "no layer changed, so no recompose"
    assert result["editions"] == ["pengyos-server"]


# --- status must not read a blocked batch as a quiet, healthy night ----------

def test_status_reports_a_blocked_batch(tmp_path, capsys):
    args = flow._args(tmp_path)
    flow._fake_repo(tmp_path)
    # Seed the loop's state through the real pipeline, then record a batch that ran
    # and published nothing.
    get, run_cmd, run_pkgstats = flow._fakes(tmp_path)
    cli.observe(args, get=get, run_cmd=run_cmd, run_pkgstats=run_pkgstats)
    cli.do_triage(args, complete=flow.model('{"priority":"P1","reachable":"local",'
                                            '"rationale":"x"}'))
    cli.do_report(args, send=lambda *a, **k: None, today=dt.date(2026, 10, 6))
    state_dir = Path(args.state_dir)
    (state_dir / "actions.json").write_text(json.dumps({"actions": [
        {"kind": "batch", "date": "2026-10-06", "published": False,
         "error": "release: build / boot check / publish failed; nothing was published",
         "changes": [], "dropped": ["security-libxml2-2.15.3"]}]}))

    result = cli.do_status(args, now=time.time())
    out = capsys.readouterr().out
    assert result["ok"] is False, out
    assert any("last batch published nothing" in p for p in result["problems"]), out
    assert "batch policy: up to 12 change(s) -> 1 release(s)/night" in out, out
