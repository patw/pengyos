"""Package-structure and API-contract tests.

The scaffold's job is a sound, importable package whose modules expose the
signatures the later units of work will implement. These tests pin that contract
so a rename or signature drift is caught immediately.
"""

from __future__ import annotations

import inspect

import pengyos_desktop_drivers
from pengyos_desktop_drivers import cli, emit, errors, lint, __main__


def test_all_modules_importable() -> None:
    for name in ("__main__", "cli", "emit", "lint", "errors"):
        mod = __import__(f"pengyos_desktop_drivers.{name}", fromlist=[name])
        assert mod is not None


def test_emit_public_contract() -> None:
    assert hasattr(emit, "emit_desktop_profile")
    sig = inspect.signature(emit.emit_desktop_profile)
    params = list(sig.parameters)
    # (out_dir, as_json=False)
    assert params[:1] == ["out_dir"]
    assert sig.parameters["as_json"].default is False
    # Canonical artifact filenames live here so CLI/tests agree.
    assert emit.FRAGMENT_FILENAME == "linux.fragment"
    assert emit.FIRMWARE_FILENAME == "firmware.list"


def test_lint_public_contract() -> None:
    assert hasattr(lint, "lint_fragment")
    sig = inspect.signature(lint.lint_fragment)
    params = list(sig.parameters)
    assert params[:3] == ["fragment_path", "firmware_path", "as_json"]
    # The four rule identifiers are the stable keys the CLI and tests key on:
    # distinct, non-empty strings.
    rule_ids = [
        lint.RULE_SYMBOL_EXISTS,
        lint.RULE_NO_VENDOR_ACCEL,
        lint.RULE_FIRMWARE_SUBSET,
        lint.RULE_CLASS_COVERAGE,
    ]
    assert len(rule_ids) == len(set(rule_ids)) == 4
    assert all(isinstance(r, str) and r for r in rule_ids)


def test_errors_contract() -> None:
    assert issubclass(errors.PengyOSError, Exception)
    assert issubclass(errors.NotYetImplemented, errors.PengyOSError)
    assert issubclass(errors.LintViolation, errors.PengyOSError)


def test_main_exposes_entrypoint() -> None:
    assert callable(__main__.main)
    assert callable(cli.main)
