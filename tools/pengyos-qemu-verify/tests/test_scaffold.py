"""Scaffold-level tests.

Covers the scaffold deliverable:

* package metadata is coherent and matches the build metadata,
* the ``main`` entry point works (``--version``, help, not-yet-implemented
  subcommands, bad usage),
* the CLI argument surface (``boot`` / ``probe``) is registered with the
  documented flags and defaults,
* the console-script entry point is declared in ``pyproject.toml``.
"""

import importlib.metadata as importlib_metadata
from pathlib import Path

import pytest

import pengyos_qemu_verify as pkg
from pengyos_qemu_verify import cli
from pengyos_qemu_verify import __version__
from pengyos_qemu_verify.cli import (
    DEFAULT_RAM,
    DEFAULT_TIMEOUT,
    EXIT_NOT_IMPLEMENTED,
    EXIT_OK,
    build_parser,
    main,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- package metadata -------------------------------------------------------


def test_package_exposes_semver_string():
    assert isinstance(pkg.__version__, str)
    parts = pkg.__version__.split(".")
    # At least a numeric MAJOR.MINOR.PATCH core.
    assert len(parts) >= 3
    assert parts[0].isdigit()
    assert parts[1].isdigit()
    assert parts[2][:1].isdigit()


def test_version_matches_pyproject():
    """__version__ must not drift from the declared build metadata."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in text
    assert 'name = "pengyos-qemu-verify"' in text
    # The license table form should name MIT.
    assert "MIT" in text


def test_version_matches_installed_metadata():
    """If the package is installed (loop does `pip install -e .`), the
    importlib metadata must agree with ``__version__``. Skipped when not
    installed so the suite is green either way."""
    try:
        installed = importlib_metadata.version("pengyos-qemu-verify")
    except importlib_metadata.PackageNotFoundError:
        pytest.skip("pengyos-qemu-verify is not installed (run: pip install -e .)")
    assert installed == __version__


def test_entry_point_declared_in_pyproject():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pengyos-qemu-verify = "pengyos_qemu_verify.cli:main"' in text


# --- entry point behaviour --------------------------------------------------


def test_main_version_prints_and_returns_zero(capsys):
    rc = main(["--version"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out
    assert "pengyos-qemu-verify" in out


def test_main_no_args_prints_help_returns_zero(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "pengyos-qemu-verify" in out
    # Both documented subcommands are advertised.
    assert "boot" in out
    assert "probe" in out


def test_main_boot_missing_image_is_a_clean_failure(tmp_path, capsys):
    # ``boot`` is implemented (item 5): a missing image is a clean
    # failure (exit 1), not a crash and not "not implemented".
    rc = main(["boot", str(tmp_path / "missing.img")])
    err = capsys.readouterr().err
    assert rc == cli.EXIT_FAIL
    assert "boot failed" in err
    assert "not found" in err


def test_main_probe_prints_argv_without_launching(capsys, monkeypatch):
    # ``probe`` is implemented (item 8): it prints the resolved argv and
    # returns 0 — no more "not implemented". The full render/no-side-effect
    # coverage lives in tests/test_dry_run.py.
    import pengyos_qemu_verify.argv as argv_mod

    monkeypatch.setattr(argv_mod, "kvm_available", lambda path=argv_mod.KVM_DEVICE: False)
    rc = main(["probe", "disk.img"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "qemu-system-x86_64" in out
    assert "file=disk.img,if=virtio,format=raw" in out


def test_bad_subcommand_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["frobnicate"])
    assert exc.value.code == 2


def test_version_flag_beats_subcommand(capsys):
    # ``--version`` on the top-level parser short-circuits before dispatch.
    rc = main(["--version", "boot", "disk.img"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert __version__ in out


# --- CLI argument surface ---------------------------------------------------


def test_boot_parser_defaults():
    args = build_parser().parse_args(["boot", "disk.img"])
    assert args.command == "boot"
    assert args.image == "disk.img"
    assert args.kernel is None
    assert args.uefi is False
    assert args.tpm is False
    assert args.ram == DEFAULT_RAM
    assert args.timeout == DEFAULT_TIMEOUT
    # --expect defaults to None (no --expect given); the effective default is
    # documented as DEFAULT_EXPECT and applied by the boot subtask.
    assert args.expect is None
    assert args.json_out is None
    assert args.junit_out is None


def test_boot_parser_full():
    args = build_parser().parse_args(
        [
            "boot",
            "disk.img",
            "--kernel",
            "bzImage",
            "--uefi",
            "--tpm",
            "--ram",
            "1024",
            "--timeout",
            "60",
            "--expect",
            "login:",
            "--expect",
            "boot complete",
            "--json",
            "out.json",
            "--junit",
            "out.xml",
        ]
    )
    assert args.image == "disk.img"
    assert args.kernel == "bzImage"
    assert args.uefi is True
    assert args.tpm is True
    assert args.ram == 1024
    assert args.timeout == 60.0
    assert args.expect == ["login:", "boot complete"]
    assert args.json_out == "out.json"
    assert args.junit_out == "out.xml"


def test_probe_parser_registered():
    args = build_parser().parse_args(["probe", "disk.img", "--ram", "256", "--timeout", "30"])
    assert args.command == "probe"
    assert args.image == "disk.img"
    assert args.ram == 256
    assert args.timeout == 30.0


def test_ram_and_timeout_are_typed():
    # Type coercion is part of the documented interface.
    args = build_parser().parse_args(["boot", "d.img", "--ram", "768", "--timeout", "45"])
    assert isinstance(args.ram, int) and args.ram == 768
    assert isinstance(args.timeout, float) and args.timeout == 45.0


def test_missing_image_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["boot"])
    assert exc.value.code == 2
