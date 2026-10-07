"""Tests for the validated data models.

Covers :class:`Release`, :class:`Layout`, :class:`Step`, :class:`Plan`, and
:class:`ProvisionConfig` — the plain-text inputs the engine consumes, validated
into typed, immutable objects.

The fixtures live in ``conftest.py`` (``release_json``, ``layout_json``,
``install_toml``); here we assert they parse cleanly and that every validation
rule has teeth.
"""

from __future__ import annotations

import pytest

from pengyos_install.models import (
    ConfigError,
    Layout,
    LayoutError,
    Plan,
    ReleaseError,
    Release,
    ProvisionConfig,
    Step,
    ValidationError,
)

HEX64 = "0" * 64
HEX64B = "1" * 64


# --------------------------------------------------------------------------- #
# Release
# --------------------------------------------------------------------------- #
def test_release_parses_fixture(release_json):
    rel = Release.from_json(release_json)
    assert rel.name == "pengyos"
    assert rel.edition == "core"
    assert rel.version == "2026.08-1"
    assert rel.slot == "a"
    assert rel.rootfs.path == "rootfs.squashfs"
    assert rel.rootfs.size == 1900000000
    assert rel.uki.path == "boot/EFI/boot/bootx64.efi"
    assert rel.signature.alg == "ed25519"
    assert rel.signature.verified is True
    assert rel.is_signed is True


def test_release_bad_json_raises_release_error():
    with pytest.raises(ReleaseError):
        Release.from_json("{not json")


def test_release_missing_required_field():
    text = '{"name": "pengyos"}'
    with pytest.raises(ReleaseError):
        Release.from_json(text)


def test_release_rejects_malformed_sha256():
    text = (
        '{"name": "p", "edition": "e", "version": "v", "slot": "a", '
        '"rootfs": {"path": "r", "sha256": "xyz", "size": 1}, '
        '"uki": {"path": "u", "sha256": "%s", "size": 1}, '
        '"signature": {"alg": "ed25519", "cert": "c", "data": "d", "verified": true}}'
        % HEX64
    )
    with pytest.raises(ReleaseError):
        Release.from_json(text)


def test_release_rejects_non_positive_size():
    text = (
        '{"name": "p", "edition": "e", "version": "v", "slot": "a", '
        '"rootfs": {"path": "r", "sha256": "%s", "size": 0}, '
        '"uki": {"path": "u", "sha256": "%s", "size": 1}, '
        '"signature": {"alg": "ed25519", "cert": "c", "data": "d", "verified": true}}'
        % (HEX64, HEX64)
    )
    with pytest.raises(ReleaseError):
        Release.from_json(text)


def test_release_unsigned_is_not_signed():
    text = (
        '{"name": "p", "edition": "e", "version": "v", "slot": "a", '
        '"rootfs": {"path": "r", "sha256": "%s", "size": 1}, '
        '"uki": {"path": "u", "sha256": "%s", "size": 1}, '
        '"signature": {"alg": "ed25519", "cert": "c", "data": "d", "verified": false}}'
        % (HEX64, HEX64)
    )
    rel = Release.from_json(text)
    assert rel.is_signed is False


def test_release_roundtrips_through_json(release_json):
    rel = Release.from_json(release_json)
    again = Release.from_json(rel.to_json())
    assert again == rel
    # Deterministic ordering: identical bytes on repeat serialization.
    assert rel.to_json() == rel.to_json()


def test_release_is_immutable(release_json):
    rel = Release.from_json(release_json)
    with pytest.raises(AttributeError):
        rel.name = "other"  # type: ignore[misc]


def test_release_from_json_file(tmp_path, release_json, write_file):
    path = write_file("rel.json", release_json)
    rel = Release.from_json_file(path)
    assert rel.name == "pengyos"


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
def test_layout_parses_fixture(layout_json):
    lay = Layout.from_json(layout_json)
    assert lay.target == "/dev/sda"
    assert lay.table == "gpt"
    assert lay.slot("a").partition == "/dev/sda2"
    assert lay.slot("a").type == "root"
    assert lay.esp.partition == "/dev/sda1"
    assert lay.esp.index == 1
    assert lay.data.partition == "/dev/sda4"
    assert lay.data.index == 4


def test_layout_assert_has_slot_passes_for_declared(layout_json):
    lay = Layout.from_json(layout_json)
    lay.assert_has_slot("a")  # must not raise


def test_layout_assert_has_slot_rejects_unknown(layout_json):
    lay = Layout.from_json(layout_json)
    with pytest.raises(LayoutError):
        lay.assert_has_slot("z")


def test_release_slot_cross_checked_against_layout(release_json, layout_json):
    # The planning step cross-checks the release's slot against the declared
    # slots; here we exercise that seam directly.
    rel = Release.from_json(release_json)
    lay = Layout.from_json(layout_json)
    lay.assert_has_slot(rel.slot)  # "a" is declared -> ok


def test_layout_bad_json_raises_layout_error():
    with pytest.raises(LayoutError):
        Layout.from_json("nope")


def test_layout_rejects_unknown_table():
    text = (
        '{"target": "/dev/sda", "table": "btr", "active_slot": "a", '
        '"slots": {"a": {"partition": "/dev/sda2", "index": 2}}, '
        '"esp": {"partition": "/dev/sda1", "index": 1}, '
        '"data": {"partition": "/dev/sda4", "index": 4}}'
    )
    with pytest.raises(LayoutError):
        Layout.from_json(text)


def test_layout_rejects_unknown_active_slot():
    # `active_slot` is no longer a layout field (the release's slot is
    # authoritative); an unknown/extra field is tolerated, not rejected.
    text = (
        '{"target": "/dev/sda", "table": "gpt", "active_slot": "z", '
        '"slots": {"a": {"partition": "/dev/sda2", "index": 2}}, '
        '"esp": {"partition": "/dev/sda1", "index": 1}, '
        '"data": {"partition": "/dev/sda4", "index": 4}}'
    )
    lay = Layout.from_json(text)
    assert lay.target == "/dev/sda"


def test_layout_tolerates_extra_top_level_fields():
    text = (
        '{"target": "/dev/sda", "table": "gpt", "note": "hi", '
        '"slots": {"a": {"partition": "/dev/sda2", "index": 2}}, '
        '"esp": {"partition": "/dev/sda1", "index": 1}, '
        '"data": {"partition": "/dev/sda4", "index": 4}}'
    )
    lay = Layout.from_json(text)
    assert lay.to_json() != text  # extra field not echoed back


def test_layout_rejects_duplicate_indices():
    text = (
        '{"target": "/dev/sda", "table": "gpt", "active_slot": "a", '
        '"slots": {"a": {"partition": "/dev/sda2", "index": 2}}, '
        '"esp": {"partition": "/dev/sda1", "index": 2}, '
        '"data": {"partition": "/dev/sda4", "index": 4}}'
    )
    with pytest.raises(LayoutError):
        Layout.from_json(text)


def test_layout_rejects_missing_target():
    text = (
        '{"table": "gpt", "active_slot": "a", '
        '"slots": {"a": {"partition": "/dev/sda2", "index": 2}}, '
        '"esp": {"partition": "/dev/sda1", "index": 1}, '
        '"data": {"partition": "/dev/sda4", "index": 4}}'
    )
    with pytest.raises(LayoutError):
        Layout.from_json(text)


def test_layout_roundtrips_through_json(layout_json):
    lay = Layout.from_json(layout_json)
    assert Layout.from_json(lay.to_json()) == lay


def test_layout_is_immutable(layout_json):
    lay = Layout.from_json(layout_json)
    with pytest.raises(AttributeError):
        lay.target = "/dev/sdb"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# ProvisionConfig
# --------------------------------------------------------------------------- #
def test_provision_parses_fixture(install_toml):
    cfg = ProvisionConfig.from_toml(install_toml)
    assert cfg.hostname == "alex-pengyos"
    assert cfg.timezone.zone == "America/Toronto"
    assert cfg.network.manager == "systemd"
    assert [u.name for u in cfg.users] == ["alex", "root"]
    assert cfg.primary_user.name == "alex"
    holder = cfg.users[0]
    assert holder.shell == "/bin/bash"
    assert holder.groups == ["wheel", "audio", "video"]
    assert holder.ssh_keys == [
        "ssh-ed25519 AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8= example@alex"
    ]
    assert cfg.users[1].ssh_keys == []


def test_provision_bad_toml_raises_config_error():
    with pytest.raises(ConfigError):
        ProvisionConfig.from_toml("hostname = ")  # invalid TOML


def test_provision_missing_required_section():
    with pytest.raises(ConfigError):
        ProvisionConfig.from_toml('hostname = "h"\n[network]\nmanager = "systemd"\n')


def test_provision_rejects_bad_timezone():
    text = (
        'hostname = "h"\n[timezone]\nzone = "Mars/Phobos"\n'
        '[network]\nmanager = "systemd"\n'
        '[[users]]\nname = "u"\nshell = "/bin/bash"\ngroups = []\nssh_keys = []\n'
    )
    with pytest.raises(ConfigError):
        ProvisionConfig.from_toml(text)


def test_provision_rejects_bad_ssh_key():
    text = (
        'hostname = "h"\n[timezone]\nzone = "America/Toronto"\n'
        '[network]\nmanager = "systemd"\n'
        '[[users]]\nname = "u"\nshell = "/bin/bash"\ngroups = []\n'
        'ssh_keys = ["not-a-valid-key"]\n'
    )
    with pytest.raises(ConfigError):
        ProvisionConfig.from_toml(text)


def test_provision_rejects_unknown_ssh_key_type():
    text = (
        'hostname = "h"\n[timezone]\nzone = "America/Toronto"\n'
        '[network]\nmanager = "systemd"\n'
        '[[users]]\nname = "u"\nshell = "/bin/bash"\ngroups = []\n'
        'ssh_keys = ["ssh-bogus AAAABlob user@host"]\n'
    )
    with pytest.raises(ConfigError):
        ProvisionConfig.from_toml(text)


def test_provision_rejects_duplicate_users():
    text = (
        'hostname = "h"\n[timezone]\nzone = "America/Toronto"\n'
        '[network]\nmanager = "systemd"\n'
        '[[users]]\nname = "u"\nshell = "/bin/bash"\ngroups = []\nssh_keys = []\n'
        '[[users]]\nname = "u"\nshell = "/bin/bash"\ngroups = []\nssh_keys = []\n'
    )
    with pytest.raises(ConfigError):
        ProvisionConfig.from_toml(text)


def test_provision_rejects_empty_users():
    text = (
        'hostname = "h"\n[timezone]\nzone = "America/Toronto"\n'
        '[network]\nmanager = "systemd"\nusers = []\n'
    )
    with pytest.raises(ConfigError):
        ProvisionConfig.from_toml(text)


def test_provision_rejects_whitespace_hostname():
    text = (
        'hostname = "has space"\n[timezone]\nzone = "America/Toronto"\n'
        '[network]\nmanager = "systemd"\n'
        '[[users]]\nname = "u"\nshell = "/bin/bash"\ngroups = []\nssh_keys = []\n'
    )
    with pytest.raises(ConfigError):
        ProvisionConfig.from_toml(text)


def test_provision_roundtrips_through_toml(install_toml):
    cfg = ProvisionConfig.from_toml(install_toml)
    again = ProvisionConfig.from_toml(cfg.to_toml())
    assert again == cfg


def test_provision_extra_keys_are_preserved():
    # A bare key at the very top (before any [table] header) is a top-level key.
    text = (
        'extra_flag = true\n'
        'hostname = "h"\n[timezone]\nzone = "America/Toronto"\n'
        '[network]\nmanager = "systemd"\n'
        '[[users]]\nname = "u"\nshell = "/bin/bash"\ngroups = []\nssh_keys = []\n'
    )
    cfg = ProvisionConfig.from_toml(text)
    assert cfg.extra == {"extra_flag": True}


def test_provision_from_file(tmp_path, install_toml, write_file):
    path = write_file("install.toml", install_toml)
    cfg = ProvisionConfig.from_toml_file(path)
    assert cfg.hostname == "alex-pengyos"


def test_errors_share_the_validation_base():
    assert issubclass(ReleaseError, ValidationError)
    assert issubclass(LayoutError, ValidationError)
    assert issubclass(ConfigError, ValidationError)


# --------------------------------------------------------------------------- #
# Step / Plan (the install plan as validated data)
# --------------------------------------------------------------------------- #
def test_step_from_argv_strips_to_list():
    step = Step.from_argv(["dd", "if=x", "of=/dev/sda2"])
    assert step.argv == ("dd", "if=x", "of=/dev/sda2")
    assert step.to_argv() == ["dd", "if=x", "of=/dev/sda2"]
    assert step.description == ""
    assert step.env == ()


def test_step_from_argv_requires_non_empty():
    with pytest.raises(ValidationError):
        Step.from_argv([])


def test_step_shlex_join_quotes_spaces():
    step = Step.from_argv(["install", "a b", "c=d"])
    assert step.shlex_join() == "install 'a b' c=d"


def test_step_carries_description_and_env():
    step = Step.from_argv(["mkfs", "/dev/sda2"], description="format root", env={"X": "1"})
    assert step.description == "format root"
    assert step.env == (("X", "1"),)
    assert step.to_json() == {
        "argv": ["mkfs", "/dev/sda2"],
        "description": "format root",
        "env": {"X": "1"},
    }


def test_step_redirect_is_not_in_argv():
    """A redirect is a shell-script concern, not an argument: it is excluded
    from ``argv`` (a ``>`` is a metacharacter, never an argument)."""
    step = Step.from_argv(["printf", "%s", "content"], redirect="/etc/fstab")
    assert step.argv == ("printf", "%s", "content")
    assert step.redirect == "/etc/fstab"
    # the runner consumes a clean argv (content to stdout, no redirect)
    assert step.to_argv() == ["printf", "%s", "content"]


def test_step_redirect_renders_in_shell_script():
    """The shell script appends ``> '<redirect>'`` after the argv."""
    step = Step.from_argv(["printf", "%s", "content"], redirect="/etc/fstab")
    assert step.shlex_join() == "printf %s content"
    script = Plan.from_steps([step]).to_shell_script()
    assert "printf %s content > /etc/fstab" in script
    # a path with a space is quoted by shlex.quote so the redirect is safe
    spaced = Step.from_argv(["printf", "%s", "content"], redirect="/my dir/f")
    spaced_script = Plan.from_steps([spaced]).to_shell_script()
    assert "printf %s content > '/my dir/f'" in spaced_script


def test_step_redirect_roundtrips_through_json():
    step = Step.from_argv(["printf", "%s", "content"], redirect="/etc/fstab")
    assert step.to_json()["redirect"] == "/etc/fstab"
    again = Step.from_argv(**step.to_json())
    assert again == step


def test_step_without_redirect_has_empty_redirect():
    step = Step.from_argv(["dd", "if=x"])
    assert step.redirect == ""
    assert "redirect" not in step.to_json()


def test_step_is_immutable():
    step = Step.from_argv(["ls"])
    with pytest.raises(AttributeError):
        step.argv = ("rm",)  # type: ignore[misc]


def test_step_str_is_shell_join():
    assert str(Step.from_argv(["echo", "hi"])) == "echo hi"


def test_plan_from_steps_preserves_order():
    steps = [
        Step.from_argv(["a"]),
        Step.from_argv(["b"]),
        Step.from_argv(["c"]),
    ]
    plan = Plan.from_steps(steps)
    assert [s.argv[0] for s in plan.steps] == ["a", "b", "c"]
    assert len(plan) == 3
    assert plan[0].argv == ("a",)
    assert plan[-1].argv == ("c",)


def test_plan_json_is_deterministic_and_roundtrips():
    plan = Plan.from_steps([
        Step.from_argv(["dd", "if=x", "of=/dev/sda2"], description="write rootfs"),
        Step.from_argv(["dd", "if=y", "of=/dev/sda1"]),
    ])
    text = plan.to_json()
    assert Plan.from_json(text) == plan
    assert plan.to_json() == text  # stable across serializations


def test_plan_json_rejects_non_array():
    with pytest.raises(ValidationError):
        Plan.from_json('{"not": "a list"}')


def test_plan_json_rejects_step_without_argv():
    with pytest.raises(ValidationError):
        Plan.from_json('[{"description": "no argv here"}]')


def test_plan_json_rejects_non_dict_item():
    with pytest.raises(ValidationError):
        Plan.from_json('["not-a-dict"]')


def test_plan_shell_script_has_shebang_and_one_line_per_step():
    plan = Plan.from_steps([
        Step.from_argv(["parted", "mkpart"], description="make partition"),
        Step.from_argv(["mkfs", "-t", "ext4", "/dev/sda2"]),
    ])
    script = plan.to_shell_script()
    assert script.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in script
    assert script.count("parted") == 1
    assert script.count("mkfs") == 1
    assert "# 1. make partition" in script
