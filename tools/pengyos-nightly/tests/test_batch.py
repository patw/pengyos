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
    # The act lane guards on this being runnable before it branches (see
    # guards.release_script_executable), so the fixture has to be a runnable stub.
    (repo / "scripts" / "release.sh").write_text("#!/bin/sh\n# stub\n")
    (repo / "scripts" / "release.sh").chmod(0o755)
    # Tracked on main, like it is in the real tree. Whether this file is in main decides
    # how a bail can take a declaration back out, so the fixture has to match.
    (repo / "buildroot-external" / "patches").mkdir(exist_ok=True)
    (repo / "buildroot-external" / "patches" / "cve-declarations.mk").write_text(
        "# Carried upstream fixes, declared to the tooling that reads this tree.\n")
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
    """Stand-in for make / the gate / release.sh; git runs for real.

    `patch_fails` is {component: (patch name, ...)} and simulates Buildroot's own
    rejection. A name that is in our tree is ours: it fails while the file is there and
    stops once the triage removes it, which is the behaviour the triage exists to get.
    A name that is not in our tree stands for the recipe's own stock patch - `<pkg>-patch`
    applies those too, the loop cannot take them out, and they fail every time.
    """

    def __init__(self, fail=(), patch_fails=None, repo=None):
        self.calls, self.fail = [], tuple(fail)
        self.patch_fails = {k: tuple(v) for k, v in (patch_fails or {}).items()}
        self.repo = repo
        # A name we have actually seen in the tree is ours: it fails while the file is
        # there and stops once the triage removes it, which is the behaviour the triage
        # exists to get. A name that is never in our tree stands for the recipe's own
        # stock patch - `<pkg>-patch` applies those too, the loop cannot take them out,
        # and they fail every time.
        self.ours = set()

    def _rejects(self, pkg: str, name: str) -> bool:
        if list((self.repo / "buildroot-external").rglob(name)):
            self.ours.add((pkg, name))
            return True
        return (pkg, name) not in self.ours

    def __call__(self, argv, **kw):
        argv = [str(a) for a in argv]
        if argv[0] == "git":
            opts = {"capture_output": True, "text": True}
            opts.update(kw)
            return subprocess.run(argv, **opts)
        self.calls.append(argv)
        joined = " ".join(argv)
        rc = 1 if any(f in joined for f in self.fail) else 0
        out = "ok " + joined
        if rc == 0 and self.repo:
            for pkg, names in self.patch_fails.items():
                if f"{pkg}-patch" not in joined:
                    continue
                for name in names:
                    if not self._rejects(pkg, name):
                        continue          # ours, and already taken out of the tree
                    rc = 1
                    out = (f"Applying {name} using patch: \n"
                           "patching file src/ima_adpcm.c\n"
                           "Reversed (or previously applied) patch detected!  Skipping patch.\n"
                           "3 out of 3 hunks ignored -- saving rejects to file src/ima_adpcm.c.rej\n"
                           "make[1]: *** [package/pkg-generic.mk:239: "
                           "build/libsndfile-1.2.2/.stamp_patched] Error 1\n"
                           "make: *** [Makefile:83: _all] Error 2")
                    break
                break
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr="")

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


def _run(tmp_path, actions, cfg=None, fail=(), patch_fails=None, apply=True, key=None):
    repo = _repo(tmp_path)
    site = _site(tmp_path)
    state = tmp_path / "state"
    cfg = _cfg(**({"key_file": key or _key(tmp_path)})) if cfg is None else cfg
    fake = Fake(fail, patch_fails, repo)
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


def test_the_batch_notes_say_what_the_patch_set_left_open(tmp_path):
    """A subset patch must not read as a clean sweep: the CVEs NVD could not name a
    fix for are named, so the next reader is not told the package is clear."""
    actions = [_patch_action(still_open=["CVE-2026-18477", "CVE-2026-8631"])]
    _run(tmp_path, actions, cfg=_cfg(key_file=_key(tmp_path)))
    notes = (tmp_path / "state" / "notes-batch-2026-10-06.md").read_text()
    assert "Still open in libxml2: CVE-2026-18477, CVE-2026-8631" in notes, notes


# --- a bad candidate must not spend the night ---------------------------------

def _two_patch_action(**kw):
    """A stock package carrying two fixes, so one can be rejected and the other kept."""
    base = "buildroot-external/patches/libxml2/2.15.3"
    good = "https://github.com/GNOME/libxml2/commit/76fe08d97de8"
    bad = "https://github.com/GNOME/libxml2/commit/aaaaaaaaaaaa"
    a = _patch_action(
        closes=["CVE-2026-86137", "CVE-2026-86144"],
        patches=[f"{base}/0001-a.patch", f"{base}/0002-b.patch"],
        cves=[{"cve": "CVE-2026-86137", "priority": "P1", "cvss": 7.8, "affected": True,
               "affected_note": "2.15.3 < 2.15.4", "fix_commit": {"sha": "76fe08d97de8",
                                                                   "host": "github.com",
                                                                   "url": good}},
                {"cve": "CVE-2026-86144", "priority": "P1", "cvss": 5.5, "affected": True,
                 "affected_note": "2.15.3 < 2.15.4", "fix_commit": {"sha": "aaaaaaaaaaaa",
                                                                    "host": "github.com",
                                                                    "url": bad}}],
        edits=[{"path": f"{base}/0001-a.patch", "kind": "create", "new": "From 76fe08d\n",
                "note": "CVE-2026-86137", "cves": ["CVE-2026-86137"], "fix_url": good},
               {"path": f"{base}/0002-b.patch", "kind": "create", "new": "From aaaa\n",
                "note": "CVE-2026-86144", "cves": ["CVE-2026-86144"], "fix_url": bad},
               {"path": "buildroot-external/configs/layers/core.config", "kind": "ensure",
                "new": 'BR2_GLOBAL_PATCH_DIR="x"', "note": "dir"}],
        still_open=[])
    return {**a, **kw}


def test_a_patch_buildroot_rejects_is_dropped_and_the_rest_of_the_package_ships(tmp_path):
    """The unit of failure is a patch, not a package.

    `apply-patches.sh` names the patch it could not apply, so the loop takes that one
    file out, re-extracts, and ships what survives - instead of condemning the whole
    package because one cherry-pick was wrong. Measured 2026-10-06: ffmpeg had 11 of 19
    patches that apply, and the old behaviour shipped none of them.
    """
    actions = [_two_patch_action(), _bump_action()]
    result, fake, repo, site, state, cfg = _run(tmp_path, actions,
                                                patch_fails={"libxml2": ("0002-b.patch",)},
                                                cfg=_cfg(key_file=_key(tmp_path)))
    assert result["published"], "one uncarriable cherry-pick does not sink the package"
    assert result["dropped"] == [] and result["applied_actions"] == [
        "security-libxml2-2.15.3", "routine-libmtp-1.1.23"], result["dropped"]
    assert not (repo / "buildroot-external/patches/libxml2/2.15.3/0002-b.patch").exists(), \
        "the rejected patch must not stay in the tree the image is built from"
    assert (repo / "buildroot-external/patches/libxml2/2.15.3/0001-a.patch").exists()
    assert fake.count("libxml2-dirclean") >= 2, "re-extract after each rejection"

    # The claim follows the tree: one patch carried, one CVE closed, one still open.
    notes = (tmp_path / "state" / "notes-batch-2026-10-06.md").read_text()
    assert "Not carried: 0002-b.patch: already present in the pinned version " \
           "(CVE-2026-86144)" in notes, notes
    assert "Still open in libxml2: CVE-2026-86144" in notes, notes
    assert "carries 1 upstream fix commit(s), closing 1 security issue(s)" in notes, notes

    st = {"cves": {"CVE-2026-86137|libxml2": {}, "CVE-2026-86144|libxml2": {}},
          "findings": {}, "actions": {}, "patch_failures": {}}
    A.record_batch(result, actions, state, st, TODAY, run=fake, repo=repo)
    assert "patched_by" in st["cves"]["CVE-2026-86137|libxml2"]
    assert "patched_by" not in st["cves"]["CVE-2026-86144|libxml2"], \
        "not carried, so not claimed closed"
    assert st["patch_failures"]["libxml2"]["fixes"] == [
        "https://github.com/GNOME/libxml2/commit/aaaaaaaaaaaa"]


def test_a_rejected_patch_does_not_block_the_other_fixes_for_that_package(tmp_path):
    """The known-bad record is per fix. A cherry-pick written for a newer series says
    nothing about a fix that would apply cleanly, so the package stays open."""
    actions = [_two_patch_action()]
    result, fake, repo, site, state, cfg = _run(tmp_path, actions,
                                                patch_fails={"libxml2": ("0002-b.patch",)},
                                                cfg=_cfg(key_file=_key(tmp_path)))
    st = {"cves": {}, "findings": {}, "actions": {}, "patch_failures": {}}
    A.record_batch(result, actions, state, st, TODAY, run=fake, repo=repo)
    bad = "https://github.com/GNOME/libxml2/commit/aaaaaaaaaaaa"
    good = "https://github.com/GNOME/libxml2/commit/76fe08d97de8"
    ok, why = G.patch_does_not_apply("libxml2", st, [bad])
    assert not ok and "known-bad" in why, why
    assert G.patch_does_not_apply("libxml2", st, [good])[0], \
        "the package is not blocked, the failed fix is"


def test_when_every_patch_for_a_package_is_rejected_the_package_is_dropped(tmp_path):
    base = "buildroot-external/patches/libxml2/2.15.3"
    actions = [_two_patch_action(), _bump_action()]
    result, fake, repo, site, state, cfg = _run(tmp_path, actions,
                                                patch_fails={"libxml2": ("0001-a.patch",
                                                                       "0002-b.patch")},
                                                cfg=_cfg(key_file=_key(tmp_path)))
    assert result["published"], "the rest of the batch still ships"
    assert result["dropped"] == ["security-libxml2-2.15.3"]
    assert not (repo / "buildroot-external/patches/libxml2").exists(), \
        "a package that carries no patches must not look like one that does"
    assert result["patch_failures"]["libxml2"]["fixes"] == [
        "https://github.com/GNOME/libxml2/commit/76fe08d97de8",
        "https://github.com/GNOME/libxml2/commit/aaaaaaaaaaaa"]


def test_a_failure_on_buildroots_own_stock_patch_is_not_blamed_on_ours(tmp_path):
    """`<pkg>-patch` applies the recipe's own patches too, and they appear in the same
    output with the same `NNNN-` numbering. A failure attributed to a patch this action
    never wrote must stop the triage, not quietly delete one of ours that looks similar.
    """
    actions = [_two_patch_action(), _bump_action()]
    result, fake, repo, site, state, cfg = _run(tmp_path, actions,
                                                patch_fails={"libxml2": ("0001-a-extra.patch",)},
                                                cfg=_cfg(key_file=_key(tmp_path)))
    assert result["dropped"] == ["security-libxml2-2.15.3"]
    assert result["patch_failures"]["libxml2"]["fixes"] == [], \
        "we do not know which of our fixes is at fault, so none is blacklisted"
    assert not (repo / "buildroot-external/patches/libxml2").exists(), \
        "the whole change is reverted, not one patch of it"


def test_a_dropped_package_leaves_no_half_patched_tree_behind(tmp_path):
    """2026-10-08: the batch was lost to the corpse of a pre-flight.

    libsndfile's only carried fix was one Buildroot 2026.08 already ships, so the
    pre-flight failed and the package was dropped - correctly. But its build tree was
    left extracted, partly patched, with `.applied_patches_list` populated and
    `.stamp_patched` missing. The full build then re-ran the patch step, apply-patches.sh
    saw basenames it had already applied and died with `Error: duplicate filename` on
    Buildroot's own 0001-...patch. Nothing was published, and the error pointed at a
    package that had already been taken out of the batch.
    """
    actions = [_two_patch_action(), _bump_action()]
    result, fake, repo, *_ = _run(tmp_path, actions,
                                  patch_fails={"libxml2": ("0001-a.patch", "0002-b.patch")},
                                  cfg=_cfg(key_file=_key(tmp_path)))
    assert result["dropped"] == ["security-libxml2-2.15.3"]
    steps = [c[-1] for c in fake.calls if any("libxml2-" in str(a) for a in c)]
    assert steps and steps[-1] == "libxml2-dirclean", \
        f"the last thing done to a dropped package must be wiping its tree: {steps}"
    assert result["published"], "and the rest of the batch still ships"


def test_known_bad_fixes_accumulate_across_nights(tmp_path):
    """The known-bad set is a memory, not a snapshot: overwriting it would make the loop
    rediscover the same bad cherry-pick every week."""
    old = "https://github.com/GNOME/libxml2/commit/oldoldold"
    st = {"cves": {}, "findings": {}, "actions": {},
          "patch_failures": {"libxml2": {"date": "2026-10-05", "where": "parser.c",
                                         "fixes": [old]}}}
    actions = [_two_patch_action()]
    result, fake, repo, site, state, cfg = _run(tmp_path, actions,
                                                patch_fails={"libxml2": ("0002-b.patch",)},
                                                cfg=_cfg(key_file=_key(tmp_path)))
    A.record_batch(result, actions, state, st, TODAY, run=fake, repo=repo)
    assert set(st["patch_failures"]["libxml2"]["fixes"]) == {
        old, "https://github.com/GNOME/libxml2/commit/aaaaaaaaaaaa"}
    assert st["patch_failures"]["libxml2"]["date"] == "2026-10-06", "the newer fact wins"


def test_a_preflight_buildroot_does_not_attribute_to_a_patch_stops_the_triage(tmp_path):
    """No `Please fix <patch>!` line, no guessing which change is at fault."""
    actions = [_two_patch_action(), _bump_action()]
    result, fake, repo, site, state, cfg = _run(tmp_path, actions, fail=("libxml2-dirclean",),
                                                cfg=_cfg(key_file=_key(tmp_path)))
    assert result["dropped"] == ["security-libxml2-2.15.3"]
    assert result["patch_failures"]["libxml2"]["fixes"] == [], \
        "an unattributed failure must not blacklist the fixes"


def _commit_files(repo):
    """[(commit subject, [paths])] for every commit the batch added."""
    out = subprocess.run(["git", "-C", str(repo), "log", "--all", "--not", _seed(repo),
                          "--name-only", "--format=COMMIT:%s"],
                         capture_output=True, text=True).stdout
    groups, cur = [], None
    for line in out.splitlines():
        if line.startswith("COMMIT:"):
            cur = (line[len("COMMIT:"):], [])
            groups.append(cur)
        elif line.strip() and cur is not None:
            cur[1].append(line.strip())
    return groups


def test_a_carried_fix_is_declared_to_pkg_stats_in_the_same_commit(tmp_path):
    """A patch the loop carries has to be declared or the tree keeps reporting the CVE
    open after shipping the fix (pkg-stats cannot see a BR2_GLOBAL_PATCH_DIR patch).
    The declaration is written by the loop, committed **with the change it describes**,
    and names the patch file - buildroot-external/patches/README.md rule 3."""
    result, fake, repo, site, state, cfg = _run(tmp_path, [_two_patch_action()])
    rel = "buildroot-external/patches/cve-declarations.mk"
    decl = repo / rel
    assert decl.is_file(), [s["step"] for s in result["journal"]]
    text = decl.read_text()
    assert "# 0001-a.patch" in text and "# 0002-b.patch" in text, text
    assert "LIBXML2_IGNORE_CVES += CVE-2026-86137" in text, text
    assert "LIBXML2_IGNORE_CVES += CVE-2026-86144" in text, text
    assert any(s["step"] == "declare" and s["ok"] for s in result["journal"]), result["journal"]

    patch = "buildroot-external/patches/libxml2/2.15.3/0001-a.patch"
    with_patch = [g for g in _commit_files(repo) if patch in g[1]]
    assert with_patch, [g[0] for g in _commit_files(repo)]
    assert all(rel in g[1] for g in with_patch), (
        f"a declaration in some later commit is not a declaration of this change: "
        f"{[g[1] for g in with_patch]}")
    assert _dirty(repo) == ""


def test_a_declaration_that_cannot_be_written_stops_the_batch(tmp_path, monkeypatch):
    """The loop may not publish a fix it cannot account for. A declaration that fails
    is a bail, not a warning: publishing anyway is how the tree ends up claiming fixes
    it cannot show."""
    real = A.E.cve_declaration_edit

    def broken(repo, component, carried):
        e = real(repo, component, carried)
        if e is not None:
            e.kind, e.old = "replace", "this text is not in the file"
        return e

    monkeypatch.setattr(A.E, "cve_declaration_edit", broken)
    result, fake, repo, site, state, cfg = _run(tmp_path, [_two_patch_action()])
    assert any(s["step"] == "cve-declaration" and not s["ok"] for s in result["journal"]), \
        [s["step"] for s in result["journal"]]
    assert not result["published"] and not fake.asked("release.sh")
    assert _branch(repo) == "main" and _dirty(repo) == "", \
        "a failed declaration must leave the tree where it started"


def test_a_batch_that_fails_after_declaring_takes_the_declaration_back(tmp_path):
    """The declaration is written before the build. If the build fails, main is restored
    - and a declaration left in the tree is a claim that a CVE is closed by a patch that
    is not there, as well as a dirty tree that makes the next night refuse to run."""
    result, fake, repo, site, state, cfg = _run(tmp_path, [_two_patch_action()],
                                                 fail=["release.sh"])
    assert not result["published"]
    decl = repo / "buildroot-external/patches/cve-declarations.mk"
    assert "LIBXML2_IGNORE_CVES" not in decl.read_text(), decl.read_text()
    assert not (repo / "buildroot-external/patches/libxml2").exists()
    assert _branch(repo) == "main" and _dirty(repo) == ""


def test_a_fix_buildroot_rejected_is_not_declared(tmp_path):
    """The declaration follows the tree, not the plan. An ignore for a fix that never
    landed would report a CVE closed while the image is still exposed - which is a
    worse lie than the one the declaration exists to fix."""
    result, fake, repo, site, state, cfg = _run(tmp_path, [_two_patch_action()],
                                                 patch_fails={"libxml2": ("0002-b.patch",)})
    assert result["published"]
    text = (repo / "buildroot-external/patches/cve-declarations.mk").read_text()
    assert "CVE-2026-86137" in text, "the patch that survived is declared"
    assert "CVE-2026-86144" not in text, "the rejected one is not"
    assert "0002-b.patch" not in text, text
    assert "Still open in libxml2: CVE-2026-86144" in (tmp_path / "state" /
                                                        "notes-batch-2026-10-06.md").read_text()


def test_a_declaration_failure_on_a_later_action_undoes_the_earlier_one(tmp_path, monkeypatch):
    """Two packages, one file. The first declares, the second cannot - and the batch
    stops. The first declaration has to go back out with everything else, or main is
    restored with a claim in the tree for a night that published nothing."""
    real = A.E.cve_declaration_edit

    def broken(repo, component, carried):
        e = real(repo, component, carried)
        if e is not None and component == "libmtp":
            e.kind, e.old = "replace", "this text is not in the file"
        return e

    monkeypatch.setattr(A.E, "cve_declaration_edit", broken)
    actions = [_two_patch_action(),
               _bump_action(component="alsa-ucm-conf", current="1.2.16", target="1.2.16.1"),
               _patch_action(component="libmtp", id="security-libmtp-1.1.23", current="1.1.22",
                             target="1.1.22", closes=["CVE-2026-11111"],
                             cves=[{"cve": "CVE-2026-11111", "priority": "P1", "cvss": 6.0,
                                    "affected": True, "affected_note": "1.1.22 < 1.1.23",
                                    "fix_commit": {"sha": "aaaaaaaaaaaa",
                                                   "host": "github.com", "url": "u"}}],
                             edits=[{"path": "buildroot-external/patches/libmtp/1.1.22/0001-m.patch",
                                     "kind": "create", "new": "From aaaa\n",
                                     "cves": ["CVE-2026-11111"], "fix_url": "u"}])]
    result, fake, repo, site, state, cfg = _run(tmp_path, actions)
    assert any(s["step"] == "cve-declaration" and not s["ok"] for s in result["journal"]), \
        [s["step"] for s in result["journal"]]
    assert not result["published"] and not fake.asked("release.sh")
    decl = repo / "buildroot-external/patches/cve-declarations.mk"
    assert "LIBXML2_IGNORE_CVES" not in decl.read_text(), \
        "the libxml2 declaration was written, then must be taken back"
    assert _branch(repo) == "main" and _dirty(repo) == ""


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
    cli.do_report(args, send=lambda *a, **k: True, today=dt.date(2026, 10, 6))
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


def test_status_reads_a_recovered_batch_as_recovered(tmp_path, capsys):
    """Three failed attempts and then a publish is a working loop, not a blocked one.

    On 2026-10-08 the batch failed three times before 26.10.14 shipped. `status` kept
    naming the last *failure*, so the morning read "last batch published nothing" about
    a release that was already on the testing feed and merged into main.
    """
    args = flow._args(tmp_path)
    flow._fake_repo(tmp_path)
    get, run_cmd, run_pkgstats = flow._fakes(tmp_path)
    cli.observe(args, get=get, run_cmd=run_cmd, run_pkgstats=run_pkgstats)
    cli.do_triage(args, complete=flow.model('{"priority":"P1","reachable":"local",'
                                            '"rationale":"x"}'))
    cli.do_report(args, send=lambda *a, **k: True, today=dt.date(2026, 10, 6))
    (Path(args.state_dir) / "actions.json").write_text(json.dumps({"actions": [
        {"kind": "batch", "date": "2026-10-08", "published": False,
         "error": "release: legal-info failed", "changes": [], "dropped": []},
        {"kind": "batch", "date": "2026-10-08", "published": True, "version": "26.10.14",
         "changes": ["security-gst1-plugins-bad-1.24.13"], "dropped": []}]}))

    result = cli.do_status(args, now=time.time())
    out = capsys.readouterr().out
    assert not any("last batch published nothing" in p for p in result["problems"]), out
    assert "1 published today" in out, out


# --- the shared line every stock-package action carries ----------------------

def _ffmpeg_action(**kw):
    """A second stock package: its own patch file, the same `ensure` on core.config."""
    base = {"id": "security-ffmpeg-6.1.5", "component": "ffmpeg", "current": "6.1.5",
            "target": "6.1.5", "closes": ["CVE-2026-90001"],
            "edits": [{"path": "buildroot-external/patches/ffmpeg/6.1.5/0001-y.patch",
                       "kind": "create", "new": "From 02fc47e\n", "note": "CVE-2026-90001",
                       "cves": ["CVE-2026-90001"],
                       "fix_url": "https://code.ffmpeg.org/FFmpeg/FFmpeg/commit/02fc47e13f90"},
                      {"path": "buildroot-external/configs/layers/core.config",
                       "kind": "ensure", "new": 'BR2_GLOBAL_PATCH_DIR="x"', "note": "dir"}]}
    base.update(kw)
    return _patch_action(**base)


def test_the_same_idempotent_line_on_a_shared_file_is_not_a_conflict(tmp_path):
    """The guard was blocking the whole batch on a line it had already written.

    Every stock-package action carries the same `ensure` of BR2_GLOBAL_PATCH_DIR into
    core.config, so on 2026-10-07 four security actions "conflicted" with each other and
    25 CVE fixes across ffmpeg, gst1-plugins-bad, gst1-plugins-good and libsndfile went
    unpublished. `ensure` writes the line only when it is missing: two of them are one
    change, and there is no order to sequence.
    """
    ok, why = G.batch_conflict([_patch_action(), _ffmpeg_action()])
    assert ok is True, why

    result, fake, repo, *_ = _run(tmp_path, [_patch_action(), _ffmpeg_action()],
                                  cfg=_cfg(key_file=_key(tmp_path)))
    assert "batch_conflict" not in (result.get("error") or ""), result.get("error")
    assert fake.asked("release.sh") == 1, "the batch reaches the release step"


def test_shared_file_with_different_content_is_still_a_human_decision(tmp_path):
    """The relaxation is only for the identical idempotent line. Anything else on the
    same path stays a conflict, including two actions that want the *same* replacement:
    `replace` is not idempotent, so applying it twice is not applying it once."""
    different_text = _ffmpeg_action(edits=[
        {"path": "buildroot-external/configs/layers/core.config", "kind": "ensure",
         "new": 'BR2_GLOBAL_PATCH_DIR="different"'}])
    ok, why = G.batch_conflict([_patch_action(), different_text])
    assert ok is False and "core.config" in why, why

    identical_replace = _bump_action(id="routine-libmtp-1.1.24", target="1.1.24",
                                     edits=[{"path": "buildroot-external/package/libmtp/libmtp.mk",
                                             "kind": "replace",
                                             "old": "LIBMTP_VERSION = 1.1.22",
                                             "new": "LIBMTP_VERSION = 1.1.23",
                                             "note": "the same line, twice"}])
    ok, why = G.batch_conflict([_bump_action(), identical_replace])
    assert ok is False and "libmtp.mk" in why, why

    result, fake, repo, *_ = _run(tmp_path, [_bump_action(), identical_replace],
                                  cfg=_cfg(key_file=_key(tmp_path)))
    assert "batch_conflict" in result["error"]
    assert fake.calls == [], "still refused before a branch, a build or a publish"


def test_a_release_script_that_cannot_be_run_is_refused_before_a_branch(tmp_path):
    """2026-10-08: commit 2d894fba flipped scripts/release.sh from 100755 to 100644.

    The batch discovered it at the last step - after the branch, the edits, four
    pre-flights, three commits and a green gate - as a PermissionError that escaped
    run_batch entirely: no recorded failure, main's working tree never restored, and a
    nightly branch left checked out. A missing exec bit is a precondition, so it is
    guarded before anything is spent. (The runnable case is the one every other batch
    test already publishes through.)
    """
    repo = _repo(tmp_path)
    ok, why = G.release_script_executable(repo)
    assert ok is True, why

    # Committed, the way it actually happened: a mode change in a commit, so the tree
    # is clean and only the guard can catch it.
    (repo / "scripts" / "release.sh").chmod(0o644)
    subprocess.run(["git", "-C", str(repo), "add", "scripts/release.sh"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "mode: drop +x"],
                   check=True)
    ok, why = G.release_script_executable(repo)
    assert ok is False and "not runnable" in why, why
    assert not _dirty(repo), "the tree is clean: this is not a dirty-tree failure"

    site, state = _site(tmp_path), tmp_path / "state"
    cfg = _cfg(key_file=_key(tmp_path))
    fake = Fake((), None, repo)
    result = A.run_batch([_bump_action()], cfg, repo, tmp_path, site, state,
                         apply=True, run=fake, today=TODAY)
    assert "release_script" in (result.get("error") or ""), result.get("error")
    assert fake.calls == [], "refused before a branch, a pre-flight or a build"
    assert _branch(repo) == "main" and not _dirty(repo)


def test_a_failed_batch_takes_every_declaration_back(tmp_path):
    """More than one declaration in a batch: reverting them one edit at a time put the
    earlier ones back.

    Each declaration's `old` is the whole file as it stood when that action wrote it, so
    it already contains the previous package's block. Writing them back in order after a
    bail leaves those blocks in the tree - and an IGNORE_CVES line for a patch that is no
    longer there is worse than no line at all: it tells pkg-stats the CVE is closed.
    On 2026-10-08 a failed release left ffmpeg and gst1-plugins-bad declarations on main.
    """
    rel = "buildroot-external/patches/cve-declarations.mk"
    actions = [_two_patch_action(), _ffmpeg_action()]
    result, fake, repo, *_ = _run(tmp_path, actions, fail=("release.sh",),
                                  cfg=_cfg(key_file=_key(tmp_path)))
    assert not result["published"] and "release" in (result.get("error") or "")
    text = (repo / rel).read_text() if (repo / rel).is_file() else ""
    assert "LIBXML2_IGNORE_CVES" not in text and "FFMPEG_IGNORE_CVES" not in text, text
    assert not _dirty(repo), "the tree is left clean for the next night"


def test_a_leftover_nightly_branch_from_a_failed_night_does_not_block_the_next(tmp_path):
    """2026-10-08: the first batch died at the release step and left
    nightly/2026-10-08-batch behind. `checkout -b` then refused the retry - "a branch
    named ... already exists" - so a night that published nothing blocked the same
    night's second attempt, and the fix had to come from a human deleting a branch.

    The branch is the loop's own scratch space for today's batch. Retrying means
    starting that branch from main again, which is what `checkout -B` does.
    """
    repo, site, state = _repo(tmp_path), _site(tmp_path), tmp_path / "state"
    cfg = _cfg(key_file=_key(tmp_path))
    result = A.run_batch([_bump_action()], cfg, repo, tmp_path, site, state,
                         apply=True, run=Fake(("release.sh",), None, repo), today=TODAY)
    assert not result["published"]
    branches = subprocess.run(["git", "-C", str(repo), "branch", "--list"],
                              capture_output=True, text=True).stdout
    assert "nightly/2026-10-06-batch" in branches, "the failed night left its branch behind"
    assert _branch(repo) == "main" and not _dirty(repo)

    fake = Fake((), None, repo)
    result = A.run_batch([_bump_action()], cfg, repo, tmp_path, site, state,
                         apply=True, run=fake, today=TODAY)
    assert result["published"], "the same night may be retried after a failure"
    assert fake.asked("release.sh") == 1


def test_a_batch_that_fails_after_the_preflight_wipes_the_trees_it_patched(tmp_path):
    """legal-info copies every patch named in `build/<pkg>/.applied_patches_list`.

    A pre-flight puts the patches this batch wrote into that list. If the batch then
    bails, those files are gone from the tree while the build dir still names them, and
    the *next* build dies with `cp: cannot stat <patch>` during legal-info - on a package
    that is not even in the batch. That is how 2026-10-08 failed twice: ffmpeg's legal-info
    reached for patches the reverted batch had taken back.
    """
    result, fake, repo, *_ = _run(tmp_path, [_two_patch_action(), _bump_action()],
                                  fail=("release.sh",), cfg=_cfg(key_file=_key(tmp_path)))
    assert not result["published"]
    for pkg in ("libxml2", "libmtp"):
        steps = [c[-1] for c in fake.calls if any(f"{pkg}-" in str(a) for a in c)]
        assert steps and steps[-1] == f"{pkg}-dirclean", \
            f"{pkg}'s build tree still names patches the bail deleted: {steps}"


# --- what the filter skipped is what the notes must name ---------------------

def test_skipped_known_bad_fixes_are_named_in_the_batch_notes(tmp_path):
    """A CVE whose only named fix is already proved bad is not carried — but it is not
    forgotten either: the notes a machine and the operator read must say it is open,
    which fix closed the door, and when that was proved."""
    skipped = [{"cve": "CVE-2026-18477",
                "fix": "https://github.com/GNOME/libxml2/commit/aaaaaaaaaaaa",
                "recorded": "2026-10-08", "failed_on": "parser.c"}]
    actions = [_patch_action(still_open=["CVE-2026-18477"], skipped_known_bad=skipped)]
    _run(tmp_path, actions, cfg=_cfg(key_file=_key(tmp_path)))
    notes = (tmp_path / "state" / "notes-batch-2026-10-06.md").read_text()
    assert "1 fix(es) skipped as known-bad" in notes, notes
    assert ("Still open in libxml2: CVE-2026-18477 — NVD's fix "
            "https://github.com/GNOME/libxml2/commit/aaaaaaaaaaaa proved not to apply on "
            "2026-10-08 (failed on parser.c)") in notes, notes


def test_a_known_bad_fix_does_not_steal_the_no_fix_reason(tmp_path):
    """'NVD names no fix' and 'NVD's fix is already proved bad' are different problems
    and the next reader needs to know which one it has."""
    actions = [_patch_action(still_open=["CVE-2026-18477", "CVE-2026-8631"],
                             skipped_known_bad=[{"cve": "CVE-2026-18477",
                                                 "fix": "https://x/commit/aaaaaaaaaaaa",
                                                 "recorded": "2026-10-08",
                                                 "failed_on": "parser.c"}])]
    _run(tmp_path, actions, cfg=_cfg(key_file=_key(tmp_path)))
    notes = (tmp_path / "state" / "notes-batch-2026-10-06.md").read_text()
    assert "Still open in libxml2: CVE-2026-8631 (NVD names no upstream fix" in notes, notes
    assert "CVE-2026-18477, CVE-2026-8631" not in notes, "not lumped into one wrong reason"


def test_a_recorded_failure_names_the_version_it_was_proved_against(tmp_path):
    """Without the pin, 'does not apply to 2.15.3' is a permanent block: the day the pin
    moves, the verdict still binds a version it was never tested against."""
    actions = [_two_patch_action()]
    result, fake, repo, site, state, cfg = _run(tmp_path, actions,
                                                patch_fails={"libxml2": ("0002-b.patch",)},
                                                cfg=_cfg(key_file=_key(tmp_path)))
    st = {"cves": {}, "findings": {}, "actions": {}, "patch_failures": {}}
    A.record_batch(result, actions, state, st, TODAY, run=fake, repo=repo)
    assert st["patch_failures"]["libxml2"]["version"] == "2.15.3"
    assert G.known_bad_fixes("libxml2", st, "2.15.3") == {
        "https://github.com/GNOME/libxml2/commit/aaaaaaaaaaaa"}
    assert G.known_bad_fixes("libxml2", st, "2.15.4") == set(), \
        "a new pin is a new question, not the old answer"
