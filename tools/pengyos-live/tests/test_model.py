"""Tests for the live-overlay data model.

Covers the model deliverable: the edition/session enums, the ``OverlayFile`` /
``LiveOverlay`` values, the per-component builders (autologin, installer
unit/session, live marker), the edition × session coverage, determinism, and
the JSON schema + serialisation.
"""

import json

import pytest

from pengyos_live import model
from pengyos_live.model import (
    AUTOLOGIN_PATH,
    INSTALLER_BIN,
    INSTALLER_SESSION_PATH,
    INSTALLER_UNIT_PATH,
    LIVE_MARKER_PATH,
    LIVE_USER,
    SUDOERS_PATH,
    Edition,
    LiveOverlay,
    OverlayFile,
    Session,
    build_overlay,
    build_overlay_from_strings,
    overlay_to_dict,
    overlay_to_json,
)


# --- Edition -----------------------------------------------------------------


def test_edition_values():
    assert Edition.DESKTOP.value_str == "desktop"
    assert Edition.CORE.value_str == "core"
    assert Edition.SERVER.value_str == "server"


def test_edition_all_values_order_and_membership():
    assert Edition.all_values() == ("desktop", "core", "server")


def test_edition_unknown_raises():
    with pytest.raises(ValueError):
        Edition("tablet")


# --- Session -----------------------------------------------------------------


def test_session_values():
    assert Session.COSMIC.value_str == "cosmic"
    assert Session.CONSOLE.value_str == "console"


def test_session_all_values_membership():
    assert Session.all_values() == ("cosmic", "console")


def test_session_is_graphical_and_console():
    assert Session.COSMIC.is_graphical is True
    assert Session.COSMIC.is_console is False
    assert Session.CONSOLE.is_graphical is False
    assert Session.CONSOLE.is_console is True


def test_session_unknown_raises():
    with pytest.raises(ValueError):
        Session("wayland")


# --- OverlayFile -------------------------------------------------------------


def test_overlay_file_is_frozen_and_round_trips_through_dict():
    f = OverlayFile("/etc/pengyos/live", "live=true\n")
    assert f.path == "/etc/pengyos/live"
    assert f.content == "live=true\n"
    # frozen -> immutable
    with pytest.raises(Exception):  # noqa: PT011
        f.path = "/x"
    assert f.as_dict() == {"path": "/etc/pengyos/live", "content": "live=true\n"}


def test_overlay_file_is_hashable():
    a = OverlayFile("/a", "x")
    b = OverlayFile("/a", "x")
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


# --- build_overlay: installer wiring by session ------------------------------


def test_cosmic_uses_session_entry_instead_of_unit():
    ov = build_overlay(Edition.DESKTOP, Session.COSMIC)
    paths = ov.paths()
    assert INSTALLER_SESSION_PATH in paths
    assert INSTALLER_UNIT_PATH not in paths
    assert [f.path for f in ov.installer_files] == [INSTALLER_SESSION_PATH]


def test_console_uses_systemd_unit_instead_of_session():
    ov = build_overlay(Edition.DESKTOP, Session.CONSOLE)
    paths = ov.paths()
    assert INSTALLER_UNIT_PATH in paths
    assert INSTALLER_SESSION_PATH not in paths
    assert [f.path for f in ov.installer_files] == [INSTALLER_UNIT_PATH]


def test_autologin_and_marker_present_for_both_sessions():
    for session in Session:
        ov = build_overlay(Edition.DESKTOP, session)
        assert ov.autologin_file is not None
        assert ov.autologin_file.path == AUTOLOGIN_PATH
        assert ov.marker_file is not None
        assert ov.marker_file.path == LIVE_MARKER_PATH


def test_sudo_policy_present_for_both_sessions():
    for session in Session:
        ov = build_overlay(Edition.DESKTOP, session)
        assert ov.sudoers_file is not None
        assert ov.sudoers_file.path == SUDOERS_PATH
        # both sessions share the sudo policy
        assert AUTOLOGIN_PATH in ov.paths()
        assert SUDOERS_PATH in ov.paths()


def test_exactly_four_files_per_overlay():
    for edition in Edition:
        for session in Session:
            ov = build_overlay(edition, session)
            assert len(ov.files) == 4


def test_sudo_policy_content_grants_live_user_passwordless_installer():
    for edition in Edition:
        for session in Session:
            ov = build_overlay(edition, session)
            sudo = ov.sudoers_file
            assert sudo is not None
            assert f"{LIVE_USER} ALL=(ALL) NOPASSWD: /usr/bin/{INSTALLER_BIN}" in sudo.content


# --- edition x session coverage ----------------------------------------------


def test_every_edition_x_session_has_unique_paths():
    for edition in Edition:
        for session in Session:
            ov = build_overlay(edition, session)
            paths = ov.paths()
            # no duplicate paths within an overlay
            assert len(paths) == len(set(paths))
            # both sessions share autologin + marker + sudo policy
            assert AUTOLOGIN_PATH in paths
            assert LIVE_MARKER_PATH in paths
            assert SUDOERS_PATH in paths


# --- component content -------------------------------------------------------


def test_autologin_content_autologs_live_user_via_getty():
    content = model._autologin_content()
    assert "[Service]" in content
    # the ExecStart= clears the previous one, then re-adds the autologin getty
    assert "ExecStart=" in content
    assert f"--autologin {LIVE_USER}" in content
    assert "getty" in content


def test_live_marker_content_records_edition_and_live_flag():
    for edition in Edition:
        ov = build_overlay(edition, Session.CONSOLE)
        marker = ov.marker_file
        assert marker is not None
        assert f"edition={edition.value_str}" in marker.content
        assert "live=true" in marker.content


def test_console_unit_runs_installer_binary_and_refs_marker():
    ov = build_overlay(Edition.DESKTOP, Session.CONSOLE)
    unit = ov.installer_files[0]
    assert unit.path == INSTALLER_UNIT_PATH
    assert f"/usr/bin/{INSTALLER_BIN}" in unit.content
    assert "[Unit]" in unit.content
    assert "[Service]" in unit.content
    assert "[Install]" in unit.content
    # the installer is wired to autostart (WantedBy)
    assert "WantedBy=multi-user.target" in unit.content


def test_cosmic_session_runs_installer_binary():
    ov = build_overlay(Edition.DESKTOP, Session.COSMIC)
    entry = ov.installer_files[0]
    assert entry.path == INSTALLER_SESSION_PATH
    assert "[Desktop Entry]" in entry.content
    assert f"Exec=/usr/bin/{INSTALLER_BIN}" in entry.content
    # an autostart entry, not a login-session choice
    assert "X-GNOME-Autostart-enabled=true" in entry.content


# --- determinism -------------------------------------------------------------


def test_build_is_deterministic():
    a = build_overlay(Edition.SERVER, Session.COSMIC)
    b = build_overlay(Edition.SERVER, Session.COSMIC)
    assert a.files == b.files
    assert overlay_to_json(a) == overlay_to_json(b)


# --- LiveOverlay properties --------------------------------------------------


def test_installer_files_property_filters_correctly():
    ov = build_overlay(Edition.CORE, Session.CONSOLE)
    installer = ov.installer_files
    assert len(installer) == 1
    assert all(f.path in (INSTALLER_UNIT_PATH, INSTALLER_SESSION_PATH) for f in installer)


def test_paths_sorted():
    ov = build_overlay(Edition.DESKTOP, Session.COSMIC)
    assert tuple(sorted(f.path for f in ov.files)) == ov.paths()


# --- build_overlay_from_strings ----------------------------------------------


def test_build_overlay_from_strings_ok():
    ov = build_overlay_from_strings("server", "console")
    assert ov.edition is Edition.SERVER
    assert ov.session is Session.CONSOLE


def test_build_overlay_from_strings_bad_edition():
    with pytest.raises(ValueError):
        build_overlay_from_strings("nope", "console")


def test_build_overlay_from_strings_bad_session():
    with pytest.raises(ValueError):
        build_overlay_from_strings("desktop", "nope")


# --- JSON schema + serialisation ---------------------------------------------


def test_overlay_json_schema_shape():
    schema = model.OVERLAY_JSON_SCHEMA
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"edition", "session", "files"}
    props = schema["properties"]
    assert props["edition"]["enum"] == list(Edition.all_values())
    assert props["session"]["enum"] == list(Session.all_values())
    assert props["files"]["type"] == "array"
    # autologin + marker + installer + sudo policy
    assert props["files"]["minItems"] == 4
    item = props["files"]["items"]
    assert set(item["required"]) == {"path", "content"}


def test_overlay_to_dict_matches_overlay():
    ov = build_overlay(Edition.DESKTOP, Session.COSMIC)
    d = overlay_to_dict(ov)
    assert d["edition"] == "desktop"
    assert d["session"] == "cosmic"
    assert len(d["files"]) == 4
    for entry in d["files"]:
        assert set(entry) == {"path", "content"}


def test_overlay_json_round_trips_through_dict():
    ov = build_overlay(Edition.SERVER, Session.CONSOLE)
    text = overlay_to_json(ov)
    parsed = json.loads(text)
    assert parsed == overlay_to_dict(ov)


def test_overlay_json_is_stable_across_runs():
    a = overlay_to_json(build_overlay(Edition.DESKTOP, Session.COSMIC))
    b = overlay_to_json(build_overlay(Edition.DESKTOP, Session.COSMIC))
    assert a == b
