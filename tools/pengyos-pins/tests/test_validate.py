"""Tests for semantic validation (pengyos_pins.validate) and the ``validate``
CLI subcommand.

Covers every validation rule with both a passing and a failing case:

* **SPDX license syntax** — well-formed expressions pass; malformed ones fail.
* **sha256 hash format** — 64-char lowercase hex passes; anything else fails.

The committed fixtures under ``tests/fixtures/`` are the "each validation rule
has a failing fixture" requirement from spec.md.  All tests are hermetic: no
network, no root, files only via ``tmp_path`` or committed fixtures.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pengyos_pins.cli import main
from pengyos_pins.schema import load_pins, parse_pins, component_table
from pengyos_pins.validate import (
    validate_component,
    validate_pins,
    validate_spdx,
    validate_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


# --------------------------------------------------------------------------- #
# SPDX expression grammar
# --------------------------------------------------------------------------- #


class TestSpdxValid:
    @pytest.mark.parametrize(
        "expr",
        [
            "GPL-2.0-only",
            "MIT",
            "BSD-3-Clause",
            "Classpath-exception-2.1",
            "GPL-2.0-or-later+",  # or-later marker
            "MIT+",
            "MIT AND GPL-2.0-only",
            "MIT OR GPL-2.0-only",
            "MIT AND (GPL-2.0-only OR BSD-3-Clause)",
            "GPL-2.0-only WITH Classpath-exception-2.1",
            "GPL-2.0-only WITH (custom-exception)",
            "(MIT) AND (GPL-2.0-only)",
            "MIT AND GPL-2.0-only AND BSD-3-Clause",
        ],
    )
    def test_valid_expression_passes(self, expr):
        assert validate_spdx(expr) == []

    def test_valid_expression_via_component(self):
        (comp,) = parse_pins(
            {"component": [component_table("linux", license="GPL-2.0-only")]}
        ).components
        assert validate_component(comp) == []


class TestSpdxInvalid:
    @pytest.mark.parametrize(
        "expr",
        [
            "",  # empty
            "   ",  # whitespace only
            "MIT AND",  # missing right operand
            "AND MIT",  # leading operator
            "MIT OR",  # missing right operand
            "MIT AND OR GPL-2.0",  # dangling operator
            "(MIT",  # unbalanced parenthesis
            "MIT)",  # unbalanced parenthesis
            "MIT GPL-2.0-only",  # missing operator (implicit AND not allowed)
            "MIT ANDOR GPL-2.0",  # not a real operator
            "MIT + GPL-2.0",  # stray + in the middle
            "GPL-2.0-only++",  # double or-later
        ],
    )
    def test_invalid_expression_fails(self, expr):
        assert validate_spdx(expr), f"expected '{expr}' to be invalid"

    def test_invalid_expression_message_mentions_spdx(self):
        (message,) = validate_spdx("MIT AND")
        assert "SPDX" in message


# --------------------------------------------------------------------------- #
# sha256 hash format
# --------------------------------------------------------------------------- #


class TestSha256Valid:
    @pytest.mark.parametrize(
        "digest",
        [
            "0" * 64,
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "deadbeef" * 8,
        ],
    )
    def test_valid_digest_passes(self, digest):
        assert validate_sha256(digest) == []


class TestSha256Invalid:
    @pytest.mark.parametrize(
        "digest",
        [
            "ABCDEF",  # too short + uppercase
            "g" * 64,  # non-hex
            "0" * 63,  # one short
            "0" * 65,  # one long
            "0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF",  # uppercase
            "",  # empty
        ],
    )
    def test_invalid_digest_fails(self, digest):
        assert validate_sha256(digest), f"expected '{digest}' to be invalid"

    def test_invalid_digest_message_mentions_hex(self):
        (message,) = validate_sha256("ABCDEF")
        assert "hex" in message


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


class TestValidatePins:
    def test_valid_document_has_no_errors(self):
        doc = {
            "component": [
                component_table("linux", license="GPL-2.0-only"),
                component_table("busybox", license="MIT AND BSD-3-Clause"),
            ]
        }
        assert validate_pins(parse_pins(doc)) == []

    def test_reports_every_problem_at_once(self):
        doc = {
            "component": [
                component_table("linux", license="MIT", sha256="0" * 64),
                # "MIT AND" is a malformed SPDX expression (missing operand).
                component_table("busybox", license="MIT AND", sha256="nope"),
            ]
        }
        errors = validate_pins(parse_pins(doc))
        # linux is valid; busybox has both a bad SPDX expr and a bad hash.
        assert len(errors) == 2
        assert errors[0].startswith("component[1]:")
        assert errors[1].startswith("component[1]:")

    def test_error_is_prefixed_by_index(self):
        doc = {"component": [component_table("linux", license="MIT AND")]}
        (message,) = validate_pins(parse_pins(doc))
        assert message.startswith("component[0]:")


# --------------------------------------------------------------------------- #
# Committed fixtures — each validation rule has a failing fixture (spec.md)
# --------------------------------------------------------------------------- #


class TestFixtures:
    def test_valid_fixture_passes(self):
        pins = load_pins(FIXTURES / "valid.toml")
        assert validate_pins(pins) == []

    def test_bad_spdx_fixture_fails_spdx_rule(self):
        # The fixture is schema-valid (parses) but fails the SPDX rule.
        pins = load_pins(FIXTURES / "bad_spdx.toml")
        assert len(pins) == 1
        errors = validate_pins(pins)
        assert errors
        assert "SPDX" in errors[0]

    def test_bad_sha256_fixture_fails_hash_rule(self):
        pins = load_pins(FIXTURES / "bad_sha256.toml")
        errors = validate_pins(pins)
        assert errors
        assert "hex" in errors[0]


# --------------------------------------------------------------------------- #
# CLI: validate subcommand
# --------------------------------------------------------------------------- #


class TestCliValidate:
    def test_validate_ok_returns_0(self, capsys):
        rc = main(["validate", str(FIXTURES / "valid.toml")])
        assert rc == 0
        assert "ok:" in capsys.readouterr().out

    def test_validate_bad_spdx_returns_1(self, capsys):
        rc = main(["validate", str(FIXTURES / "bad_spdx.toml")])
        assert rc == 1
        assert "invalid:" in capsys.readouterr().err

    def test_validate_bad_sha256_returns_1(self, capsys):
        rc = main(["validate", str(FIXTURES / "bad_sha256.toml")])
        assert rc == 1
        assert "invalid:" in capsys.readouterr().err

    def test_validate_missing_file_returns_1(self, capsys):
        rc = main(["validate", str(FIXTURES / "does-not-exist.toml")])
        assert rc == 1
        assert "invalid:" in capsys.readouterr().err

    def test_validate_malformed_toml_returns_1(self, tmp_path: Path, capsys):
        bad = tmp_path / "bad.toml"
        bad.write_text("component = [[ not valid")
        rc = main(["validate", str(bad)])
        assert rc == 1
        assert "invalid:" in capsys.readouterr().err

    def test_validate_missing_required_field_returns_1(self, tmp_path: Path, capsys):
        # A schema (required-field) failure surfaces through the validate command.
        missing = tmp_path / "missing.toml"
        missing.write_text(
            textwrap.dedent(
                """\
                [[component]]
                name = "linux"
                version = "7.2.5"
                """
            )
        )
        rc = main(["validate", str(missing)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "invalid:" in err
        assert "missing required field" in err


def _run_cli(*argv: str, **env: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI as a real process: ``python -m pengyos_pins ...``."""
    base_env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
    base_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "pengyos_pins", *argv],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=base_env,
    )


class TestCliValidateProcess:
    def test_validate_ok_subprocess(self):
        proc = _run_cli("validate", str(FIXTURES / "valid.toml"))
        assert proc.returncode == 0
        assert "ok:" in proc.stdout

    def test_validate_bad_subprocess(self):
        proc = _run_cli("validate", str(FIXTURES / "bad_spdx.toml"))
        assert proc.returncode == 1
        assert "invalid:" in proc.stderr

    def test_validate_default_path_absent(self):
        # No path given -> defaults to pins.toml, which is not in the repo yet.
        proc = _run_cli("validate")
        assert proc.returncode == 1
        assert "invalid:" in proc.stderr
