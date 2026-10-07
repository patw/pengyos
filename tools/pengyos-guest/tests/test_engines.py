"""Tests for :class:`pengyos_guest.engines.GuestManager`.

Exercised through the mockable :class:`FakeRunner`, so these assert on the exact
argv that would be executed and never touch a real podman/distrobox, network,
root, or display.
"""

import os
from pathlib import Path

import pytest

from pengyos_guest.config import Guest
from pengyos_guest.engines import GuestManager, default_launcher_dir
from pengyos_guest.podman import SubprocessRunner
from pengyos_guest.runner import FakeRunner, RunResult

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


def _script_not_found(fake: FakeRunner) -> None:
    """A container that does not exist yet (``podman inspect`` -> non-zero)."""
    fake.respond(
        ["podman", "container", "exists"],
        RunResult(argv=["podman"], returncode=1),
    )


def _script_exists(fake: FakeRunner) -> None:
    """A container that already exists (``podman inspect`` -> zero)."""
    fake.respond(
        ["podman", "container", "exists"],
        RunResult(argv=["podman"], returncode=0),
    )


# -- runner wiring ----------------------------------------------------------


def test_defaults_to_subprocess_runner():
    assert isinstance(GuestManager().runner, SubprocessRunner)


def test_manager_shares_injected_runner():
    fake = FakeRunner()
    manager = GuestManager(runner=fake)
    assert manager.runner is fake
    assert manager.podman.runner is fake
    assert manager.distrobox.runner is fake


# -- apply ------------------------------------------------------------------


def test_apply_first_run_creates_and_provisions():
    fake = FakeRunner()
    _script_not_found(fake)
    manager = GuestManager(runner=fake)

    result = manager.apply(WOLFI)

    assert result.created is True
    assert result.provisioned is True
    assert result.packages == ["git", "curl"]
    assert fake.calls == [
        ["podman", "container", "exists", "wolfi"],
        [
            "distrobox", "create", "--yes", "--name", "wolfi",
            "--image", "quay.io/aliyunos/wolfi:latest",
            "--volume", "/home/alex/projects:/projects:rw",
        ],
        [
            "distrobox", "enter", "wolfi", "--",
            "sudo", "apk", "add", "--no-cache", "git", "curl",
        ],
    ]


def test_apply_when_container_exists_skips_create_but_reprovisions():
    fake = FakeRunner()
    _script_exists(fake)
    manager = GuestManager(runner=fake)

    result = manager.apply(WOLFI)

    assert result.created is False
    assert result.provisioned is True
    # No distrobox create on the second apply; only inspect + provision.
    assert fake.calls == [
        ["podman", "container", "exists", "wolfi"],
        [
            "distrobox", "enter", "wolfi", "--",
            "sudo", "apk", "add", "--no-cache", "git", "curl",
        ],
    ]


def test_apply_is_idempotent_across_two_runs():
    """Running apply twice does not recreate an existing guest."""

    class TogglingRunner(FakeRunner):
        """``inspect`` returns non-zero on the first call, zero afterwards."""

        def __init__(self) -> None:
            super().__init__()
            self._called = 0

        def run(self, argv, *, env=None, stream=False):  # type: ignore[override]
            result = super().run(argv, env=env, stream=stream)
            if argv[:3] == ["podman", "container", "exists"]:
                self._called += 1
                code = 1 if self._called == 1 else 0
                return RunResult(argv=list(argv), returncode=code)
            return result

    fake = TogglingRunner()
    manager = GuestManager(runner=fake)

    first = manager.apply(WOLFI)
    second = manager.apply(WOLFI)

    assert first.created is True
    assert second.created is False
    # Exactly one create across both runs.
    creates = [c for c in fake.calls if c[0] == "distrobox" and c[1] == "create"]
    assert len(creates) == 1


def test_apply_argv_references_only_declared_host_paths():
    """The spec's "no host writes" contract: no host path outside the declared
    mounts appears in the argv handed to podman/distrobox.
    """
    fake = FakeRunner()
    _script_not_found(fake)
    manager = GuestManager(runner=fake)
    manager.apply(WOLFI)

    for call in fake.calls:
        for token in call:
            # Any token that looks like an absolute host path must be exactly
            # the declared mount spec — nothing points at /etc, /tmp, /usr,
            # or any other undeclared location.
            if token.startswith("/home"):
                assert token == "/home/alex/projects:/projects:rw"
            assert not token.startswith(("/etc", "/tmp", "/usr", "/var", "/root"))


def test_apply_raises_on_create_failure():
    from pengyos_guest.engines import GuestError

    fake = FakeRunner()
    _script_not_found(fake)
    fake.respond(
        ["distrobox", "create"],
        RunResult(argv=["distrobox"], returncode=1, stderr="create failed"),
    )
    manager = GuestManager(runner=fake)

    with pytest.raises(GuestError) as exc:
        manager.apply(WOLFI)
    assert "failed to create guest 'wolfi'" in str(exc.value)


def test_apply_without_packages_does_not_provision():
    fake = FakeRunner()
    _script_not_found(fake)
    manager = GuestManager(runner=fake)

    no_pkgs = Guest(name="bare", image="wolfi:latest")
    result = manager.apply(no_pkgs)

    assert result.created is True
    assert result.provisioned is False
    # create, but no enter/provision step.
    assert fake.calls == [
        ["podman", "container", "exists", "bare"],
        ["distrobox", "create", "--yes", "--name", "bare", "--image", "wolfi:latest"],
    ]


def test_apply_nixos_uses_nix_env():
    fake = FakeRunner()
    _script_not_found(fake)
    manager = GuestManager(runner=fake)

    manager.apply(NIXOS)

    provision = [c for c in fake.calls if c[1] == "enter"][0]
    assert provision == [
        "distrobox", "enter", "nixos", "--", "nix-env", "-iA", "nixpkgs.ripgrep",
    ]


# -- enter ------------------------------------------------------------------


def test_enter_launches_shell():
    fake = FakeRunner()
    manager = GuestManager(runner=fake)

    result = manager.enter("wolfi")

    assert fake.calls == [["distrobox", "enter", "wolfi"]]
    assert fake.streams == [True]  # attached to the terminal
    assert result.returncode == 0


def test_enter_with_command_runs_after_double_dash():
    fake = FakeRunner()
    manager = GuestManager(runner=fake)

    manager.enter("wolfi", command=["git", "--version"])

    # Through a login shell (the guest's /etc/profile.d PATH), argv untouched.
    assert fake.calls == [[
        "distrobox", "enter", "wolfi", "--",
        "sh", "-lc", 'exec "$@"', "pengyos-guest", "git", "--version",
    ]]


# -- export -----------------------------------------------------------------


def test_export_writes_launcher_and_registers_app(tmp_path, monkeypatch):
    monkeypatch.setenv("PENGYOS_LAUNCHER_DIR", str(tmp_path))
    fake = FakeRunner()
    manager = GuestManager(runner=fake)

    result = manager.export("wolfi", "git-editor")

    assert result.name == "wolfi"
    assert result.app == "git-editor"
    assert result.launcher_path == tmp_path / "git-editor"
    # distrobox-export is asked for a menu entry (GUI apps)...
    assert fake.calls == [
        ["distrobox", "enter", "wolfi", "--", "distrobox-export", "--app", "git-editor"]
    ]
    # ...and a faithful host-side launcher is written referencing the guest.
    launcher = result.launcher_path.read_text(encoding="utf-8")
    assert launcher == (
        "#!/bin/sh\n"
        "# Host-side launcher for git-editor via the wolfi PengyOS guest.\n"
        "# Generated by pengyos-guest export — do not edit by hand.\n"
        'exec distrobox enter wolfi -- git-editor "$@"\n'
    )
    assert os.access(result.launcher_path, os.X_OK)


def test_export_defaults_launcher_dir_to_home_local_bin(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PENGYOS_LAUNCHER_DIR", raising=False)
    monkeypatch.delenv("XDG_BIN_HOME", raising=False)
    assert default_launcher_dir() == tmp_path / ".local" / "bin"


def test_export_respects_xdg_bin_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_BIN_HOME", str(tmp_path / "bin"))
    monkeypatch.delenv("PENGYOS_LAUNCHER_DIR", raising=False)
    assert default_launcher_dir() == tmp_path / "bin"


# -- destroy ----------------------------------------------------------------


def test_destroy_removes_the_container():
    fake = FakeRunner()
    manager = GuestManager(runner=fake)

    manager.destroy("wolfi")

    assert fake.calls == [
        ["podman", "container", "exists", "wolfi"],
        ["distrobox", "rm", "--force", "wolfi"],
    ]


def test_destroy_is_idempotent_when_container_gone():
    fake = FakeRunner()
    _script_not_found(fake)
    manager = GuestManager(runner=fake)

    manager.destroy("ghost")

    assert fake.calls == [["podman", "container", "exists", "ghost"]]


# -- nvidia / setup -------------------------------------------------------------


def test_apply_nvidia_guest_and_setup_steps():
    fake = FakeRunner()
    _script_not_found(fake)
    cuda = Guest(
        name="cuda",
        image="docker.io/nvidia/cuda:13.1.0-devel-ubuntu24.04",
        packages=["cmake"],
        setup=["command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh"],
        nvidia=True,
    )

    result = GuestManager(runner=fake).apply(cuda, stream=True)

    assert result.setup == cuda.setup
    assert fake.calls[1][-1] == "--nvidia"
    assert fake.calls[2] == [
        "distrobox", "enter", "cuda", "--", "sh", "-c",
        "sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y cmake",
    ]
    assert fake.calls[3] == ["distrobox", "enter", "cuda", "--", "sh", "-lc", cuda.setup[0]]
    assert fake.streams == [False, True, True, True]


def test_apply_raises_on_setup_failure():
    from pengyos_guest.engines import GuestError

    fake = FakeRunner()
    fake.respond(["distrobox", "enter", "x", "--", "sh", "-lc"], RunResult(argv=[], returncode=1))
    with pytest.raises(GuestError, match="setup step failed in guest 'x': false"):
        GuestManager(runner=fake).apply(Guest(name="x", image="alpine:3", setup=["false"]))
