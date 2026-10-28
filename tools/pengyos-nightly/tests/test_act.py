"""Tests for the act lane, against a real git repo and a fake build.

git runs for real here: the claim under test is "a failed run leaves main where it
started", and that is only worth anything if main is a real branch. make, the gate
and release.sh are fakes, so nothing here can build or publish anything.
"""

import datetime as dt
import json
import subprocess
from pathlib import Path

from pengyos_nightly import act as A, cli, guards as G

TODAY = dt.date(2026, 10, 5)


def _repo(tmp_path):
    """A real git repo on a branch called main, with the files act touches."""
    repo = tmp_path / "repo"
    (repo / "buildroot-external" / "configs" / "layers").mkdir(parents=True)
    (repo / "VERSION").write_text("26.10.8\n")
    (repo / "targets.toml").write_text(
        '[[targets]]\nname = "pengyos-desktop"\nbr_version = "2026.08"\n'
        'external_tree = "buildroot-external"\n')
    (repo / "buildroot-external" / "configs" / "layers" / "core.config").write_text(
        "BR2_ROOTFS_OVERLAY=\"x\"\n")
    run = subprocess.run
    for cmd in [["git", "init", "-q", "-b", "main", str(repo)],
                ["git", "-C", str(repo), "config", "user.email", "t@t"],
                ["git", "-C", str(repo), "config", "user.name", "t"],
                ["git", "-C", str(repo), "add", "-A"],
                ["git", "-C", str(repo), "commit", "-q", "-m", "seed"]]:
        run(cmd, check=True, capture_output=True)
    return repo


def _action(**kw):
    a = {"id": "security-libxml2-2.15.3", "lane": "security", "component": "libxml2",
         "current": "2.15.3", "target": "2.15.3", "bump_kind": "none",
         "shape": "patch_dir", "editions": ["pengyos-desktop"], "allowed": True,
         "closes": ["CVE-2026-86137"], "patches": ["p.patch"], "risks": [],
         "cves": [{"cve": "CVE-2026-86137", "priority": "P1", "cvss": 7.8,
                   "affected": True, "affected_note": "2.15.3 < 2.15.4",
                   "fix_commit": {"sha": "76fe08d97de8", "host": "github.com",
                                  "url": "https://github.com/GNOME/libxml2/commit/76fe08d97de8"}}],
         "edits": [{"path": "buildroot-external/patches/libxml2/2.15.3/0001-x.patch",
                    "kind": "create", "new": "From 76fe08d\n", "note": "CVE-2026-86137"},
                   {"path": "buildroot-external/configs/layers/core.config",
                    "kind": "ensure", "new": 'BR2_GLOBAL_PATCH_DIR="x"', "note": "dir"}]}
    return {**a, **kw}


def _cfg(**kw):
    c = {"act_enabled": True, "push": False, "lanes": ["security"], "priorities": ["P0", "P1"],
         "max_actions": 1, "cooldown_days": 7, "allow_patch_shape": True,
         "skip_cosmic": True, "key_file": "", "secrets": ""}
    return {**c, **kw}


class Fake:
    """Stand-in for make / the gate / release.sh.

    git runs for real: the claim under test is that main does not move when a run
    fails, and that is worthless if main is a stub. Everything that could build or
    publish is recorded and faked.
    """

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


def _run(tmp_path, action=None, cfg=None, fail=(), key=None, apply=True):
    repo = _repo(tmp_path)
    site = tmp_path / "site"
    (site / "update" / "pengyos-desktop" / "26.10.7").mkdir(parents=True)
    (site / "update" / "pengyos-desktop" / "26.10.7" / "manifest.json").write_text("{}")
    state = tmp_path / "state"
    cfg = _cfg(**({"key_file": str(key)} if key else {})) if cfg is None else cfg
    fake = Fake(fail)
    result = A.run_act(action or _action(), cfg, repo, tmp_path, site, state,
                       apply=apply, run=fake, today=TODAY)
    return result, fake, repo, site, state, cfg


def _branch(repo):
    return subprocess.run(["git", "-C", str(repo), "branch", "--show-current"],
                          capture_output=True, text=True).stdout.strip()


def _dirty(repo):
    return subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip()


# --- dry run and the kill switch ---------------------------------------------

def test_dry_run_changes_nothing(tmp_path):
    result, fake, repo, *_ = _run(tmp_path, apply=False)
    assert not result["applied"] and not result["published"]
    assert _branch(repo) == "main" and not _dirty(repo)
    assert fake.calls == [], "a dry run must not run make, the gate or release.sh"


def test_act_enabled_false_is_a_hard_stop(tmp_path):
    result, fake, repo, *_ = _run(tmp_path, cfg=_cfg(act_enabled=False, push=False))
    assert "kill switch" in result["error"]
    assert _branch(repo) == "main" and not _dirty(repo)
    assert not fake.asked("release.sh")


def test_a_missing_key_refuses_rather_than_publishing(tmp_path):
    result, fake, repo, *_ = _run(tmp_path, key="/definitely/not/here")
    assert "key_present" in result["error"] and not fake.asked("release.sh")


def test_push_without_a_site_remote_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv("PENGYOS_SITE_REMOTE", raising=False)
    key = tmp_path / "release.key"
    key.write_text("k\n")
    result, fake, *_ = _run(tmp_path, cfg=_cfg(push=True, key_file=str(key)))
    assert "site_remote" in result["error"] and not fake.asked("release.sh")


def test_an_action_the_plan_did_not_allow_is_refused(tmp_path):
    result, fake, repo, *_ = _run(tmp_path, action=_action(allowed=False, refused_by=["cap"]))
    assert "not allowed" in result["error"] and not fake.calls


# --- the happy path ----------------------------------------------------------

def test_one_action_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("PENGYOS_SITE_REMOTE", "miniserv:/srv/site/")
    key = tmp_path / "release.key"
    key.write_text("pengyos-key-v1 ed25519 abc\n")
    result, fake, repo, site, state, cfg = _run(tmp_path, cfg=_cfg(push=True,
                                                                   key_file=str(key)))
    assert result["published"] and result["applied"]
    assert result["version"] == "26.10.8", "26.10.8 was never published, so it is reused"
    assert fake.asked("libxml2-dirclean") and fake.asked("libxml2-patch")
    assert fake.asked("pengyos_gate.py") and fake.asked("release.sh")
    release = " ".join(" ".join(c) for c in fake.calls if any("release.sh" in a for a in c))
    assert "--channel testing" in release and "--push" in release
    assert _branch(repo) == "main", "main moves only by merging the nightly branch"
    merged = subprocess.run(["git", "-C", str(repo), "log", "--merges", "--oneline"],
                            capture_output=True, text=True).stdout
    assert "nightly/2026-10-05-libxml2" in merged
    st = {"cves": {}, "findings": {}, "last_report": "", "actions": {}}
    A.record(result, _action(), state, st, TODAY, run=fake)
    log = json.loads((state / "actions.json").read_text())["actions"]
    assert log[0]["published"] and log[0]["version"] == "26.10.8"
    assert log[0]["cves"] == ["CVE-2026-86137"]
    assert st["actions"] == {"libxml2": "2026-10-05"}, "a published action spends cooldown"


def test_the_patch_and_the_global_dir_land_in_the_tree(tmp_path):
    key = tmp_path / "release.key"
    key.write_text("k\n")
    _run(tmp_path, cfg=_cfg(key_file=str(key)))
    repo = tmp_path / "repo"
    patch = repo / "buildroot-external/patches/libxml2/2.15.3/0001-x.patch"
    assert patch.is_file() and "76fe08d" in patch.read_text()
    layer = (repo / "buildroot-external/configs/layers/core.config").read_text()
    assert "BR2_GLOBAL_PATCH_DIR" in layer


def test_a_published_version_is_bumped_past(tmp_path):
    key = tmp_path / "release.key"
    key.write_text("k\n")
    site = tmp_path / "site"
    (site / "update/pengyos-desktop/26.10.8").mkdir(parents=True)
    (site / "update/pengyos-desktop/26.10.8/manifest.json").write_text("{}")
    result, *_ = _run(tmp_path, cfg=_cfg(key_file=str(key)))
    assert result["version"] == "26.10.9", "26.10.8 is already in the feed"


# --- failure paths: nothing published, main unmoved --------------------------

def test_a_failed_gate_publishes_nothing(tmp_path):
    key = tmp_path / "release.key"
    key.write_text("k\n")
    result, fake, repo, *_ = _run(tmp_path, cfg=_cfg(key_file=str(key)),
                                  fail=["pengyos_gate.py"])
    assert "gate_first" in result["error"]
    assert not fake.asked("release.sh"), "the gate runs before any build"
    assert _branch(repo) == "main" and not _dirty(repo)


def test_a_failed_build_publishes_nothing_and_main_stays(tmp_path):
    key = tmp_path / "release.key"
    key.write_text("k\n")
    result, fake, repo, site, *_ = _run(tmp_path, cfg=_cfg(key_file=str(key)),
                                        fail=["release.sh"])
    assert "release" in result["error"]
    assert _branch(repo) == "main" and not _dirty(repo)
    merged = subprocess.run(["git", "-C", str(repo), "log", "--merges", "--oneline"],
                            capture_output=True, text=True).stdout
    assert merged == "", ("a published-but-unmerged release is a different problem; "
                          "an unpublished one must never reach main")
    assert not (site / "update/pengyos-desktop/26.10.8").exists()


def test_a_patch_that_does_not_apply_stops_before_the_build(tmp_path):
    """The podman finding, made permanent: upstream's own test hunks can fail while
    the source hunks apply. <pkg>-patch catches it in seconds, before release.sh."""
    key = tmp_path / "release.key"
    key.write_text("k\n")
    result, fake, repo, *_ = _run(tmp_path, cfg=_cfg(key_file=str(key)),
                                  fail=["libxml2-patch"])
    assert "dirclean" in result["error"]
    assert not fake.asked("release.sh")
    assert _branch(repo) == "main" and not _dirty(repo)


def test_a_failed_run_does_not_spend_the_cooldown(tmp_path):
    """The first real run failed at dirclean and then found libxml2 blocked for 7
    days by its own failure, so the plan moved on to a different package."""
    key = tmp_path / "release.key"
    key.write_text("k\n")
    action = _action()
    result, fake, repo, site, state, cfg = _run(tmp_path, cfg=_cfg(key_file=str(key)),
                                                fail=["release.sh"])
    st = {"cves": {}, "findings": {}, "last_report": "", "actions": {}}
    A.record(result, action, state, st, TODAY, run=fake)
    assert st["actions"] == {}, "a failed attempt must not consume the component"
    log = json.loads((state / "actions.json").read_text())["actions"]
    assert log[0]["published"] is False and log[0]["error"], "but it is still recorded"


def test_a_published_run_does_consume_the_cooldown(tmp_path):
    key = tmp_path / "release.key"
    key.write_text("k\n")
    action = _action()
    result, fake, *_ = _run(tmp_path, cfg=_cfg(key_file=str(key)))
    st = {"cves": {}, "findings": {}, "last_report": "", "actions": {}}
    A.record(result, action, tmp_path / "state", st, TODAY, run=fake)
    assert st["actions"] == {"libxml2": "2026-10-05"}


def test_the_action_record_names_each_carried_patch_and_its_sha256(tmp_path):
    """So a published image's patches can be audited after the fact: where each came
    from and the hash of what was written, not just the path and the CVE."""
    action = {**_action(), "edits": [
        {"path": "buildroot-external/patches/libxml2/2.15.3/0001-x.patch",
         "fix_url": "https://github.com/GNOME/libxml2/commit/76fe08d", "sha256": "ab" * 32},
        {"path": "buildroot-external/configs/layers/core.config", "kind": "ensure"}]}
    assert A.carried_record(action) == [
        {"path": "buildroot-external/patches/libxml2/2.15.3/0001-x.patch",
         "fix_url": "https://github.com/GNOME/libxml2/commit/76fe08d", "sha256": "ab" * 32}]
    result, fake, *_ = _run(tmp_path)
    state = tmp_path / "state"
    A.record(result, action, state, {"cves": {}, "findings": {}, "actions": {}}, TODAY,
             run=fake)
    log = json.loads((state / "actions.json").read_text())["actions"]
    assert log[-1]["patches"] == A.carried_record(action)


# --- the wall, checked on the way out ----------------------------------------

def test_act_never_assembles_a_stable_publish(tmp_path, monkeypatch):
    """Even if every guard upstream were changed, the argv is checked again."""
    original = G.assert_locked

    def locked(argv):
        joined = " ".join(str(a) for a in argv)
        if "release.sh" in joined:
            return original([str(a) for a in argv])
        return argv
    monkeypatch.setattr(G, "assert_locked", locked)
    key = tmp_path / "release.key"
    key.write_text("k\n")
    result, fake, repo, *_ = _run(tmp_path, cfg=_cfg(key_file=str(key)))
    assert result["published"]
    release = [c for c in fake.calls if any("release.sh" in a for a in c)][0]
    assert G.CHANNEL in release and "stable" not in release and "update-promote" not in release


def test_a_patch_preflight_failure_is_recorded_so_the_planner_stops_trying(tmp_path):
    """The podman finding, made permanent end to end: the failure lands in
    state.json, so the next plan refuses the candidate by name instead of queueing it
    again."""
    key = tmp_path / "release.key"
    key.write_text("k\n")
    action = _action()
    result, fake, repo, site, state, cfg = _run(tmp_path, cfg=_cfg(key_file=str(key)),
                                                fail=["libxml2-patch"])
    assert result["patch_failure"]["date"] == "2026-10-05"
    st = {"cves": {}, "findings": {}, "last_report": "", "actions": {}, "patch_failures": {}}
    A.record(result, action, state, st, TODAY, run=fake)
    assert st["patch_failures"]["libxml2"]["date"] == "2026-10-05"
    assert st["actions"] == {}, "a failed attempt still does not spend the cooldown"


def test_failed_files_names_the_files_a_patch_failed_on():
    detail = ("patching file libpod/container_internal_common.go\n"
              "Hunk #1 FAILED at 10.\n"
              "saving rejects to file test/e2e/run_working_dir_test.go.rej\n")
    where = A._failed_files(detail)
    assert "test/e2e/run_working_dir_test.go.rej" in where
    assert "libpod/container_internal_common.go" in where
    assert A._failed_files("make: *** Error 1") == "make: *** Error 1"


def test_act_exit_codes_distinguish_a_quiet_night_from_a_broken_one(tmp_path, capsys,
                                                                   monkeypatch):
    """Most nights the right answer is 'nothing to do'. If that exits non-zero, a
    watchdog cannot tell a healthy loop from a dead one — the exact confusion
    `status` was built to remove.

    The failing half points --repo / --site / --build-root at empty temp paths, so a
    test can never reach a real build or a real feed.

    The key is written here and pointed at with PENGYOS_RELEASE_KEY: the guards are
    ordered, so without a key of its own this test bails at `key_present` instead of
    `no-repo` and only passes on a machine that owns the signing key. It did exactly
    that — red on the GitHub runner from 2026-10-16 to 10-18, green on the dev box.
    The missing-key path is asserted by test_a_missing_key_refuses_rather_than_publishing.
    """
    key = tmp_path / "release.key"
    key.write_text("k\n")
    monkeypatch.setenv("PENGYOS_RELEASE_KEY", str(key))
    # The same is true one guard later: site_remote falls back to a secrets file under
    # $HOME, so a box with no secrets file also fails before --repo is reached.
    monkeypatch.setenv("PENGYOS_SITE_REMOTE", "test@nowhere:/no-site")
    state = tmp_path / "st"
    state.mkdir()
    common = ["--state-dir", str(state), "--repo", str(tmp_path / "no-repo"),
              "--site", str(tmp_path / "no-site"), "--build-root", str(tmp_path / "no-build")]
    # Global options belong before the subcommand, exactly as cron passes them.

    (state / "plan.json").write_text(json.dumps({"date": "2026-10-05", "actions": [],
                                                 "refused": [{"id": "security-x-1",
                                                              "refused_by": ["cap"]}]}))
    assert cli.main(common + ["act", "--apply"]) == 0, "nothing allowed is a healthy night"
    out = capsys.readouterr().out
    assert "nothing allowed" in out
    assert "key_present" not in out, "the key must not be what this test trips over"

    (state / "plan.json").write_text(json.dumps({"date": "2026-10-05", "actions": [
        {"id": "security-libxml2-2.15.3", "allowed": True, "component": "libxml2",
         "lane": "security", "shape": "patch_dir", "current": "2.15.3", "target": "2.15.3",
         "editions": ["pengyos-desktop"], "cves": [], "closes": [], "patches": [],
         "risks": [], "edits": []}], "refused": []}))
    assert cli.main(common + ["act", "--apply"]) == 1, \
        "an action that cannot be carried out is a real failure"
    out = capsys.readouterr().out
    assert "no-repo" in out
    assert "key_present" not in out and "site_remote" not in out, \
        "the supplied key and remote must get act as far as the --repo guard"


def test_a_published_patch_action_records_the_fix_in_state(tmp_path):
    """A patch shape does not move the version, so pkg-stats will still flag these CVEs
    tomorrow. If the loop does not record that it carries the fix, it cries wolf about
    closed issues forever and re-proposes the same action when the cooldown expires."""
    key = tmp_path / "release.key"
    key.write_text("k\n")
    action = _action(closes=["CVE-2026-86137", "CVE-2026-86143"],
                     patches=["buildroot-external/patches/libxml2/2.15.3/0001-x.patch"])
    result, fake, repo, site, state, cfg = _run(tmp_path, action=action,
                                                cfg=_cfg(key_file=str(key)))
    assert result["published"]
    st = {"cves": {"CVE-2026-86137|libxml2": {"priority": "P1"},
                   "CVE-2026-86143|libxml2": {"priority": "P1"},
                   "CVE-2026-99999|libxml2": {"priority": "P1"}},
          "findings": {}, "last_report": "", "actions": {}, "patch_failures": {}}
    A.record(result, action, state, st, TODAY, run=fake)
    fixed = st["cves"]["CVE-2026-86137|libxml2"]["patched_by"]
    assert fixed["date"] == "2026-10-05" and fixed["version"] == "2.15.3"
    assert fixed["release"] == "26.10.8" and fixed["patches"]
    assert "patched_by" not in st["cves"]["CVE-2026-99999|libxml2"], \
        "only the CVEs this action closed"


def test_notes_say_what_the_release_actually_carries(tmp_path):
    notes = A.notes_for(_action(), "26.10.8", "26.10.8 has never been published")
    assert "carries 1 upstream fix commit(s), closing 1 security issue(s)" in notes
    assert "CVE-2026-86137 (P1, CVSS 7.8, upstream 76fe08d97de8)" in notes
    assert "26.10.8 has never been published" in notes


# --- which patch Buildroot stopped on ----------------------------------------

# Captured from a real `make libsndfile-patch` on behoserver, 2026-10-07: the fix
# NVD named for CVE-2025-52194 is already in libsndfile 1.2.2. Note what is NOT in
# it: apply-patches.sh has a `Patch failed!  Please fix <patch>!` line, but the script
# runs `set -e`, so a failing patch ends it before that echo. The last `Applying`
# line is the only thing that names the culprit.
REAL_PATCH_FAILURE = """Applying 0016-Include-stdbool.h-instead-of-redefining-bool-true-an.patch using patch: 
patching file src/ALAC/alac_decoder.c
patching file src/ALAC/alac_encoder.c

Applying 0001-ima-adpcm-fix-int-overflow-in-ima-reader-init.patch using patch: 
patching file src/ima_adpcm.c
Reversed (or previously applied) patch detected!  Skipping patch.
3 out of 3 hunks ignored -- saving rejects to file src/ima_adpcm.c.rej
make[1]: *** [package/pkg-generic.mk:239: .../build/libsndfile-1.2.2/.stamp_patched] Error 1
make: *** [Makefile:83: _all] Error 2
"""


def test_the_failing_patch_is_named_by_the_last_applying_line():
    assert A._failed_patch(REAL_PATCH_FAILURE) == \
        "0001-ima-adpcm-fix-int-overflow-in-ima-reader-init.patch"


def test_a_buildroot_that_names_the_failing_patch_is_still_preferred():
    assert A._failed_patch("Applying a.patch using patch: \n"
                           "Patch failed!  Please fix b.patch!") == "b.patch"


def test_an_unattributable_failure_names_no_patch():
    assert A._failed_patch("make: *** [Makefile:83: _all] Error 2") == ""


def test_triage_finds_the_culprit_in_a_long_failure_log(tmp_path):
    """One ffmpeg cherry-pick produced 17 failed hunks, so the `Applying` line sits
    hundreds of lines from the end of the log. The message shown to a human is
    summarised; the evidence the triage reads is not."""
    action = {"component": "ffmpeg", "current": "6.1.5", "shape": "patch_dir",
              "editions": ["pengyos-desktop"], "cves": [], "edits": [
                  {"path": "buildroot-external/patches/ffmpeg/6.1.5/0001-a.patch",
                   "kind": "create", "new": "From aaaaaaa\n", "cves": ["CVE-2026-1"],
                   "fix_url": "https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/aaaaaaaaaaaa"},
                  {"path": "buildroot-external/patches/ffmpeg/6.1.5/0002-b.patch",
                   "kind": "create", "new": "From bbbbbbb\n", "cves": ["CVE-2026-2"],
                   "fix_url": "https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/bbbbbbbbbbbb"}]}
    for e in action["edits"]:
        p = tmp_path / e["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(e["new"])

    def fake(argv, **kw):
        argv = [str(a) for a in argv]
        bad = tmp_path / "buildroot-external/patches/ffmpeg/6.1.5/0002-b.patch"
        if any(a.endswith("ffmpeg-patch") for a in argv) and bad.exists():
            out = ("Applying 0001-a.patch using patch: \npatching file libavformat/mov.c\n"
                   "Applying 0002-b.patch using patch: \n"
                   + "".join(f"Hunk #{i} FAILED at {100 * i}.\n" for i in range(1, 61))
                   + "60 out of 60 hunks FAILED -- saving rejects to file libavformat/mov.c.rej\n"
                   + "make[1]: *** [package/pkg-generic.mk:239: build/ffmpeg-6.1.5/"
                     ".stamp_patched] Error 1\nmake: *** [Makefile:83: _all] Error 2\n")
            return subprocess.CompletedProcess(argv, 2, stdout=out, stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    r = A.triage_package(fake, tmp_path, tmp_path, "2026.08", "tree",
                         ["pengyos-desktop"], action)
    assert r["ok"], r["error"]
    assert [Path(e["path"]).name for e in r["carried"]] == ["0001-a.patch"]
    assert [Path(d["patch"]).name for d in r["dropped"]] == ["0002-b.patch"]
    assert r["dropped"][0]["reason"] == "does not apply to the pinned version"


def test_refresh_package_keeps_the_lines_the_triage_needs(tmp_path):
    """The old error summary kept only lines containing FAILED / error:, which is
    exactly how the culprit's name got thrown away."""
    def fake(argv, **kw):
        return subprocess.CompletedProcess(argv, 2, stdout=REAL_PATCH_FAILURE, stderr="")
    try:
        A.refresh_package(fake, tmp_path, "2026.08", "tree", ["pengyos-desktop"], "libsndfile")
        raise AssertionError("expected a failure")
    except RuntimeError as exc:
        assert A._failed_patch(str(exc)) == \
            "0001-ima-adpcm-fix-int-overflow-in-ima-reader-init.patch", str(exc)
        assert "already present in the pinned version" == A._patch_reason(str(exc))


# --- someone else's work in progress is never act's to throw away ------------
# On 2026-10-15 the 21:00 cron found a PengyTasker job's uncommitted Disks 0.2.0 in
# the shared checkout, refused (clean_tree) - and then bail() ran `checkout -f main`,
# which put every modified tracked file back to HEAD. A refusal must touch nothing.

def _work_in_progress(repo):
    (repo / "VERSION").write_text("26.10.8\nsomeone's edit\n")
    (repo / "new-tool.rs").write_text("fn main() {}\n")


def _assert_untouched(repo):
    assert (repo / "VERSION").read_text() == "26.10.8\nsomeone's edit\n"
    assert (repo / "new-tool.rs").read_text() == "fn main() {}\n"
    assert _branch(repo) == "main"


def test_a_dirty_tree_is_refused_and_left_exactly_as_it_was(tmp_path):
    repo = _repo(tmp_path)
    _work_in_progress(repo)
    key = tmp_path / "release.key"
    key.write_text("pengyos-key-v1 ed25519 abc\n")
    fake = Fake()
    result = A.run_act(_action(), _cfg(key_file=str(key)), repo, tmp_path,
                       tmp_path / "site", tmp_path / "state", apply=True, run=fake,
                       today=TODAY)
    assert "clean_tree" in result["error"]
    _assert_untouched(repo)
    assert fake.calls == []


def test_an_early_guard_on_a_dirty_tree_leaves_it_alone(tmp_path):
    repo = _repo(tmp_path)
    _work_in_progress(repo)
    result = A.run_act(_action(), _cfg(act_enabled=False), repo, tmp_path,
                       tmp_path / "site", tmp_path / "state", apply=True, run=Fake(),
                       today=TODAY)
    assert "kill switch" in result["error"]
    _assert_untouched(repo)
