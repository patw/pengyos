"""Tests for the distrobox wrapper (``pengyos_guest.distrobox``).

Exercised entirely through the mockable :class:`FakeRunner`, so these assert on
the exact argv that would be executed without needing a real distrobox,
network, root, or display.
"""

from pengyos_guest.distrobox import Distrobox
from pengyos_guest.runner import FakeRunner, RunResult, SubprocessRunner


def test_defaults_to_subprocess_runner():
    assert isinstance(Distrobox().runner, SubprocessRunner)


def test_injected_runner_is_used():
    fake = FakeRunner()
    Distrobox(runner=fake).list()
    assert fake.calls == [["distrobox", "list"]]


def test_create_builds_argv_without_mounts():
    fake = FakeRunner()
    Distrobox(runner=fake).create("wolfi", "quay.io/wolfi:latest")
    assert fake.calls == [
        ["distrobox", "create", "--yes", "--name", "wolfi", "--image", "quay.io/wolfi:latest"],
    ]


def test_create_nvidia_shares_the_host_driver():
    fake = FakeRunner()
    Distrobox(runner=fake).create("cuda", "nvidia/cuda:13.1.0-devel-ubuntu24.04", nvidia=True)
    assert fake.calls == [
        ["distrobox", "create", "--yes", "--name", "cuda",
         "--image", "nvidia/cuda:13.1.0-devel-ubuntu24.04", "--nvidia"],
    ]


def test_create_appends_each_mount_as_its_own_flag():
    fake = FakeRunner()
    mounts = ["/home/alex/projects:/projects:rw", "/home/alex/data:/data:ro"]
    Distrobox(runner=fake).create("wolfi", "img:latest", mounts=mounts)
    assert fake.calls == [
        [
            "distrobox",
            "create",
            "--yes",
            "--name",
            "wolfi",
            "--image",
            "img:latest",
            "--volume",
            "/home/alex/projects:/projects:rw",
            "--volume",
            "/home/alex/data:/data:ro",
        ],
    ]


def test_enter_without_command_launches_shell():
    fake = FakeRunner()
    Distrobox(runner=fake).enter("wolfi")
    assert fake.calls == [["distrobox", "enter", "wolfi"]]


def test_enter_with_command_appends_after_double_dash():
    fake = FakeRunner()
    Distrobox(runner=fake).enter(
        "wolfi",
        command=["sudo", "dnf", "install", "-y", "git", "curl"],
    )
    assert fake.calls == [
        ["distrobox", "enter", "wolfi", "--", "sudo", "dnf", "install", "-y", "git", "curl"],
    ]


def test_destroy_builds_argv_noninteractive():
    fake = FakeRunner()
    Distrobox(runner=fake).destroy("wolfi")
    assert fake.calls == [["distrobox", "rm", "--force", "wolfi"]]


def test_list_builds_argv():
    fake = FakeRunner()
    Distrobox(runner=fake).list()
    assert fake.calls == [["distrobox", "list"]]


def test_export_app_builds_argv():
    fake = FakeRunner()
    Distrobox(runner=fake).export_app("wolfi", "code")
    # distrobox-export runs inside the guest; it writes the host menu entry.
    assert fake.calls == [
        ["distrobox", "enter", "wolfi", "--", "distrobox-export", "--app", "code"],
    ]


def test_run_passthrough():
    fake = FakeRunner()
    Distrobox(runner=fake).run(["version"])
    assert fake.calls == [["distrobox", "version"]]


def test_env_passed_through_to_runner():
    fake = FakeRunner()
    Distrobox(runner=fake).run(["version"], env={"DISTROBOX_NAME": "wolfi"})
    assert fake.calls == [["distrobox", "version"]]
    assert fake.last_env == {"DISTROBOX_NAME": "wolfi"}


def test_default_result_is_used_when_unscripted():
    fake = FakeRunner(default=RunResult(argv=[], returncode=7))
    result = Distrobox(runner=fake).destroy("wolfi")
    assert result.returncode == 7
