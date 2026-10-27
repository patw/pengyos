"""Tests for the gate's fork-budget check (scripts/pengyos_gate.py).

Why the check exists, both halves found 2026-10-19:

1. `divergence.toml` opens with "Budget: at most 10 maintained forks" and stated, in the
   same block, that the gate had no fork-count check. The budget was counted by hand on a
   day the count was about to move from 1 to 3 (openssh landed, ffmpeg and hplip queued).

2. A fork is invisible to `pkg-stats` — it scans the stock recipe and the stock recipe is
   switched off. The ledger requires an `observe_source` for exactly that reason (the
   PengyR case: images shipped 1.10.0 while upstream was at 1.11.1 and nothing noticed),
   but `check_ledger_covers_patch_sets` only checks the field is non-empty. A fork naming
   `observe_source = "pkg-stats"` passes that and is still blind. OpenSSH nearly shipped
   the sibling of that: the fork's CPE product was `openssh-pengyos`, which matches nothing
   in NVD, so pkg-stats reported zero CVEs for a package carrying OpenSSH.

Every check gets a positive control (the shape that must pass) and a negative control
(the exact defect, reproduced). Run: python3 -m pytest scripts/tests -q  (from repo root)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pengyos_gate as gate  # noqa: E402

RECIPE = "buildroot-external/package/openssh-pengyos"


def fork_entry(fid: str, component: str, *, recipe: str = "", observe: str = "") -> str:
    lines = [
        "[[divergence]]",
        f'id = "{fid}"',
        'kind = "fork"',
        f'component = "{component}"',
        'pinned_version = "10.6p1"',
        'carried_since = "2026-10-19"',
        'expiry = "Buildroot releases with openssh >= 10.6p1: delete the recipe"',
        f'observe_source = "{observe}"' if observe else 'observe_source = ""',
        'upstream_status = "released"',
    ]
    if recipe:
        lines.append(f'recipe = "{recipe}"')
    return "\n".join(lines) + "\n"


def make_repo(tmp_path: Path, entries: list[str], *, budget: int | None = None,
              recipes: tuple[str, ...] = (RECIPE,)) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    for r in recipes:
        (repo / r).mkdir(parents=True, exist_ok=True)
    head = 'schema = 1\n'
    if budget is not None:
        head += f"fork_budget = {budget}\n"
    (repo / "divergence.toml").write_text(head + "\n" + "\n\n".join(entries))
    return repo


def budget_check(repo: Path):
    return next(c for c in gate.check_fork_budget(repo) if c.name == "fork-budget")


def obs_checks(repo: Path) -> list:
    return [c for c in gate.check_fork_budget(repo) if c.name.startswith("fork-observable")]


GOOD = fork_entry("openssh-10.6p1-fork", "openssh-pengyos", recipe=RECIPE,
                  observe="pengyos-nightly observe, source id openssh-fork")


# --- the budget -----------------------------------------------------------------------

def test_fork_under_budget_passes(tmp_path):
    """Positive control: 1 fork, budget 10 — the state the repo is in today."""
    repo = make_repo(tmp_path, [GOOD], budget=10)
    c = budget_check(repo)
    assert c.ok, c.detail
    assert "1 of 10" in c.detail and "openssh-10.6p1-fork" in c.detail


def test_fork_count_over_budget_fails(tmp_path):
    """The defect: three forks against a budget of two, and nothing saying so."""
    entries = [
        GOOD,
        fork_entry("ffmpeg-9.0.2-fork", "ffmpeg-pengyos",
                   recipe="buildroot-external/package/ffmpeg-pengyos",
                   observe="pengyos-nightly observe, source id ffmpeg-fork"),
        fork_entry("hplip-3.26.4-fork", "hplip-pengyos",
                   recipe="buildroot-external/package/hplip-pengyos",
                   observe="pengyos-nightly observe, source id hplip-fork"),
    ]
    repo = make_repo(tmp_path, entries, budget=2,
                     recipes=(RECIPE, "buildroot-external/package/ffmpeg-pengyos",
                              "buildroot-external/package/hplip-pengyos"))
    c = budget_check(repo)
    assert not c.ok, "3 forks against a budget of 2 must fail the gate"
    assert "3 maintained forks, budget is 2" in c.detail


def test_fork_count_exactly_at_budget_passes(tmp_path):
    """The boundary: at the budget is allowed, past it is not."""
    entries = [
        GOOD,
        fork_entry("ffmpeg-9.0.2-fork", "ffmpeg-pengyos",
                   recipe="buildroot-external/package/ffmpeg-pengyos",
                   observe="pengyos-nightly observe, source id ffmpeg-fork"),
    ]
    repo = make_repo(tmp_path, entries, budget=2,
                     recipes=(RECIPE, "buildroot-external/package/ffmpeg-pengyos"))
    assert budget_check(repo).ok, budget_check(repo).detail


def test_budget_defaults_when_the_ledger_states_it_in_prose_only(tmp_path):
    """A ledger with no `fork_budget` key is the shape the repo had until today."""
    repo = make_repo(tmp_path, [GOOD], budget=None)
    c = budget_check(repo)
    assert c.ok and f"1 of {gate.DEFAULT_FORK_BUDGET}" in c.detail, c.detail


def test_patch_sets_are_not_counted_as_forks(tmp_path):
    """The budget is about forks. A patch set is a divergence of a different kind."""
    entry = fork_entry("openssh-10.6p1-fork", "openssh-pengyos", recipe=RECIPE,
                       observe="pengyos-nightly observe, source id openssh-fork")
    patchset = ('[[divergence]]\nid = "libxml2-2.15.3-cve-2026"\nkind = "patch-set"\n'
                'component = "libxml2"\ncarried_since = "2026-10-05"\n'
                'expiry = "Buildroot pins libxml2 >= 2.15.4"\nobserve_source = "pkg-stats"\n'
                'upstream_status = "not-offered"\n'
                'patches_dir = "buildroot-external/patches/libxml2/2.15.3"\n')
    repo = make_repo(tmp_path, [entry, patchset], budget=10)
    c = budget_check(repo)
    assert c.ok and "1 of 10" in c.detail, c.detail


def test_unreadable_ledger_is_a_failure_not_a_pass(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "divergence.toml").write_text("this is not [ toml")
    checks = gate.check_fork_budget(repo)
    assert len(checks) == 1 and not checks[0].ok
    assert "unreadable" in checks[0].detail


# --- a fork must be visible to something ----------------------------------------------

def test_fork_with_its_own_observe_source_passes(tmp_path):
    """Positive control: the shape the openssh fork has today."""
    repo = make_repo(tmp_path, [GOOD], budget=10)
    checks = obs_checks(repo)
    assert len(checks) == 1 and checks[0].ok, [c.detail for c in checks]


def test_fork_observed_by_pkg_stats_fails(tmp_path):
    """The blind fork: pkg-stats scans the stock recipe, and the stock recipe is off.

    This is the failure the ledger's `observe_source` rule exists to prevent, and the
    non-empty check in check_ledger_covers_patch_sets lets it through.
    """
    repo = make_repo(tmp_path, [fork_entry("openssh-10.6p1-fork", "openssh-pengyos",
                                           recipe=RECIPE, observe="pkg-stats")], budget=10)
    checks = obs_checks(repo)
    assert len(checks) == 1 and not checks[0].ok, [c.detail for c in checks]
    assert "cannot see a fork" in checks[0].detail


def test_authored_fork_pkg_stats_tracks_passes_with_its_project(tmp_path):
    """distrobox and xdg-utils (2026-10-27): Buildroot has no recipe for them, so ours is
    the only recipe and pkg-stats looks it up on release-monitoring.org by name. That is
    a real observer, but only as a stated, checkable claim: the project id."""
    recipe = "buildroot-external/package/distrobox"
    entry = fork_entry("distrobox-1.8.2.5-fork", "distrobox", recipe=recipe,
                       observe="pkg-stats") + "release_monitoring_id = 242098\n"
    repo = make_repo(tmp_path, [entry], budget=10, recipes=(recipe,))
    checks = obs_checks(repo)
    assert len(checks) == 1 and checks[0].ok, [c.detail for c in checks]
    assert "242098" in checks[0].detail


def test_a_release_monitoring_id_must_be_a_real_project(tmp_path):
    """A string, zero or a blank does not count: the check must not become a phrase to
    paste past the openssh failure."""
    for bad in ('"242098"', "0", '""'):
        entry = fork_entry("openssh-10.6p1-fork", "openssh-pengyos", recipe=RECIPE,
                           observe="pkg-stats") + f"release_monitoring_id = {bad}\n"
        repo = make_repo(tmp_path / bad.strip('"') if bad.strip('"') else tmp_path / "blank",
                         [entry], budget=10)
        checks = obs_checks(repo)
        assert len(checks) == 1 and not checks[0].ok, (bad, checks[0].detail)
        assert "release_monitoring_id" in checks[0].detail


def test_build_fixes_are_not_counted_as_forks(tmp_path):
    """A build-fix is ledgered (expiry, observer) but is packaging, not a fork."""
    fix = ('[[divergence]]\nid = "appstream-1.2.1-build-fix"\nkind = "build-fix"\n'
           'component = "appstream"\ncarried_since = "2026-10-02"\n'
           'expiry = "a release cross-builds data/"\nobserve_source = "pkg-stats"\n'
           'upstream_status = "not-offered"\nrecipe = "buildroot-external/package/appstream"\n')
    repo = make_repo(tmp_path, [GOOD, fix], budget=10)
    assert "1 of 10" in budget_check(repo).detail
    assert len(obs_checks(repo)) == 1


def test_fork_with_no_observe_source_fails(tmp_path):
    repo = make_repo(tmp_path, [fork_entry("openssh-10.6p1-fork", "openssh-pengyos",
                                           recipe=RECIPE, observe="")], budget=10)
    checks = obs_checks(repo)
    assert len(checks) == 1 and not checks[0].ok, [c.detail for c in checks]
    assert "no observe_source" in checks[0].detail


def test_fork_naming_no_recipe_fails(tmp_path):
    """No recipe means no pin, so the observe source compares a version nothing builds."""
    repo = make_repo(tmp_path, [fork_entry("openssh-10.6p1-fork", "openssh-pengyos",
                                           observe="pengyos-nightly observe, source id openssh-fork")],
                     budget=10)
    checks = obs_checks(repo)
    assert len(checks) == 1 and not checks[0].ok, [c.detail for c in checks]
    assert "must name `recipe`" in checks[0].detail


def test_fork_naming_a_recipe_that_is_not_in_the_tree_fails(tmp_path):
    """The observe source reads <recipe>/<name>.mk. A path that is not there has no version."""
    repo = make_repo(tmp_path, [GOOD], budget=10, recipes=())  # recipe dir absent
    checks = obs_checks(repo)
    assert len(checks) == 1 and not checks[0].ok, [c.detail for c in checks]
    assert "does not exist in the tree" in checks[0].detail


def test_patch_set_may_name_pkg_stats_as_its_observer(tmp_path):
    """Negative control on the negative control: a patch set IS visible to pkg-stats.

    The rule is about forks. Applying it to patch sets would fail the six sets the repo
    carries today, all of which are correctly observed by pkg-stats.
    """
    patchset = ('[[divergence]]\nid = "libxml2-2.15.3-cve-2026"\nkind = "patch-set"\n'
                'component = "libxml2"\ncarried_since = "2026-10-05"\n'
                'expiry = "Buildroot pins libxml2 >= 2.15.4"\nobserve_source = "pkg-stats"\n'
                'upstream_status = "not-offered"\n'
                'patches_dir = "buildroot-external/patches/libxml2/2.15.3"\n')
    repo = make_repo(tmp_path, [patchset], budget=10)
    assert obs_checks(repo) == [], "a patch set must not be held to the fork rule"


# --- the live ledger ------------------------------------------------------------------

def test_the_real_ledger_passes_the_new_check():
    """The repo as it stands must pass: every fork within budget and observable.

    This is the check that would have caught the openssh fork's first shape, and the one
    that will fail the day ffmpeg and hplip push the count past a stated budget.
    """
    repo = Path(__file__).resolve().parents[2]
    checks = gate.check_fork_budget(repo)
    failed = [c for c in checks if not c.ok]
    assert not failed, [f"{c.name}: {c.detail}" for c in failed]
    assert any(c.name == "fork-budget" for c in checks)
