"""Tests for scripts/push-site.sh, the retrying push at the end of release.sh --push.

On 2026-10-30 miniserv rebooted in the middle of the act lane's push, rsync exited
255, and 26.10.50 was signed in the local site but missing from the live feed. The
push now retries; these tests drive it with a fake rsync that fails a set number
of times.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "push-site.sh"


def run(tmp_path: Path, failures: int, tries: int = 3, args=("/site",)):
    """push-site.sh with an rsync that fails `failures` times, then succeeds."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    rsync = bin_dir / "rsync"
    rsync.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        f'[ "$(wc -l < "{calls}")" -gt {failures} ] || exit 255\n')
    os.chmod(rsync, 0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "PENGYOS_PUSH_TRIES": str(tries), "PENGYOS_PUSH_WAIT": "0"}
    p = subprocess.run([str(SCRIPT), *args, "host:path/"], env=env,
                       capture_output=True, text=True)
    lines = calls.read_text().splitlines() if calls.exists() else []
    return p, lines


def test_first_try_pushes_once(tmp_path):
    p, calls = run(tmp_path, failures=0)
    assert p.returncode == 0, p.stderr
    assert calls == ["-a /site/update /site/download host:path/"]


def test_host_away_then_back_is_retried(tmp_path):
    p, calls = run(tmp_path, failures=2)
    assert p.returncode == 0, p.stderr
    assert len(calls) == 3
    assert "attempt 2/3 failed" in p.stderr


def test_gives_up_after_tries_and_says_where_the_release_is(tmp_path):
    p, calls = run(tmp_path, failures=10)
    assert p.returncode == 1
    assert len(calls) == 3
    assert "push failed after 3 attempts" in p.stderr
    assert "published in /site but not on host:path/" in p.stderr


def test_release_sh_pushes_through_it():
    text = (REPO / "scripts" / "release.sh").read_text()
    assert '"${REPO}/scripts/push-site.sh" "${SITE}" "${PENGYOS_SITE_REMOTE}"' in text
    assert "rsync -a" not in text, "release.sh pushes around the retry"


def _site(tmp_path: Path) -> Path:
    site = tmp_path / "site"
    (site / "update").mkdir(parents=True)
    (site / "download").mkdir()
    (site / "update" / "feed.xml").write_text("<feed/>\n")
    (site / "download" / "index.html").write_text("<html/>\n")
    return site


def test_delete_mirrors_what_prune_removed(tmp_path):
    """After `pengyos-release prune --apply` the server must drop the pruned releases
    too; plain -a never deletes, so they stayed on the server (2026-11-03)."""
    site = _site(tmp_path)
    p, calls = run(tmp_path, failures=0, args=("--delete", str(site)))
    assert p.returncode == 0, p.stderr
    assert calls == [f"-a --delete-after {site}/update {site}/download host:path/"]


def test_delete_is_retried_like_any_push(tmp_path):
    site = _site(tmp_path)
    p, calls = run(tmp_path, failures=1, args=("--delete", str(site)))
    assert p.returncode == 0 and len(calls) == 2
    assert all("--delete-after" in c for c in calls)


def test_delete_refuses_a_site_that_is_not_one(tmp_path):
    """A wrong or empty SITE with --delete would empty the live site."""
    empty = tmp_path / "empty"
    (empty / "update").mkdir(parents=True)
    p, calls = run(tmp_path, failures=0, args=("--delete", str(empty)))
    assert p.returncode == 2 and calls == []
    assert "--delete refused" in p.stderr


def test_without_the_flag_nothing_is_deleted(tmp_path):
    p, calls = run(tmp_path, failures=0)
    assert "--delete" not in calls[0]
