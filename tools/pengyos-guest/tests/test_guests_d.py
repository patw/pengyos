"""Tests for the shipped ``guests.d/*.toml`` (the image's default guests).

The repo-root ``guests.toml`` is a small example; ``guests.d/`` holds what
actually ships in the Server/Desktop image as
``/usr/share/pengyos/guests.d`` (``board/server/post-build.sh`` copies these
files verbatim). They are the deliverable, so they are tested as such.

The PATH tests are a regression test for a real bug (found on a Desktop box
running 26.10): the dev box installs rustup into ``~/.cargo/bin`` and exported
the PATH only through ``/etc/profile.d``, which only bash login shells read.
zsh — PengyOS's default shell, and the shell ``pengyos-guest enter dev`` lands
you in — never sources ``/etc/profile``, so the box came out with cargo
installed and not on the PATH. Any guest that puts a toolchain on the PATH must
wire it up for *both* shells.
"""

import pathlib
import re

import pytest

from pengyos_guest.config import load

GUESTS_D = pathlib.Path(__file__).resolve().parent.parent / "guests.d"

#: The single file both shells are made to source.
PATH_FILE = "/etc/pengyos-dev-path.sh"
#: Marker that makes the zshenv append idempotent.
MARKER = "pengyos-dev-path"


def _guest(filename: str):
    return load(str(GUESTS_D / filename)).guests[0]


def _echo_payloads(step: str) -> list[str]:
    """The single-quoted argument of each ``echo '…'`` in *step*."""
    return re.findall(r"echo\s+'([^']*)'", step)


def _sourced_files(guest) -> list[str]:
    """Every ``/etc`` file that a setup step tells a shell to source."""
    files = []
    for step in guest.setup:
        for payload in _echo_payloads(step):
            payload = payload.strip()
            if payload.startswith(". /etc/"):
                files.append(payload.split()[1])
    return files


def test_guests_d_directory_ships_dev_and_cuda():
    assert sorted(p.name for p in GUESTS_D.glob("*.toml")) == ["cuda.toml", "dev.toml"]


@pytest.mark.parametrize("filename", ["dev.toml", "cuda.toml"])
def test_shipped_guest_parses(filename):
    guest = _guest(filename)
    assert guest.name and guest.image
    assert isinstance(guest.packages, list) and guest.packages
    assert isinstance(guest.setup, list)


def test_dev_guest_fields():
    dev = _guest("dev.toml")
    assert dev.name == "dev"
    assert dev.image == "docker.io/library/ubuntu:24.04"
    assert dev.description.startswith("Ubuntu 24.04")
    for pkg in ("build-essential", "cmake", "git", "golang"):
        assert pkg in dev.packages


def test_cuda_guest_fields():
    cuda = _guest("cuda.toml")
    assert cuda.name == "cuda"
    assert cuda.nvidia is True, "the CUDA box must share the host driver"
    assert "13.1.0" in cuda.image


# -- the PATH regression ---------------------------------------------------------

@pytest.mark.parametrize("filename", ["dev.toml", "cuda.toml"])
def test_path_setup_reaches_bash_login_shells(filename):
    """``/etc/profile.d`` is what bash login shells read."""
    text = "\n".join(_guest(filename).setup)
    assert "/etc/profile.d/" in text


@pytest.mark.parametrize("filename", ["dev.toml", "cuda.toml"])
def test_path_setup_reaches_zsh(filename):
    """zsh never sources ``/etc/profile``, so it needs the box's ``zshenv``.

    ``zshenv`` is the only system zsh file sourced on *every* invocation,
    login or not, interactive or not — which is what both ``enter dev`` and
    ``enter dev -- cargo build`` need.
    """
    text = "\n".join(_guest(filename).setup)
    assert "/etc/zsh/zshenv" in text, (
        f"{filename} puts toolchains in ~/.cargo/bin and ~/.local/bin and wires the "
        "PATH only into /etc/profile.d; a zsh shell in the box — the shell "
        "pengyos-guest enter dev gives a PengyOS host — will not see cargo or uv"
    )


@pytest.mark.parametrize("filename", ["dev.toml", "cuda.toml"])
def test_both_shells_source_the_same_path_file(filename):
    """One PATH file, sourced by both shells, so they cannot drift apart."""
    sourced = _sourced_files(_guest(filename))
    assert sourced, "no setup step tells a shell to source a PATH file"
    assert set(sourced) == {PATH_FILE}, f"shells source different files: {sorted(set(sourced))}"


@pytest.mark.parametrize("filename", ["dev.toml", "cuda.toml"])
def test_path_file_defines_the_toolchain_path(filename):
    """The file both shells source is written whole, and carries both dirs."""
    guest = _guest(filename)
    writers = [s for s in guest.setup if f"tee {PATH_FILE} >" in s]
    assert len(writers) == 1, "the PATH file should be written by exactly one step"
    payload = _echo_payloads(writers[0])[0]
    assert "$HOME/.cargo/bin" in payload and "$HOME/.local/bin" in payload
    assert "export PATH=" in payload
    # Truncated, not appended to: `tee -a` here would grow the PATH every apply.
    assert f"tee -a {PATH_FILE}" not in "\n".join(guest.setup)


@pytest.mark.parametrize("filename", ["dev.toml", "cuda.toml"])
def test_path_setup_is_idempotent(filename):
    """``setup`` runs on every apply, so the zshenv append must be guarded.

    An unguarded ``tee -a`` appends the same line once per apply.
    """
    guest = _guest(filename)
    append_steps = [s for s in guest.setup if "tee -a /etc/zsh/zshenv" in s]
    assert len(append_steps) == 1
    step = append_steps[0]
    assert "grep -q" in step, "the zshenv append is unguarded and will accumulate"
    # The guard must test for a marker that the appended line itself contains,
    # otherwise the guard never fires and the line still accumulates.
    assert MARKER in step.split("grep -q ")[1].split(" ")[0]
    appended = _echo_payloads(step)[0]
    assert MARKER in appended, "the guard tests a marker absent from the appended line"
    assert PATH_FILE in appended


def test_path_setup_does_not_touch_the_host_shell_configs():
    """The box's ``/etc`` is its own; the shared dotfiles stay untouched.

    Writing the PATH into ``~/.bashrc`` / ``~/.zshrc`` would leak the box's
    PATH onto the host, which shares the same home.
    """
    for filename in ("dev.toml", "cuda.toml"):
        text = "\n".join(_guest(filename).setup)
        for host_file in ("~/.bashrc", "~/.zshrc", "$HOME/.bashrc", "$HOME/.zshrc"):
            assert host_file not in text, f"{filename} writes the host's {host_file}"
