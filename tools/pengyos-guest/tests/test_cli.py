"""Tests for the CLI."""

import io

import pytest

from pengyos_guest import __version__, cli
from pengyos_guest.engines import GuestManager
from pengyos_guest.runner import FakeRunner, RunResult

EXISTS = ["podman", "container", "exists"]


def run(argv, *, fake=None):
    out, err = io.StringIO(), io.StringIO()
    manager = GuestManager(runner=fake) if fake is not None else None
    code = cli.main(argv, out=out, err=err, manager=manager)
    return code, out.getvalue(), err.getvalue()


def _missing(fake: FakeRunner) -> FakeRunner:
    fake.respond(EXISTS, RunResult(argv=["podman"], returncode=1))
    return fake


@pytest.fixture
def guests_dirs(tmp_path, monkeypatch):
    """An empty image guests.d and user config dir, wired through the env."""
    system = tmp_path / "guests.d"
    system.mkdir()
    config = tmp_path / "config"
    (config / "pengyos").mkdir(parents=True)
    monkeypatch.setenv("PENGYOS_GUESTS_DIR", str(system))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    return system, config / "pengyos" / "guests.toml"


DEV = '''\
[guests.dev]
description = "Ubuntu build box"
image = "docker.io/library/ubuntu:24.04"
packages = ["build-essential"]
setup = ["command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh"]
'''


# -- list --------------------------------------------------------------------


def test_list_with_config_file(write_guests):
    code, out, _ = run(["--config", write_guests(), "list"], fake=_missing(FakeRunner()))
    assert code == 0
    assert [line.split("\t")[0] for line in out.splitlines()] == ["wolfi", "nixos"]
    assert "not created" in out


def test_list_defaults_to_image_and_user_guests(guests_dirs):
    system, user = guests_dirs
    (system / "dev.toml").write_text(DEV, encoding="utf-8")
    user.write_text('[guests.mine]\nimage = "alpine:3"\n', encoding="utf-8")
    code, out, _ = run(["list"], fake=FakeRunner())
    assert code == 0
    assert out.splitlines() == [
        "dev\tdocker.io/library/ubuntu:24.04\tcreated\tUbuntu build box",
        "mine\talpine:3\tcreated",
    ]


def test_list_missing_config_errors(tmp_path):
    code, _, err = run(["--config", str(tmp_path / "missing.toml"), "list"], fake=FakeRunner())
    assert code == 2
    assert "not found" in err


def test_list_bad_toml_errors(tmp_path):
    (tmp_path / "bad.toml").write_text("[guests]\nfoo = 1\n", encoding="utf-8")
    code, _, err = run(["--config", str(tmp_path / "bad.toml"), "list"], fake=FakeRunner())
    assert code == 2
    assert "foo" in err


# -- apply -------------------------------------------------------------------


def test_apply_creates_and_provisions_all(write_guests):
    fake = _missing(FakeRunner())
    code, out, _ = run(["--config", write_guests(), "apply"], fake=fake)

    assert code == 0
    assert "created guest wolfi" in out
    assert "provisioned 2 package(s) in wolfi" in out
    assert "created guest nixos" in out
    assert fake.calls == [
        EXISTS + ["wolfi"],
        [
            "distrobox", "create", "--yes", "--name", "wolfi",
            "--image", "quay.io/aliyunos/wolfi:latest",
            "--volume", "/home/alex/projects:/projects:rw",
        ],
        ["distrobox", "enter", "wolfi", "--", "sudo", "apk", "add", "--no-cache", "git", "curl"],
        EXISTS + ["nixos"],
        [
            "distrobox", "create", "--yes", "--name", "nixos",
            "--image", "ghcr.io/pengyos/nixos:latest",
            "--volume", "/home/alex/data:/data:ro",
        ],
        ["distrobox", "enter", "nixos", "--", "nix-env", "-iA", "nixpkgs.ripgrep"],
    ]
    # Pulls and installs go straight to the terminal.
    assert fake.streams == [False, True, True, False, True, True]


def test_apply_named_guest_only(write_guests):
    fake = _missing(FakeRunner())
    code, out, _ = run(["--config", write_guests(), "apply", "nixos"], fake=fake)
    assert code == 0
    assert "wolfi" not in out
    assert fake.calls[0] == EXISTS + ["nixos"]


def test_apply_unknown_guest_errors(write_guests):
    code, _, err = run(["--config", write_guests(), "apply", "nope"], fake=FakeRunner())
    assert code == 2
    assert "no such guest: nope" in err


def test_apply_is_idempotent_when_all_exist(write_guests):
    fake = FakeRunner()
    code, out, _ = run(["--config", write_guests(), "apply"], fake=fake)
    assert code == 0
    assert "created guest" not in out
    assert "already exists" in out
    assert not any(call[:2] == ["distrobox", "create"] for call in fake.calls)


def test_apply_reports_clean_error_on_failure(write_guests):
    fake = _missing(FakeRunner())
    fake.respond(["distrobox", "create"], RunResult(argv=["distrobox"], returncode=1))
    code, _, err = run(["--config", write_guests(), "apply"], fake=fake)
    assert code == 2
    assert "error: failed to create guest 'wolfi'" in err


# -- enter -------------------------------------------------------------------


def test_enter_existing_guest_runs_interactive_shell():
    fake = FakeRunner()
    code, out, _ = run(["enter", "wolfi"], fake=fake)
    assert code == 0
    assert out == ""
    assert fake.calls == [EXISTS + ["wolfi"], ["distrobox", "enter", "wolfi"]]
    assert fake.streams[-1] is True


def test_enter_passes_command_through():
    fake = FakeRunner()
    run(["enter", "wolfi", "--", "make", "-j8", "--keep-going"], fake=fake)
    assert fake.calls[-1][-3:] == ["make", "-j8", "--keep-going"]


def test_enter_returns_the_command_exit_code():
    fake = FakeRunner()
    fake.respond(["distrobox", "enter"], RunResult(argv=["distrobox"], returncode=3))
    code, _, _ = run(["enter", "wolfi", "--", "false"], fake=fake)
    assert code == 3


def test_enter_creates_a_declared_guest_on_first_use(guests_dirs):
    system, _ = guests_dirs
    (system / "dev.toml").write_text(DEV, encoding="utf-8")
    fake = _missing(FakeRunner())
    code, out, _ = run(["enter", "dev", "--", "cmake", "--version"], fake=fake)

    assert code == 0
    assert "creating it" in out
    assert [c[:2] for c in fake.calls] == [
        ["podman", "container"],  # enter: does it exist?
        ["podman", "container"],  # apply: does it exist?
        ["distrobox", "create"],
        ["distrobox", "enter"],   # apt install
        ["distrobox", "enter"],   # setup step
        ["distrobox", "enter"],   # the command
    ]
    assert fake.calls[4][-3:] == ["sh", "-lc", DEV_SETUP]
    assert fake.calls[-1][:3] == ["distrobox", "enter", "dev"]
    assert fake.calls[-1][-2:] == ["cmake", "--version"]


DEV_SETUP = "command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh"


def test_enter_unknown_guest_errors(guests_dirs):
    code, _, err = run(["enter", "nope"], fake=_missing(FakeRunner()))
    assert code == 2
    assert "no such guest: nope" in err


# -- export / destroy -----------------------------------------------------------


def test_export_writes_launcher(tmp_path, monkeypatch):
    monkeypatch.setenv("PENGYOS_LAUNCHER_DIR", str(tmp_path))
    fake = FakeRunner()
    code, out, _ = run(["export", "wolfi", "--app", "git-editor"], fake=fake)

    assert code == 0
    assert "exported git-editor" in out
    launcher = tmp_path / "git-editor"
    assert "distrobox enter wolfi -- git-editor" in launcher.read_text(encoding="utf-8")
    assert fake.calls == [
        ["distrobox", "enter", "wolfi", "--", "distrobox-export", "--app", "git-editor"]
    ]


def test_destroy_removes_the_container():
    fake = FakeRunner()
    code, out, _ = run(["destroy", "wolfi"], fake=fake)
    assert code == 0
    assert "destroyed guest wolfi" in out
    assert fake.calls == [EXISTS + ["wolfi"], ["distrobox", "rm", "--force", "wolfi"]]


def test_destroy_is_idempotent_when_gone():
    fake = _missing(FakeRunner())
    code, _, _ = run(["destroy", "wolfi"], fake=fake)
    assert code == 0
    assert fake.calls == [EXISTS + ["wolfi"]]


# -- parser --------------------------------------------------------------------


def test_version():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0


def test_requires_a_command():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0


def test_build_parser_has_all_subcommands():
    parser = cli.build_parser()
    sub = {
        choice
        for action in parser._actions
        if isinstance(getattr(action, "choices", None), dict)
        for choice in action.choices
    }
    assert {"apply", "enter", "export", "destroy", "list"} <= sub


def test_export_requires_app():
    with pytest.raises(SystemExit) as exc:
        cli.main(["export", "wolfi"])
    assert exc.value.code != 0


def test_module_version_is_string():
    assert isinstance(__version__, str)
