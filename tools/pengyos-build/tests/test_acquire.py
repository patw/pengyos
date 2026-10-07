"""Tests for Buildroot acquisition (clone blobless + pinned checkout, idempotent re-entry).

The default runner shells out to real git, but every unit test here injects a
:class:`FakeGit` runner — an in-memory model of the git commands
``acquire_buildroot`` issues — so no network, git binary, or root is needed.
A PENGYOS_IT=1 integration test drives the real subprocess runner against a
local file:// remote (still no network).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pengyos_build.acquire import (
    BUILDROOT_REMOTE,
    BUILD_ROOT_ENV,
    AcquireError,
    acquire_buildroot,
    acquire_target_tree,
    buildroot_tag,
    default_build_root,
    source_dir,
)
from pengyos_build.targets import parse_targets

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
TAG_A = "2025.02.9"
TAG_B = "2026.02.3"
DEFAULT_BRANCH = "master"


class FakeGit:
    """In-memory model of the git commands used by :mod:`acquire`.

    State mirrors what the real commands would do:
    - ``clone --no-checkout`` creates a repo whose HEAD is the default-branch
      commit and no local tags;
    - ``fetch --tags`` copies the remote tags into the local tag table;
    - ``checkout <tag>`` detaches HEAD at the tag's commit.
    """

    def __init__(self, remote_tags: dict[str, str] | None = None):
        self.remote_tags = {TAG_A: COMMIT_A, TAG_B: COMMIT_B}
        if remote_tags is not None:
            self.remote_tags.update(remote_tags)
        self.head = {"master": COMMIT_A}  # branch -> commit
        self.local_tags: dict[str, str] = {}
        self.repos: set[Path] = set()
        self.calls: list[tuple[str, ...]] = []
        self.fail_next: str | None = None  # argv prefix to fail with

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        argv = tuple(argv)
        self.calls.append(argv)
        assert argv[0] == "git"
        args = self._args(argv)
        dest: Path | None = None
        if "-C" in argv:
            dest = Path(argv[argv.index("-C") + 1])
        want = tuple(self.fail_next.split()) if self.fail_next else None
        if want and tuple(argv[: len(want)]) == want:
            return subprocess.CompletedProcess(
                argv, 128, stdout="", stderr=f"fake: forced failure in {self.fail_next}"
            )

        if args[0] == "clone":
            assert "--filter=blob:none" in args
            assert "--no-checkout" in args
            dest = Path(args[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()
            self.repos.add(dest)
            # A clone (even --no-checkout) does transfer the remote tags.
            self.local_tags.update(self.remote_tags)
            return self._ok()
        if dest is None:
            raise AssertionError(f"unexpected git invocation: {argv}")
        if dest not in self.repos:
            return subprocess.CompletedProcess(argv, 128, "", "fatal: not a git repository")
        if args[0] == "rev-parse":
            return self._rev_parse(argv)
        if args[0] == "fetch":
            self.local_tags.update(self.remote_tags)
            return self._ok()
        if args[0] == "checkout":
            tag = args[-1]
            if tag not in self.local_tags:
                return subprocess.CompletedProcess(
                    argv, 1, "", f"error: pathspec '{tag}' did not match any file"
                )
            self.head["HEAD"] = self.local_tags[tag]
            return self._ok()
        raise AssertionError(f"unexpected git invocation: {argv}")

    def _args(self, argv: tuple[str, ...]) -> list[str]:
        """argv minus git, dropping the ``-C <dir>`` pair entirely."""
        args: list[str] = []
        skip = False
        for a in argv[1:]:
            if skip:
                skip = False
                continue
            if a == "-C":
                skip = True
            else:
                args.append(a)
        return args

    def _rev_parse(self, argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        args = self._args(argv)
        if "--verify" in args:
            ref = args[-1]
            assert ref.startswith("refs/tags/") and ref.endswith("^{commit}")
            tag = ref[len("refs/tags/") : -len("^{commit}")]
            if tag in self.local_tags:
                return self._ok(stdout=self.local_tags[tag] + "\n")
            return subprocess.CompletedProcess(argv, 1, "", "")
        if args[-1] == "HEAD":
            commit = self.head.get("HEAD") or self.head[DEFAULT_BRANCH]
            return self._ok(stdout=commit + "\n")
        raise AssertionError(f"unexpected rev-parse: {argv}")

    def _ok(self, stdout: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess((), 0, stdout=stdout, stderr="")

    def head_commit(self, dest: Path) -> str:
        return self.head.get("HEAD", self.head[DEFAULT_BRANCH])


# ---------------------------------------------------------------- golden argv


def test_buildroot_tag_is_bare_version():
    assert buildroot_tag("2026.02.3") == "2026.02.3"
    assert buildroot_tag("2026.02") == "2026.02"


def test_source_dir_is_per_version():
    assert source_dir("/x", "2026.02.3") == Path("/x/buildroot-2026.02.3")
    assert source_dir("/x", "2025.02.9") == Path("/x/buildroot-2025.02.9")


def test_default_build_root_env_override():
    assert default_build_root({BUILD_ROOT_ENV: "/srv/br"}) == Path("/srv/br")
    assert default_build_root({}) == Path.home() / "build" / "pengyos"


# ---------------------------------------------------------------- fresh clone


def test_fresh_clone_argv_is_golden(tmp_path):
    git = FakeGit()
    dest = tmp_path / "deep" / "buildroot-2026.02.3"
    result = acquire_buildroot(TAG_B, dest, remote="git://example/br.git", runner=git)

    assert result.commands == (
        ("git", "clone", "--filter=blob:none", "--no-checkout", "git://example/br.git", str(dest)),
        ("git", "-C", str(dest), "checkout", TAG_B),
    )
    assert result.action == "cloned"
    assert result.commit == COMMIT_B
    assert result.tag == TAG_B
    assert result.path == dest
    assert (dest / ".git").is_dir()
    assert f"{result.action}:" in str(result)


def test_fresh_clone_uses_upstream_remote_by_default(tmp_path):
    git = FakeGit()
    acquire_buildroot(TAG_A, tmp_path / "br", runner=git)
    clone = git.calls[0]
    assert clone[-2] == BUILDROOT_REMOTE


def test_result_str_summarizes_action_path_tag(tmp_path):
    git = FakeGit()
    result = acquire_buildroot(TAG_A, tmp_path / "br", runner=git)
    assert str(result) == f"cloned: {tmp_path / 'br'} @ {TAG_A} ({COMMIT_A[:12]})"


# ---------------------------------------------------------- idempotent re-entry


def test_reentry_up_to_date_runs_no_commands(tmp_path):
    git = FakeGit()
    dest = tmp_path / "br"
    first = acquire_buildroot(TAG_B, dest, runner=git)
    calls_before = len(git.calls)

    second = acquire_buildroot(TAG_B, dest, runner=git)

    assert second.action == "up-to-date"
    assert second.commands == ()
    assert second.commit == first.commit
    # Only read-only probes may run; no clone/checkout/fetch.
    for argv in git.calls[calls_before:]:
        assert argv[1] != "clone"
        assert "checkout" not in argv
        assert "fetch" not in argv


def test_reentry_wrong_commit_checks_out_tag(tmp_path):
    git = FakeGit()
    dest = tmp_path / "br"
    acquire_buildroot(TAG_B, dest, runner=git)
    git.local_tags = {TAG_A: COMMIT_A, TAG_B: COMMIT_B}
    git.head["HEAD"] = git.head[DEFAULT_BRANCH]  # someone checked out master

    result = acquire_buildroot(TAG_B, dest, runner=git)

    assert result.action == "checked-out"
    assert result.commands == (("git", "-C", str(dest), "checkout", TAG_B),)
    assert result.commit == COMMIT_B
    assert git.head_commit(dest) == COMMIT_B


def test_reentry_unknown_tag_fetches_then_checks_out(tmp_path):
    git = FakeGit()
    dest = tmp_path / "br"
    # A tree that predates the 2026.02.3 tag (only 2025.02.9 known locally).
    acquire_buildroot(TAG_A, dest, runner=git)
    git.local_tags = {TAG_A: COMMIT_A}

    result = acquire_buildroot(TAG_B, dest, runner=git)

    assert result.action == "fetched-checked-out"
    assert result.commands == (
        ("git", "-C", str(dest), "fetch", "--filter=blob:none", "--tags", BUILDROOT_REMOTE),
        ("git", "-C", str(dest), "checkout", TAG_B),
    )
    assert result.commit == COMMIT_B
    assert git.head_commit(dest) == COMMIT_B


# ------------------------------------------------------------------- failures


def test_existing_non_git_directory_is_an_error(tmp_path):
    dest = tmp_path / "br"
    dest.mkdir()
    with pytest.raises(AcquireError, match=r"not a git repository.*refusing to overwrite"):
        acquire_buildroot(TAG_A, dest, runner=FakeGit())
    # And the data is untouched.
    assert list(dest.iterdir()) == []


def test_existing_plain_file_is_an_error(tmp_path):
    dest = tmp_path / "br"
    dest.write_text("precious")
    with pytest.raises(AcquireError, match=r"is not a directory"):
        acquire_buildroot(TAG_A, dest, runner=FakeGit())
    assert dest.read_text() == "precious"


def test_git_failure_surfaces_stderr(tmp_path):
    git = FakeGit()
    git.fail_next = "git clone"
    with pytest.raises(AcquireError) as excinfo:
        acquire_buildroot(TAG_A, tmp_path / "br", runner=git)
    message = str(excinfo.value)
    assert "exit 128" in message
    assert "fake: forced failure" in message
    assert "git clone" in message


def test_head_not_at_tag_after_checkout_is_an_error(tmp_path):
    class RogueGit(FakeGit):
        def __call__(self, argv):
            proc = super().__call__(argv)
            if "checkout" in argv:
                # Simulate a checkout that silently failed to move HEAD.
                del self.head["HEAD"]
            return proc

    git = RogueGit()
    with pytest.raises(AcquireError, match=r"not at the pinned tag"):
        acquire_buildroot(TAG_B, tmp_path / "br", runner=git)


# -------------------------------------------------------------------- targets


def test_acquire_target_tree_uses_target_br_version(tmp_path):
    targets = parse_targets(
        {
            "targets": [
                {
                    "name": "demo",
                    "br_version": TAG_B,
                    "external_tree": "ext",
                    "defconfig": "pengyos_server_defconfig",
                }
            ]
        }
    )
    git = FakeGit()
    result = acquire_target_tree(targets[0], tmp_path, runner=git)
    assert result.path == tmp_path / "buildroot-2026.02.3"
    assert result.tag == TAG_B


def test_acquire_target_tree_defaults_to_build_root_env(tmp_path, monkeypatch):
    monkeypatch.setenv(BUILD_ROOT_ENV, str(tmp_path))
    targets = parse_targets(
        {
            "targets": [
                {
                    "name": "demo",
                    "br_version": TAG_B,
                    "external_tree": "ext",
                    "defconfig": "pengyos_server_defconfig",
                }
            ]
        }
    )
    git = FakeGit()
    result = acquire_target_tree(targets[0], runner=git)
    assert result.path == tmp_path / "buildroot-2026.02.3"


def test_different_versions_get_different_trees(tmp_path):
    git = FakeGit()
    first = acquire_target_tree_for_version(tmp_path, TAG_A, git)
    second = acquire_target_tree_for_version(tmp_path, TAG_B, git)
    assert first.path != second.path
    assert first.path.name == "buildroot-2025.02.9"
    assert second.path.name == "buildroot-2026.02.3"


def acquire_target_tree_for_version(tmp_path, version, git):
    targets = parse_targets(
        {
            "targets": [
                {
                    "name": "demo",
                    "br_version": version,
                    "external_tree": "ext",
                    "defconfig": "pengyos_server_defconfig",
                }
            ]
        }
    )
    return acquire_target_tree(targets[0], tmp_path, runner=git)


# ------------------------------------------------------ real git (opt-in IT)


pytestmark_it = pytest.mark.skipif(
    __import__("os").environ.get("PENGYOS_IT") != "1",
    reason="real-git integration test; enable with PENGYOS_IT=1",
)


@pytestmark_it
def test_it_real_git_local_remote(tmp_path):
    """Blobless clone + pin + idempotent re-entry against a file:// remote."""
    import shutil
    import subprocess as sp

    if shutil.which("git") is None:
        pytest.skip("git binary not available")

    remote = tmp_path / "upstream.git"
    sp.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    seed = tmp_path / "seed"
    sp.run(["git", "init", "-q", str(seed)], check=True)
    sp.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
    sp.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    (seed / "file.txt").write_text("v1\n")
    sp.run(["git", "-C", str(seed), "add", "."], check=True)
    sp.run(
        ["git", "-C", str(seed), "commit", "-q", "-m", "v1"],
        check=True,
    )
    sp.run(["git", "-C", str(seed), "tag", TAG_A], check=True)
    (seed / "file.txt").write_text("v2\n")
    sp.run(["git", "-C", str(seed), "commit", "-q", "-am", "v2"], check=True)
    sp.run(["git", "-C", str(seed), "tag", TAG_B], check=True)
    sp.run(["git", "-C", str(seed), "remote", "add", "origin", str(remote)], check=True)
    sp.run(
        ["git", "-C", str(seed), "push", "-q", "origin", "HEAD:master", "--tags"],
        check=True,
    )

    dest = tmp_path / "work" / "buildroot-2026.02.3"
    first = acquire_buildroot(TAG_B, dest, remote=str(remote))
    assert first.action == "cloned"
    assert (dest / "file.txt").read_text() == "v2\n"

    second = acquire_buildroot(TAG_B, dest, remote=str(remote))
    assert second.action == "up-to-date"
    assert second.commit == first.commit
