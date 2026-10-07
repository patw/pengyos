"""Tests for the COSMIC session data model + deterministic emitter.

Covers:

* the :class:`~pengyos_desktop_session.model.CosmicSession` frozen dataclass
  holds the operator-decided facts (compositor = ``cosmic-comp``,
  exec = ``cosmic-session``, unit name, wayland socket path, greetd config,
  environment);
* the emitter renders each of the five artifacts deterministically (byte-for-byte
  reproducible, canonical ordering, single trailing newline);
* every renderer emits the facts the spec requires (present/absent ground for
  the check engine, item 4);
* overrides work, so the model is portable to other distros/tests.
"""

import tomllib
from pathlib import Path

import pytest

from pengyos_desktop_session import emit
from pengyos_desktop_session.model import CosmicSession

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def golden():
    """The canonical emitted artifacts (filename -> content)."""
    return emit.render_artifacts()


# --- the model: operator-decided facts -------------------------------------


def test_default_session_is_frozen_and_immutable():
    session = CosmicSession.default()
    assert isinstance(session, CosmicSession)
    # frozen: assigning a field raises
    with pytest.raises(Exception):
        session.greetd_user = "root"  # type: ignore[misc]


def test_default_session_compositor_is_cosmic_comp():
    """The decided compositor is cosmic-comp (SPEC 6.9 / Decision Record)."""
    assert CosmicSession().compositor == "cosmic-comp"


def test_default_session_exec_is_cosmic_session():
    assert CosmicSession().cosmic_greeter_exec == "cosmic-session"


def test_default_session_unit_name_and_wayland_socket():
    session = CosmicSession()
    assert session.unit_name == "cosmic-session@.service"
    assert session.wayland_socket_path == "/run/user/{uid}/wayland-0"
    # the placeholder resolves against the uid
    assert session.wayland_socket(1000) == "/run/user/1000/wayland-0"


def test_default_session_greetd_config_facts():
    session = CosmicSession()
    assert session.greetd_command == "cosmic-greeter"
    assert session.greetd_user == "alex"
    assert session.greetd_socket_var == "WAYLAND_SOCKET"


def test_default_session_env_present():
    env = CosmicSession().env
    assert env["XDG_CURRENT_DESKTOP"] == "COSMIC"
    assert env["XDG_SESSION_TYPE"] == "wayland"
    assert "WAYLAND_DISPLAY" in env


def test_model_overrides_are_allowed():
    """The model is portable: overriding the compositor yields a different
    (still valid) session — used to prove the emitter reads the data, not a
    hardcoded value."""
    session = CosmicSession(compositor="weston", cosmic_greeter_exec="weston")
    assert session.compositor == "weston"
    assert session.cosmic_greeter_exec == "weston"


# --- the emitter: canonical artifacts --------------------------------------


def test_render_artifacts_returns_all_five_in_order():
    arts = emit.render_artifacts()
    assert list(arts.keys()) == list(emit.ARTIFACT_ORDER)
    assert set(emit.ARTIFACT_ORDER) == {
        "greetd.toml",
        "cosmic.desktop",
        "cosmic-session@.service",
        "cosmic-session-launch.sh",
        "cosmic-session.env",
    }


def test_all_artifacts_end_with_single_newline():
    for name, content in emit.render_artifacts().items():
        assert content.endswith("\n"), name
        assert not content.endswith("\n\n"), name
        assert "\r" not in content, f"{name} has CR (must be LF-only)"


def test_emitter_is_deterministic():
    """Same input -> byte-identical output, across repeated renders."""
    assert emit.render_artifacts() == emit.render_artifacts()
    assert emit.render_artifacts() == emit.render_artifacts()


def test_emitter_is_pure_no_io():
    """render_artifacts must not touch the filesystem."""
    import os

    before = set(os.listdir("."))
    emit.render_artifacts()
    assert set(os.listdir(".")) == before


def test_header_identifies_the_tool():
    for content in emit.render_artifacts().values():
        assert "pengyos-desktop-session" in content
        assert "Do not edit by hand" in content


# --- greetd.toml -----------------------------------------------------------


def test_greetd_config_has_default_session():
    text = emit.render_greetd_config()
    assert "[default_session]" in text
    assert 'command = "cosmic-greeter"' in text
    assert 'user = "alex"' in text


def test_greetd_config_env_is_sorted():
    text = emit.render_greetd_config()
    env_line = next(
        line for line in text.splitlines() if line.startswith("env =")
    )
    keys = [
        part.split(" = ")[0] for part in env_line[len("env = { ") :].rstrip(" }").split(", ")
    ]
    assert keys == sorted(keys)


def test_greetd_config_is_valid_toml():
    """The emitted greetd config must parse as TOML (it is a config.toml)."""
    data = tomllib.loads(emit.render_greetd_config())
    session = data["default_session"]
    assert session["command"] == "cosmic-greeter"
    assert session["user"] == "alex"
    assert "env" in session


def test_greetd_config_reads_override():
    text = emit.render_greetd_config(
        CosmicSession(greetd_command="weston-greeter", greetd_user="alice")
    )
    assert 'command = "weston-greeter"' in text
    assert 'user = "alice"' in text


# --- cosmic.desktop --------------------------------------------------------


def test_cosmic_greeter_entry_has_decided_compositor_and_exec():
    text = emit.render_cosmic_greeter_entry()
    assert "[Desktop Entry]" in text
    assert 'Exec = "cosmic-session"' in text
    assert 'Compositor = "cosmic-comp"' in text


def test_cosmic_greeter_entry_reads_override():
    text = emit.render_cosmic_greeter_entry(
        CosmicSession(compositor="niri", cosmic_greeter_exec="niri-session")
    )
    assert 'Compositor = "niri"' in text
    assert 'Exec = "niri-session"' in text


# --- systemd unit ----------------------------------------------------------


def test_cosmic_session_unit_has_service_sections():
    text = emit.render_cosmic_session_unit()
    assert "[Unit]" in text
    assert "[Service]" in text
    assert "ExecStart=/usr/local/bin/cosmic-session-launch.sh" in text
    assert "cosmic-session@.service" in text


# --- launcher --------------------------------------------------------------


def test_launcher_execs_cosmic_session():
    text = emit.render_launcher()
    assert text.startswith("#!/bin/sh")
    assert "exec cosmic-session" in text


def test_launcher_reads_override():
    text = emit.render_launcher(
        CosmicSession(cosmic_greeter_exec="weston")
    )
    assert "exec weston" in text


# --- env file --------------------------------------------------------------


def test_env_file_is_sorted_key_equals_value():
    text = emit.render_env()
    pairs = [
        line for line in text.splitlines() if "=" in line and not line.startswith("#")
    ]
    keys = [line.split("=", 1)[0] for line in pairs]
    assert keys == sorted(keys)
    assert "XDG_CURRENT_DESKTOP=COSMIC" in pairs


def test_env_file_reads_override():
    text = emit.render_env(
        CosmicSession(env={"AAA": "1", "ZZZ": "2"})
    )
    assert "AAA=1" in text
    assert "ZZZ=2" in text


# --- golden: the canonical Desktop session renders exactly ------------------

GOLDEN = {
    "greetd.toml": (
        "# Emitted by `pengyos-desktop-session` (US-DSK-1). Do not edit by hand;\n"
        "# re-emit with:  pengyos-desktop-session emit --out /path/to/overlay\n"
        "\n"
        "[default_session]\n"
        'command = "cosmic-greeter"\n'
        'user = "alex"\n'
        'env = { WAYLAND_DISPLAY = "wayland-0", XDG_CURRENT_DESKTOP = "COSMIC", '
        'XDG_SEAT = "seat0", XDG_SESSION_TYPE = "wayland", '
        'XDG_VIRTUAL_BACKEND = "wayland" }\n'
    ),
    "cosmic.desktop": (
        "# Emitted by `pengyos-desktop-session` (US-DSK-1). Do not edit by hand;\n"
        "# re-emit with:  pengyos-desktop-session emit --out /path/to/overlay\n"
        "\n"
        "[Desktop Entry]\n"
        'Name = "COSMIC"\n'
        'Comment = "COSMIC Desktop Environment"\n'
        'Exec = "cosmic-session"\n'
        'Compositor = "cosmic-comp"\n'
        'Type = "Application"\n'
        'Categories = "COSMIC"\n'
    ),
    "cosmic-session@.service": (
        "# Emitted by `pengyos-desktop-session` (US-DSK-1). Do not edit by hand;\n"
        "# re-emit with:  pengyos-desktop-session emit --out /path/to/overlay\n"
        "\n"
        "[Unit]\n"
        "Description=PengyOS COSMIC session (cosmic-session@.service)\n"
        "After=graphical-login.service greetd.service\n"
        "\n"
        "[Service]\n"
        "ExecStart=/usr/local/bin/cosmic-session-launch.sh\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
    ),
    "cosmic-session-launch.sh": (
        "#!/bin/sh\n"
        "# Launcher for the PengyOS COSMIC session (emitted by "
        "pengyos-desktop-session, US-DSK-1). Do not edit by hand;\n"
        "# re-emit with:  pengyos-desktop-session emit --out /path/to/overlay\n"
        "set -eu\n"
        "\n"
        "# Ensure a wayland display exists for the compositor.\n"
        'if [ -z "${WAYLAND_DISPLAY:-}" ]; then\n'
        '  export WAYLAND_DISPLAY="wayland-0"\n'
        "fi\n"
        "\n"
        "exec cosmic-session\n"
    ),
    "cosmic-session.env": (
        "# Emitted by `pengyos-desktop-session` (US-DSK-1). Do not edit by hand;\n"
        "# re-emit with:  pengyos-desktop-session emit --out /path/to/overlay\n"
        "\n"
        "WAYLAND_DISPLAY=wayland-0\n"
        "XDG_CURRENT_DESKTOP=COSMIC\n"
        "XDG_SEAT=seat0\n"
        "XDG_SESSION_TYPE=wayland\n"
        "XDG_VIRTUAL_BACKEND=wayland\n"
    ),
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_golden_content(name, golden):
    assert golden[name] == GOLDEN[name]


def test_golden_matches_default_session():
    assert emit.render_artifacts() == GOLDEN
