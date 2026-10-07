"""Tests for :mod:`pengyos_debx.analyse`.

Covers: Implement DT_NEEDED analysis via pure parsing of ELF headers.

The ELF fixtures are built in-test (see :mod:`test_elf`); the analyse layer is
exercised against a single ELF file and against a synthetic prefix directory.
No network, root, ``ldd``, or external tooling is involved.
"""

from pathlib import Path

import pytest

from pengyos_debx.analyse import AnalysisReport, analyse_path
from pengyos_debx.elf import parse_elf

# Reuse the byte-exact ELF builders from test_elf.
from test_elf import _build_elf32, _build_elf64  # noqa: E402


@pytest.fixture
def elf64_bytes():
    return _build_elf64(endian="<", needed=["libc.so.6", "libm.so.6"])


@pytest.fixture
def rootfs(tmp_path: Path):
    """A synthetic extracted prefix with two ELF objects and a non-ELF file."""
    root = tmp_path / "rootfs"
    (root / "usr").mkdir(parents=True)
    (root / "usr" / "lib").mkdir()
    (root / "usr" / "lib" / "app").write_bytes(
        _build_elf64(endian="<", needed=["libc.so.6", "libm.so.6"])
    )
    (root / "usr" / "lib" / "plugin").write_bytes(
        _build_elf32(endian="<", needed=["libpthread.so.0"])
    )
    # A non-ELF file that merely looks like a library by extension.
    (root / "usr" / "lib" / "README").write_text("not a library")
    return root


# --- analyse_path ------------------------------------------------------------


def test_analyse_single_file(rootfs):
    elf = rootfs / "usr" / "lib" / "app"
    report = analyse_path(str(elf))
    assert report.files_scanned == 1
    assert report.entries[0].dt_needed == ["libc.so.6", "libm.so.6"]


def test_analyse_prefix(rootfs):
    report = analyse_path(str(rootfs))
    # Only the two ELF objects are scanned; the README is skipped.
    assert report.files_scanned == 2
    names = {e.path.split("/")[-1] for e in report.entries}
    assert names == {"app", "plugin"}


def test_unique_needed_union_and_sort(rootfs):
    report = analyse_path(str(rootfs))
    # Union of libc.so.6, libm.so.6, libpthread.so.0, sorted.
    assert report.unique_needed() == [
        "libc.so.6",
        "libm.so.6",
        "libpthread.so.0",
    ]


def test_libdirs_recorded(rootfs):
    report = analyse_path(str(rootfs), libdirs=["/lib", "/usr/lib"])
    assert report.libdirs == ["/lib", "/usr/lib"]


def test_analyse_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        analyse_path(str(tmp_path / "nope.so"))


# --- report formatting -------------------------------------------------------


def test_report_format_lists_dependencies(rootfs, capsys):
    report = analyse_path(str(rootfs))
    out = report.format()
    assert "DT_NEEDED" in out
    assert "app" in out
    assert "libc.so.6" in out
    assert "libm.so.6" in out
    assert "libpthread.so.0" in out
    assert "2 ELF file(s) scanned" in out


def test_report_format_single_dependency_pluralization(tmp_path):
    p = tmp_path / "solo.so"
    # A single DT_NEEDED -> "1 dependency" (singular), not "dependencies".
    p.write_bytes(_build_elf64(endian="<", needed=["libc.so.6"]))
    report = analyse_path(str(p))
    out = report.format()
    assert "1 dependency:" in out
    assert "libc.so.6" in out


def test_report_format_no_dependencies(tmp_path):
    p = tmp_path / "static.o"
    # No dynamic segment -> no declared dependencies.
    from test_elf import _build_elf64

    p.write_bytes(_build_elf64(endian="<", with_dynamic=False))
    report = analyse_path(str(p))
    out = report.format()
    assert "(no dynamic dependencies)" in out
    assert "1 ELF file(s) scanned" in out


def test_report_format_libdirs_note(rootfs):
    # The old "not yet performed" note is gone: resolution now happens.
    report = analyse_path(str(rootfs), libdirs=["/lib"])
    out = report.format()
    assert "--libdirs resolution is not yet performed" not in out
    assert "resolved vs missing" in out


# --- resolved vs missing resolution ------------------------------------------


def _libdir_with(libdir: Path, names: list[str]) -> str:
    """Create a fake libdir: a directory containing one file per lib name.

    Returns the absolute path of the libdir. The library *files* are empty ELF
    objects (they only need to exist on disk for the existence check).
    """
    libdir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (libdir / name).write_bytes(_build_elf64(endian="<", needed=[]))
    return str(libdir)


def test_resolution_single_file_resolved_and_missing(tmp_path):
    p = tmp_path / "app"
    p.write_bytes(_build_elf64(endian="<", needed=["libc.so.6", "libm.so.6"]))
    # Provide libc.so.6 but not libm.so.6.
    libdir = _libdir_with(tmp_path / "libroot" / "lib", ["libc.so.6"])
    report = analyse_path(str(p), libdirs=[libdir])
    res = report.resolution()[0]
    assert res.resolved == ("libc.so.6",)
    assert res.missing == ("libm.so.6",)
    assert report.missing_libs() == ["libm.so.6"]


def test_resolution_prefix_per_object(tmp_path, rootfs):
    libdir_a = _libdir_with(
        tmp_path / "libroot" / "lib", ["libc.so.6"]
    )
    libdir_b = _libdir_with(
        tmp_path / "libroot" / "usr_lib",
        ["libm.so.6", "libpthread.so.0"],
    )
    report = analyse_path(str(rootfs), libdirs=[libdir_a, libdir_b])
    by_name = {e.path.split("/")[-1]: e for e in report.resolution()}
    # app needs libc.so.6 (present in lib) + libm.so.6 (present in usr/lib).
    assert by_name["app"].resolved == ("libc.so.6", "libm.so.6")
    assert by_name["app"].missing == ()
    # plugin needs libpthread.so.0 (present in usr/lib).
    assert by_name["plugin"].resolved == ("libpthread.so.0",)
    assert by_name["plugin"].missing == ()
    assert report.missing_libs() == []


def test_resolution_missing_lib_reported(tmp_path, rootfs):
    # Only libc.so.6 exists; libm.so.6 and libpthread.so.0 are missing.
    libdir = _libdir_with(tmp_path / "libroot" / "lib", ["libc.so.6"])
    report = analyse_path(str(rootfs), libdirs=[libdir])
    by_name = {e.path.split("/")[-1]: e for e in report.resolution()}
    assert by_name["app"].missing == ("libm.so.6",)
    assert by_name["plugin"].resolved == ()
    assert by_name["plugin"].missing == ("libpthread.so.0",)
    assert report.missing_libs() == ["libm.so.6", "libpthread.so.0"]


def test_resolution_symlinked_soname_counts_as_present(tmp_path):
    p = tmp_path / "app"
    p.write_bytes(_build_elf64(endian="<", needed=["libc.so.6"]))
    libdir = tmp_path / "libroot" / "lib"
    libdir.mkdir(parents=True)
    # libc.so.6 is a symlink to the real versioned object — the loader resolves
    # the soname via the symlink, so lexists must count it as present.
    (libdir / "libc.so.6").symlink_to("libc-2.38.so")
    report = analyse_path(str(p), libdirs=[str(libdir)])
    res = report.resolution()[0]
    assert res.resolved == ("libc.so.6",)
    assert res.missing == ()


def test_resolution_is_exact_name_case_sensitive(tmp_path):
    p = tmp_path / "app"
    p.write_bytes(_build_elf64(endian="<", needed=["libc.so.6", "LibC.So.6"]))
    # A dir that happens to contain libc.so.6 must NOT satisfy LibC.So.6.
    libdir = _libdir_with(tmp_path / "libroot" / "lib", ["libc.so.6"])
    report = analyse_path(str(p), libdirs=[libdir])
    res = report.resolution()[0]
    assert res.resolved == ("libc.so.6",)
    assert res.missing == ("LibC.So.6",)


def test_resolution_absent_libdir_flagged(tmp_path):
    p = tmp_path / "app"
    p.write_bytes(_build_elf64(endian="<", needed=["libc.so.6"]))
    # Point at a libdir that does not exist on disk.
    report = analyse_path(str(p), libdirs=[str(tmp_path / "does-not-exist")])
    res = report.resolution()[0]
    assert res.resolved == ()
    assert res.missing == ("libc.so.6",)
    out = report.format()
    assert "not present on disk" in out


def test_resolution_no_libdirs_reports_all_missing(tmp_path):
    p = tmp_path / "app"
    p.write_bytes(_build_elf64(endian="<", needed=["libc.so.6", "libm.so.6"]))
    report = analyse_path(str(p))  # no libdirs
    res = report.resolution()[0]
    assert res.resolved == ()
    assert res.missing == ("libc.so.6", "libm.so.6")
    # With no libdirs there is nothing to resolve against, so the "missing"
    # union is empty by contract (use unique_needed instead).
    assert report.missing_libs() == []


def test_resolution_multiple_libdirs_union(tmp_path):
    p = tmp_path / "app"
    p.write_bytes(_build_elf64(endian="<", needed=["libc.so.6", "libm.so.6"]))
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    (root / "a" / "libc.so.6").write_bytes(b"x")
    (root / "b" / "libm.so.6").write_bytes(b"x")
    report = analyse_path(str(p), libdirs=[str(root / "a"), str(root / "b")])
    res = report.resolution()[0]
    assert sorted(res.resolved) == ["libc.so.6", "libm.so.6"]
    assert res.missing == ()


def test_resolution_format_lists_resolved_and_missing(tmp_path):
    p = tmp_path / "app"
    p.write_bytes(_build_elf64(endian="<", needed=["libc.so.6", "libm.so.6"]))
    libdir = _libdir_with(tmp_path / "libroot" / "lib", ["libc.so.6"])
    report = analyse_path(str(p), libdirs=[libdir])
    out = report.format()
    assert "resolved vs missing against libdirs" in out
    assert "Resolved: 1 occurrence(s)." in out
    assert "Missing: 1 occurrence(s)." in out
    assert "libm.so.6" in out


def test_resolution_never_shells_out(
    tmp_path, monkeypatch
):
    """Resolution must only stat the filesystem — never run a process."""
    import subprocess as _subprocess

    calls = []

    def _fake_run(*args, **kwargs):
        calls.append(args)
        raise AssertionError("resolution must not shell out")

    monkeypatch.setattr(_subprocess, "run", _fake_run)

    p = tmp_path / "app"
    p.write_bytes(_build_elf64(endian="<", needed=["libc.so.6"]))
    libdir = _libdir_with(tmp_path / "libroot" / "lib", ["libc.so.6"])
    report = analyse_path(str(p), libdirs=[libdir])
    report.resolution()
    report.format()
    assert calls == []
