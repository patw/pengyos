"""Reproducibility self-check: build twice → identical sha256.

The byte-identical property is asserted pairwise elsewhere; here we state it in its
intended **sha256** form and, crucially, we verify it the way an operator would:

* :func:`pengyos_initramfs.builder.build_archive` invoked twice in one process
  yields the same sha256 digest (the wall clock is never read).
* The real CLI (``pengyos-initramfs build``) invoked **twice in independent
  subprocesses** writes ``.cpio.gz`` files whose sha256 digests match. A
  separate process is the strongest no-root, no-network self-check: any
  nondeterminism (wall-clock mtime, dict/set iteration order, random inode,
  gzip FNAME/MTIME) would surface as a digest mismatch.

No network, no root, no display, no real hardware — only a temp dir and a
subprocess of the interpreter we are already running under.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_initramfs.builder import build_archive
from pengyos_initramfs.spec import parse_spec

TOOL_DIR = Path(__file__).resolve().parent.parent


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_spec(**overrides):
    data = {
        "binaries": ["bin/busybox"],
        "files": ["etc/os-release", "etc/fstab"],
        "modules": ["lib/modules/6.1.0/extra/loop.ko"],
        "build": {"mtime": "2000-01-01T00:00:00Z", "uid": 0, "gid": 0},
    }
    data.update(overrides)
    return parse_spec(data)


@pytest.fixture
def root_tree(tmp_path):
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "bin/busybox").write_bytes(b"\x7fELF-busybox-stub\n")
    (root / "etc").mkdir()
    (root / "etc/os-release").write_bytes(b"ID=pengyos\nNAME=\"PengyOS\"\n")
    (root / "etc/fstab").write_bytes(b"# /etc/fstab\n")
    (root / "lib/modules/6.1.0/extra").mkdir(parents=True)
    (root / "lib/modules/6.1.0/extra/loop.ko").write_bytes(bytes(range(256)) * 4)
    return root


def test_build_twice_in_process_identical_sha256(root_tree):
    """Two in-process builds of the same spec+root have an identical sha256."""
    spec = make_spec()
    a = build_archive(spec, root_tree)
    b = build_archive(spec, root_tree)
    assert sha256_hex(a) == sha256_hex(b)
    # Guard against a degenerate empty digest comparison: the archive is real.
    assert len(a) > 0
    assert sha256_hex(a) != sha256_hex(b"")


def test_build_twice_with_fresh_specs_identical_sha256(root_tree):
    """Re-parsing the spec from scratch still yields the identical sha256,
    so the digest is a function of the *spec's values*, not of any object
    identity or ordering that happens to be stable within one parse."""
    a = build_archive(make_spec(), root_tree)
    b = build_archive(make_spec(), root_tree)
    assert sha256_hex(a) == sha256_hex(b)


def _run_cli(root: Path, spec_path: Path, out: Path) -> int:
    """Invoke the tool as an independent subprocess (the real reproducibility unit)."""
    # PYTHONPATH points at the tool dir so the package imports without install,
    # mirroring how the test suite itself finds it.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pengyos_initramfs",
            "build",
            "--root",
            str(root),
            "--spec",
            str(spec_path),
            "--out",
            str(out),
        ],
        cwd=str(TOOL_DIR),
        capture_output=True,
        text=True,
    )
    return proc.returncode


def test_cli_two_independent_process_builds_identical_sha256(root_tree, tmp_path):
    """Two separate ``pengyos-initramfs`` process invocations produce .cpio.gz
    files with identical sha256 — the strongest no-root reproducibility proof."""
    spec_path = tmp_path / "initramfs.toml"
    spec_path.write_text(
        "binaries = [\"bin/busybox\"]\n"
        "files = [\"etc/os-release\", \"etc/fstab\"]\n"
        "modules = [\"lib/modules/6.1.0/extra/loop.ko\"]\n"
        "build.mtime = \"2000-01-01T00:00:00Z\"\n",
        encoding="utf-8",
    )

    out1 = tmp_path / "a.cpio.gz"
    out2 = tmp_path / "b.cpio.gz"

    code1 = _run_cli(root_tree, spec_path, out1)
    code2 = _run_cli(root_tree, spec_path, out2)
    assert code1 == 0
    assert code2 == 0

    d1, d2 = sha256_hex(out1.read_bytes()), sha256_hex(out2.read_bytes())
    assert d1 == d2, f"reproducibility check failed: {d1} != {d2}"


def test_cli_rerun_overwrites_identical_sha256(root_tree, tmp_path):
    """A re-run writing to the *same* output path leaves a file whose sha256
    matches a fresh build to a different path (no leftover/append nondeterminism)."""
    spec_path = tmp_path / "initramfs.toml"
    spec_path.write_text(
        "binaries = [\"bin/busybox\"]\n"
        "files = [\"etc/os-release\"]\n",
        encoding="utf-8",
    )

    same = tmp_path / "init.cpio.gz"
    other = tmp_path / "other.cpio.gz"

    assert _run_cli(root_tree, spec_path, same) == 0
    assert _run_cli(root_tree, spec_path, other) == 0
    assert _run_cli(root_tree, spec_path, same) == 0  # rerun over the same path

    assert sha256_hex(same.read_bytes()) == sha256_hex(other.read_bytes())
