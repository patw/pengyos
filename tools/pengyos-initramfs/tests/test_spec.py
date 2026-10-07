"""Tests for spec parsing (:mod:`pengyos_initramfs.spec`)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from pengyos_initramfs.spec import (
    DEFAULT_MTIME,
    Spec,
    SpecError,
    load_spec,
    parse_spec,
)


# --- include lists --------------------------------------------------------


def test_empty_spec_gives_defaults():
    s = parse_spec({})
    assert s.all_paths() == ()
    assert s.uid == 0 and s.gid == 0
    assert s.user == "root" and s.group == "root"
    assert s.mtime == DEFAULT_MTIME
    assert s.init_path == "init"
    assert s.init_mode == 0o755


def test_parse_all_three_kinds():
    s = parse_spec(
        {
            "binaries": ["bin/busybox"],
            "files": ["etc/os-release"],
            "modules": ["lib/modules/6.1/loop.ko"],
        }
    )
    assert s.binaries == ("bin/busybox",)
    assert s.files == ("etc/os-release",)
    assert s.modules == ("lib/modules/6.1/loop.ko",)
    # all_paths is the de-duplicated, sorted union.
    assert s.all_paths() == (
        "bin/busybox",
        "etc/os-release",
        "lib/modules/6.1/loop.ko",
    )


def test_all_paths_deduplicates_and_sorts():
    s = parse_spec({"binaries": ["b/x", "a/y"], "files": ["a/y"]})
    assert s.all_paths() == ("a/y", "b/x")


def test_path_normalization_strips_dot_slash():
    s = parse_spec({"binaries": ["./usr/bin/sh"]})
    assert s.binaries == ("usr/bin/sh",)


def test_paths_for_kind():
    s = parse_spec({"binaries": ["bin/a"], "files": ["etc/f"]})
    assert s.paths_for("binaries") == ("bin/a",)
    assert s.paths_for("files") == ("etc/f",)
    assert s.paths_for("modules") == ()
    assert s.paths_for("directories") == ()


# --- directories list -----------------------------------------------------


def test_directories_defaults_empty():
    s = parse_spec({})
    assert s.directories == ()


def test_parse_directories_list():
    s = parse_spec({"files": ["etc/os-release"], "directories": ["etc"]})
    assert s.directories == ("etc",)


def test_directories_paths_normalized():
    s = parse_spec({"directories": ["./etc", "lib/modules/6.1.0"]})
    assert s.directories == ("etc", "lib/modules/6.1.0")


def test_all_paths_includes_directories():
    s = parse_spec(
        {
            "files": ["etc/os-release"],
            "directories": ["etc"],
        }
    )
    assert s.all_paths() == ("etc", "etc/os-release")


def test_all_paths_dedupes_directory_against_file_listings():
    # Same path in two lists is packed once.
    s = parse_spec({"files": ["etc"], "directories": ["etc"]})
    assert s.all_paths() == ("etc",)
    assert s.paths_for("directories") == ("etc",)
    assert s.paths_for("files") == ("etc",)


def test_directories_must_be_array_of_strings():
    with pytest.raises(SpecError):
        parse_spec({"directories": "etc"})  # string, not array
    with pytest.raises(SpecError):
        parse_spec({"directories": {"a": "b"}})  # table, not array
    with pytest.raises(SpecError):
        parse_spec({"directories": [123]})


@pytest.mark.parametrize(
    "bad",
    ["/etc", "../etc", "a/../../b", "", "a\\b"],
)
def test_directories_reject_bad_paths(bad):
    with pytest.raises(SpecError):
        parse_spec({"directories": [bad]})


# --- path validation ------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "/abs/path",  # absolute
        "../etc/passwd",  # escapes root
        "a/../../x",  # escapes root
        "",  # empty
        "   ",  # whitespace-only
        "a\\b",  # backslash
        "a//b/..",  # trailing dotdot
    ],
)
def test_rejects_bad_paths(bad):
    with pytest.raises(SpecError):
        parse_spec({"binaries": [bad]})


def test_path_must_be_string():
    with pytest.raises(SpecError):
        parse_spec({"binaries": [123]})


def test_kind_must_be_array():
    with pytest.raises(SpecError):
        parse_spec({"files": "etc/os-release"})  # string, not array
    with pytest.raises(SpecError):
        parse_spec({"modules": {"a": "b"}})  # table, not array


# --- [build] normalization settings --------------------------------------


def test_build_mtime_parsed_to_utc_aware():
    s = parse_spec({"build": {"mtime": "2020-06-01T12:30:00Z"}})
    assert s.mtime == datetime(2020, 6, 1, 12, 30, 0, tzinfo=timezone.utc)
    assert s.mtime.tzinfo is not None


def test_build_mtime_naive_assumed_utc():
    s = parse_spec({"build": {"mtime": "2020-06-01T12:30:00"}})
    assert s.mtime == datetime(2020, 6, 1, 12, 30, 0, tzinfo=timezone.utc)


def test_build_mtime_offset_converted_to_utc():
    # +02:00 offset should be normalized to UTC.
    s = parse_spec({"build": {"mtime": "2020-06-01T14:30:00+02:00"}})
    assert s.mtime == datetime(2020, 6, 1, 12, 30, 0, tzinfo=timezone.utc)


def test_build_uid_gid_user_group():
    s = parse_spec(
        {
            "build": {
                "uid": 1000,
                "gid": 2000,
                "user": "nobody",
                "group": "nogroup",
            }
        }
    )
    assert s.uid == 1000 and s.gid == 2000
    assert s.user == "nobody" and s.group == "nogroup"


def test_bad_mtime_type():
    with pytest.raises(SpecError):
        parse_spec({"build": {"mtime": 12345}})


def test_bad_mtime_format():
    with pytest.raises(SpecError):
        parse_spec({"build": {"mtime": "not-a-date"}})


def test_uid_out_of_range():
    with pytest.raises(SpecError):
        parse_spec({"build": {"uid": -1}})
    with pytest.raises(SpecError):
        parse_spec({"build": {"uid": 2**32}})


def test_uid_must_be_int():
    with pytest.raises(SpecError):
        parse_spec({"build": {"uid": "1000"}})
    # bool is not an acceptable uid.
    with pytest.raises(SpecError):
        parse_spec({"build": {"uid": True}})


def test_user_must_be_nonempty_string():
    with pytest.raises(SpecError):
        parse_spec({"build": {"user": ""}})
    with pytest.raises(SpecError):
        parse_spec({"build": {"user": 5}})


# --- [init] generator settings ------------------------------------------


def test_init_defaults():
    s = parse_spec({})
    assert s.init_path == "init"
    assert s.init_mode == 0o755


def test_init_mode_octal_string():
    assert parse_spec({"init": {"mode": "0700"}}).init_mode == 0o700
    assert parse_spec({"init": {"mode": "4755"}}).init_mode == 0o4755


def test_init_mode_int():
    assert parse_spec({"init": {"mode": 0o755}}).init_mode == 0o755


def test_init_path_cleaned():
    assert parse_spec({"init": {"path": "./sbin/init"}}).init_path == "sbin/init"


def test_init_mode_out_of_range():
    with pytest.raises(SpecError):
        parse_spec({"init": {"mode": "010000"}})  # > 07777
    with pytest.raises(SpecError):
        parse_spec({"init": {"mode": -1}})


def test_init_mode_bad_octal():
    with pytest.raises(SpecError):
        parse_spec({"init": {"mode": "07zz"}})


def test_init_path_must_be_relative():
    with pytest.raises(SpecError):
        parse_spec({"init": {"path": "/init"}})


# --- file-level loading (load_spec) -------------------------------------


def test_load_spec_from_file(tmp_path):
    p = tmp_path / "initramfs.toml"
    p.write_text(
        "binaries = [\"bin/busybox\"]\n"
        "files = [\"etc/os-release\"]\n"
        "[build]\n"
        "mtime = \"2000-01-01T00:00:00Z\"\n"
        "uid = 0\n",
        encoding="utf-8",
    )
    s = load_spec(p)
    assert s.all_paths() == ("bin/busybox", "etc/os-release")
    assert s.mtime == DEFAULT_MTIME


def test_load_spec_missing_file(tmp_path):
    with pytest.raises(SpecError):
        load_spec(tmp_path / "nope.toml")


def test_load_spec_invalid_toml(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("binaries = [", encoding="utf-8")
    with pytest.raises(SpecError):
        load_spec(p)


def test_load_spec_non_utf8(tmp_path):
    p = tmp_path / "bin.toml"
    p.write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(SpecError):
        load_spec(p)


# --- Spec dataclass shape ------------------------------------------------


def test_spec_is_frozen():
    s = parse_spec({"files": ["etc/x"]})
    with pytest.raises(Exception):
        s.files = ("etc/y",)  # type: ignore[misc]


def test_spec_all_paths_uses_tuple():
    s = parse_spec({"files": ["etc/x"]})
    assert isinstance(s, Spec)
    assert isinstance(s.all_paths(), tuple)


# --- committed example is itself valid -----------------------------------


def test_committed_example_spec_parses():
    example = (
        Path(__file__).resolve().parent.parent / "examples" / "initramfs.toml"
    )
    assert example.is_file(), "examples/initramfs.toml is missing"
    s = load_spec(example)
    assert "bin/busybox" in s.binaries
    assert "etc/os-release" in s.files
    assert s.mtime == DEFAULT_MTIME
    assert s.init_mode == 0o755
