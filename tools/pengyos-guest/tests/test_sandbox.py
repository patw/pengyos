"""The no-host-writes sandbox guarantee (spec: "no host writes outside the
guest's home mount and the exports you declare").

These tests assert the guarantee two ways:

- Directly, against :mod:`pengyos_guest.sandbox` — the allowlist builder, the
  argv auditor, and the pre-execution :class:`SandboxGuard`.
- End-to-end, through the full guest lifecycle (apply / enter / export /
  destroy): every argv handed to the runner across the whole lifecycle is
  scanned, and no undeclared host path ever appears.

Everything runs through the mockable :class:`FakeRunner`, so no real
podman/distrobox, network, root, or display is needed.
"""

from __future__ import annotations

import pytest

from pengyos_guest.config import Guest
from pengyos_guest.engines import GuestManager
from pengyos_guest.runner import FakeRunner, RunResult
from pengyos_guest.sandbox import (
    SandboxError,
    SandboxGuard,
    declared_host_paths,
    is_sandboxed,
    undeclared_host_paths,
)

WOLFI = Guest(
    name="wolfi",
    image="quay.io/aliyunos/wolfi:latest",
    packages=["git", "curl"],
    exports={"git-editor": "/var/lib/distrobox/bin/git-editor"},
    mounts=["/home/alex/projects:/projects:rw"],
)
NIXOS = Guest(
    name="nixos",
    image="ghcr.io/pengyos/nixos:latest",
    packages=["ripgrep"],
    exports={"rg": "/usr/bin/rg"},
    mounts=["/home/alex/data:/data:ro"],
)


# -- declared_host_paths ----------------------------------------------------


def test_declared_host_paths_collects_mounts_from_all_guests():
    assert declared_host_paths([WOLFI, NIXOS]) == {
        "/home/alex/projects:/projects:rw",
        "/home/alex/data:/data:ro",
    }


def test_declared_host_paths_empty_for_guest_without_mounts():
    assert declared_host_paths([Guest(name="bare", image="wolfi:latest")]) == set()


def test_declared_host_paths_is_deduplicated():
    same = Guest(name="a", image="i", mounts=["/h/g:rw"])
    other = Guest(name="b", image="i", mounts=["/h/g:rw"])
    assert len(declared_host_paths([same, other])) == 1


# -- undeclared_host_paths --------------------------------------------------


def test_undeclared_host_paths_finds_offenders():
    argvs = [
        ["distrobox", "create", "--mount", "/home/alex/projects:/projects:rw"],
        ["distrobox", "enter", "wolfi", "--", "apk", "add", "/etc/evil"],
    ]
    assert undeclared_host_paths(argvs, ["/home/alex/projects:/projects:rw"]) == [
        "/etc/evil",
    ]


def test_undeclared_host_paths_allows_declared_paths():
    declared = ["/home/alex/projects:/projects:rw"]
    argvs = [["distrobox", "create", "--mount", "/home/alex/projects:/projects:rw"]]
    assert undeclared_host_paths(argvs, declared) == []


def test_undeclared_host_paths_ignores_non_path_tokens():
    # Image refs, guest names, package names, and flags never start with "/".
    argvs = [
        ["podman", "inspect", "quay.io/aliyunos/wolfi:latest", "wolfi"],
        ["distrobox", "enter", "wolfi", "--", "apk", "add", "--no-cache", "git"],
    ]
    assert undeclared_host_paths(argvs, []) == []


def test_undeclared_host_paths_dedupes_and_keeps_first_seen_order():
    argvs = [
        ["a", "/x", "/y"],
        ["b", "/x", "/z"],
    ]
    assert undeclared_host_paths(argvs, []) == ["/x", "/y", "/z"]


# -- is_sandboxed -----------------------------------------------------------


def test_is_sandboxed_true_when_only_declared_paths():
    declared = ["/home/alex/projects:/projects:rw"]
    argvs = [["distrobox", "create", "--mount", "/home/alex/projects:/projects:rw"]]
    assert is_sandboxed(argvs, declared) is True


def test_is_sandboxed_false_when_undeclared_path():
    argvs = [["distrobox", "enter", "wolfi", "--", "apk", "add", "/etc/evil"]]
    assert is_sandboxed(argvs, []) is False


# -- SandboxGuard -----------------------------------------------------------


def test_guard_allows_declared_host_path():
    guard = SandboxGuard(allowlist=["/home/alex/projects:/projects:rw"])
    guard.check(["distrobox", "create", "--mount", "/home/alex/projects:/projects:rw"])


def test_guard_rejects_undeclared_host_path():
    guard = SandboxGuard()
    with pytest.raises(SandboxError) as exc:
        guard.check(["distrobox", "enter", "wolfi", "--", "apk", "add", "/etc/passwd"])
    assert "/etc/passwd" in str(exc.value)
    # The message names the declared allowlist for debugging.
    assert "declared host paths" in str(exc.value)


def test_guard_ignores_non_path_tokens():
    guard = SandboxGuard()
    guard.check(["podman", "inspect", "quay.io/wolfi:latest", "wolfi"])


def test_guard_set_allowlist_swaps_scope():
    guard = SandboxGuard(allowlist=["/old"])
    guard.set_allowlist(["/home/alex/projects:/projects:rw"])
    # Old path now rejected...
    with pytest.raises(SandboxError):
        guard.check(["x", "/old"])
    # New path now allowed.
    guard.check(["x", "/home/alex/projects:/projects:rw"])


# -- guard wired into the runner -------------------------------------------


def test_runner_guard_checks_before_execution():
    guard = SandboxGuard(allowlist=["/home/alex/projects:/projects:rw"])
    fake = FakeRunner(guard=guard)
    # Declared mount -> runs, is recorded.
    fake.run(["distrobox", "create", "--mount", "/home/alex/projects:/projects:rw"])
    assert fake.calls == [["distrobox", "create", "--mount", "/home/alex/projects:/projects:rw"]]


def test_runner_guard_rejects_before_recording():
    guard = SandboxGuard()
    fake = FakeRunner(guard=guard)
    with pytest.raises(SandboxError):
        fake.run(["distrobox", "enter", "wolfi", "--", "apk", "add", "/etc/evil"])
    # Rejected before the command ran -> nothing recorded.
    assert fake.calls == []


def test_runner_without_guard_never_checks():
    fake = FakeRunner()  # guard defaults to None
    fake.run(["distrobox", "enter", "wolfi", "--", "apk", "add", "/etc/evil"])
    assert fake.calls == [["distrobox", "enter", "wolfi", "--", "apk", "add", "/etc/evil"]]


# -- full-lifecycle guarantee ----------------------------------------------


def _not_found(fake: FakeRunner) -> None:
    """``podman inspect`` reports the container does not exist yet."""
    fake.respond(
        ["podman", "container", "exists"],
        RunResult(argv=["podman"], returncode=1, stderr="no such container"),
    )


def _full_lifecycle(guest: Guest, *, guard: SandboxGuard | None = None) -> list[list[str]]:
    """Run apply + enter + export + destroy for *guest*; return every argv."""
    fake = FakeRunner(guard=guard)
    _not_found(fake)
    manager = GuestManager(runner=fake)

    manager.apply(guest)
    manager.enter(guest.name)
    manager.export(guest.name, next(iter(guest.exports)))
    manager.destroy(guest.name)

    return fake.calls


def test_full_lifecycle_has_no_undeclared_host_paths():
    calls = _full_lifecycle(WOLFI)
    assert is_sandboxed(calls, declared_host_paths([WOLFI]))


def test_full_lifecycle_xnos_has_no_undeclared_host_paths():
    calls = _full_lifecycle(NIXOS)
    assert is_sandboxed(calls, declared_host_paths([NIXOS]))


def test_full_lifecycle_two_guests_has_no_undeclared_host_paths():
    fake = FakeRunner()
    _not_found(fake)
    manager = GuestManager(runner=fake)
    manager.apply(WOLFI)
    manager.apply(NIXOS)
    manager.destroy(WOLFI.name)
    manager.destroy(NIXOS.name)

    # The union of both guests' mounts is the only sanctioned host path set.
    allowlist = declared_host_paths([WOLFI, NIXOS])
    assert undeclared_host_paths(fake.calls, allowlist) == []


def test_apply_guard_locks_sandbox_to_declared_mounts():
    guard = SandboxGuard()
    _full_lifecycle(WOLFI, guard=guard)
    # After apply, the guard's allowlist is exactly this guest's mounts.
    assert guard.allowlist == {"/home/alex/projects:/projects:rw"}


def test_apply_guard_rejects_undeclared_host_path(monkeypatch):
    """Defense in depth: if the allow list ever comes up empty (e.g. a bug
    that fails to detect a guest's mounts), the guard rejects the create that
    references a host mount *before* it runs — surfaced as a clean GuestError."""
    from pengyos_guest import engines
    from pengyos_guest.engines import GuestError

    monkeypatch.setattr(engines, "declared_host_paths", lambda guests: set())
    guard = SandboxGuard()
    fake = FakeRunner(guard=guard)
    _not_found(fake)
    manager = GuestManager(runner=fake)

    with pytest.raises(GuestError) as exc:
        manager.apply(WOLFI)
    assert "undeclared host path" in str(exc.value)
    # The offending command never reached the runner.
    assert not any(c[1] == "create" for c in fake.calls)


def test_apply_guard_allows_declared_mount_in_create():
    guard = SandboxGuard()
    fake = FakeRunner(guard=guard)
    _not_found(fake)
    manager = GuestManager(runner=fake)

    result = manager.apply(WOLFI)
    assert result.created is True
    # The create referenced the declared mount and was allowed through.
    assert any(
        "/home/alex/projects:/projects:rw" in c
        for c in fake.calls
        if c[1] == "create"
    )
