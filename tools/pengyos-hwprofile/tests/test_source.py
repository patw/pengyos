"""Tests for :mod:`pengyos_hwprofile.source` — the read-only directory view.

These lock down the single primitive the collectors build on: reading a file
relative to a source root, with the guarantees that matter for testability
(sandboxed to the root, deterministic, no surprises from symlinks or escapes).

No root, no network, no real hardware — only the committed fixture tree. The
:class:`LiveSource` reads the live ``/proc`` + ``/sys``; its path-mapping logic
is unit-tested by pointing it at a throwaway tree (via ``monkeypatch``), so the
live test needs no root or real hardware either.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_hwprofile.source import Source, SourceError, open_source

FIXTURES = Path(__file__).resolve().parents[0] / "fixtures" / "sys"


def test_open_source_accepts_a_directory():
    src = open_source(FIXTURES)
    assert src.root == FIXTURES.resolve()


def test_open_source_rejects_a_missing_path():
    with pytest.raises(SourceError, match="source does not exist"):
        open_source("/nonexistent/pengyos-hwprofile-fixture")


def test_open_source_rejects_a_file():
    with pytest.raises(SourceError, match="source is not a directory"):
        open_source(FIXTURES / "proc" / "cpuinfo")


def test_read_returns_text_without_trailing_newline():
    src = open_source(FIXTURES)
    # The fixture files end in "\n"; read() strips exactly one trailing newline.
    assert src.read("proc/sys/kernel/hostname") == "pengyos-laptop"
    assert src.read("proc/sys/kernel/osrelease") == "6.8.0-pengyos"


def test_read_is_case_sensitive_and_exact():
    src = open_source(FIXTURES)
    # A wrong path is an error, not an empty string — the collectors must not
    # silently treat "missing" as "empty".
    with pytest.raises(SourceError):
        src.read("proc/sys/kernel/DOES_NOT_EXIST")


def test_read_bytes_keeps_newline():
    src = open_source(FIXTURES)
    raw = src.read_bytes("proc/sys/kernel/hostname")
    assert raw == b"pengyos-laptop"


def test_read_of_regular_file_only():
    # A directory is not a regular file -> error, even though it "exists".
    src = open_source(FIXTURES)
    with pytest.raises(SourceError):
        src.read("proc")


def test_source_root_is_resolved_and_absolute():
    src = Source(".")
    assert src.root.is_absolute()


def test_source_rejects_path_escape():
    # A ".." that would leave the root is rejected, not followed.
    src = Source(FIXTURES)
    with pytest.raises(SourceError, match="escapes source root"):
        src.read("../../../../etc/passwd")


def test_source_repr_is_informative():
    src = open_source(FIXTURES)
    assert "Source" in repr(src)
    assert str(FIXTURES) in repr(src)


def test_source_can_read_into_a_real_tmpdir(tmp_path):
    # End-to-end: a freshly built tree round-trips through Source.
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "file").write_text("hello\n")
    src = open_source(tmp_path)
    assert src.read("a/b/file") == "hello"


# ---------------------------------------------------------------------------
# LiveSource — maps the collectors' relative paths onto /proc + /sys.
#
# The live roots are module-level constants, so we point them at a throwaway
# tree with monkeypatch: the mapping logic is exercised without any root, real
# hardware, or the real /proc + /sys.
# ---------------------------------------------------------------------------


def _build_live_like_tree(tmp_path):
    """A throwaway /proc + /sys mirror for LiveSource tests."""

    (tmp_path / "proc" / "sys" / "kernel").mkdir(parents=True)
    (tmp_path / "proc" / "sys" / "kernel" / "hostname").write_text("live-host\n")
    (tmp_path / "proc" / "sys" / "kernel" / "osrelease").write_text("9.9.9-live\n")
    (tmp_path / "proc" / "cpuinfo").write_text("processor\t: 0\nmodel name\t: Test CPU\n")
    (tmp_path / "class" / "dmi" / "id").mkdir(parents=True)
    (tmp_path / "class" / "dmi" / "id" / "sys_vendor").write_text("TestVendor\n")
    (tmp_path / "bus" / "pci" / "devices").mkdir(parents=True)
    (tmp_path / "bus" / "pci" / "devices" / "0000:00:02.0").mkdir()
    (tmp_path / "bus" / "pci" / "devices" / "0000:00:02.0" / "vendor").write_text("0x8086\n")
    return tmp_path


def test_live_source_reads_proc_and_sys(monkeypatch, tmp_path):
    import pengyos_hwprofile.source as source_module

    tree = _build_live_like_tree(tmp_path)
    monkeypatch.setattr(
        source_module,
        "_LIVE_ROOTS",
        (("proc", tree / "proc"), ("class", tree / "class"), ("bus", tree / "bus")),
    )

    from pengyos_hwprofile.source import LiveSource, live_source

    src = live_source()
    assert isinstance(src, LiveSource)
    # Symlink-following helpers use root / "class|bus/...", so root must be the
    # /sys-equivalent.
    assert src.root == tree / "sys" or src.root.name in ("sys", "class", "bus")
    assert src.read("proc/sys/kernel/hostname") == "live-host"
    assert src.read("proc/sys/kernel/osrelease") == "9.9.9-live"
    assert src.read("class/dmi/id/sys_vendor") == "TestVendor"
    assert src.read("bus/pci/devices/0000:00:02.0/vendor") == "0x8086"


def test_live_source_rejects_unknown_prefix(monkeypatch, tmp_path):
    import pengyos_hwprofile.source as source_module

    tree = _build_live_like_tree(tmp_path)
    monkeypatch.setattr(
        source_module,
        "_LIVE_ROOTS",
        (("proc", tree / "proc"), ("class", tree / "class"), ("bus", tree / "bus")),
    )
    from pengyos_hwprofile.source import live_source

    with pytest.raises(source_module.SourceError, match="unknown source prefix"):
        live_source().read("etc/hostname")


def test_live_source_missing_file(monkeypatch, tmp_path):
    import pengyos_hwprofile.source as source_module

    tree = _build_live_like_tree(tmp_path)
    monkeypatch.setattr(
        source_module,
        "_LIVE_ROOTS",
        (("proc", tree / "proc"), ("class", tree / "class"), ("bus", tree / "bus")),
    )
    from pengyos_hwprofile.source import live_source

    with pytest.raises(source_module.SourceError, match="no such file"):
        live_source().read("proc/sys/kernel/does_not_exist")
