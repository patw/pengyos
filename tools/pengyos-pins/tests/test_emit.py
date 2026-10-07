"""Tests for the ``emit`` subcommand (pengyos_pins.emit + CLI).

Covers every emit feature:

* **Buildroot fragment** — one ``CONFIG_<SYMBOL>=y`` line per component, in
  name-sorted order, with a Buildroot-safe symbol mapping, deterministic output,
  and a lossless emit → parse round-trip.
* **Markdown report** — component count, editions, and a per-component table.
* **JSON report** — a machine-readable document that re-parses to the same data.
* **Determinism** — the same input yields byte-identical output.
* **CLI** — the ``emit`` subcommand (in-process and subprocess) for each format,
  the default format, an invalid format, and a missing file.

All tests are hermetic: no network, no root, files only via ``tmp_path`` or the
committed fixtures.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_pins.cli import main
from pengyos_pins.emit import (
    ALL_FORMATS,
    buildroot_fragment,
    emit,
    emit_json,
    emit_report,
    parse_buildroot_fragment,
)
from pengyos_pins.schema import Pins, PinsError, component_table, parse_pins

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _pins(*names: str) -> "object":
    """Build a :class:`Pins` from a list of names (all in edition ``core``)."""
    tables = [component_table(name, edition_membership="core") for name in names]
    return parse_pins({"component": tables})


# --------------------------------------------------------------------------- #
# Buildroot fragment
# --------------------------------------------------------------------------- #


class TestBuildrootFragment:
    def test_one_line_per_component(self):
        frag = buildroot_fragment(_pins("linux", "busybox"))
        assert "CONFIG_BUSYBOX=y" in frag
        assert "CONFIG_LINUX=y" in frag

    def test_name_sorted(self):
        # Pass names out of order; the fragment must be name-sorted.
        frag = buildroot_fragment(_pins("zebra", "alpha", "mike"))
        order = [
            line.split("=")[0]
            for line in frag.splitlines()
            if line.startswith("CONFIG_")
        ]
        assert order == ["CONFIG_ALPHA", "CONFIG_MIKE", "CONFIG_ZEBRA"]

    def test_symbol_upcases_and_underscores_dashes(self):
        # Buildroot symbols allow only [A-Za-z0-9_]; dashes become underscores.
        frag = buildroot_fragment(_pins("linux-libre"))
        assert "CONFIG_LINUX_LIBRE=y" in frag
        assert "linux-libre" not in frag

    def test_header_comments_present(self):
        frag = buildroot_fragment(_pins("linux"))
        assert frag.startswith("#")
        assert "pengyos-pins" in frag

    def test_trailing_newline(self):
        frag = buildroot_fragment(_pins("linux"))
        assert frag.endswith("\n")
        assert not frag.endswith("\n\n")

    def test_deterministic(self):
        pins = _pins("linux", "busybox", "gcc")
        assert buildroot_fragment(pins) == buildroot_fragment(pins)

    def test_roundtrip_parses_back_symbols(self):
        pins = _pins("linux", "busybox")
        symbols = parse_buildroot_fragment(buildroot_fragment(pins))
        assert symbols == ["CONFIG_BUSYBOX", "CONFIG_LINUX"]

    def test_roundtrip_recovers_component_names(self):
        # emit -> parse -> reverse the symbol mapping == the original names.
        names = ["linux", "busybox", "linux-libre"]
        symbols = parse_buildroot_fragment(buildroot_fragment(_pins(*names)))
        recovered = [s[len("CONFIG_"):].lower().replace("_", "-") for s in symbols]
        assert recovered == sorted(names)

    def test_empty_pins_has_no_config_lines(self):
        # An empty Pins (no components) still emits cleanly — just the header.
        frag = buildroot_fragment(Pins(components=()))
        assert not any(line.startswith("CONFIG_") for line in frag.splitlines())
        assert frag.startswith("#")


class TestParseBuildrootFragment:
    def test_ignores_comments_and_blank_lines(self):
        text = (
            "# a comment\n"
            "\n"
            "CONFIG_BUSYBOX=y\n"
            "# another\n"
            "CONFIG_LINUX=y\n"
        )
        assert parse_buildroot_fragment(text) == ["CONFIG_BUSYBOX", "CONFIG_LINUX"]

    def test_accepts_arbitrary_values(self):
        text = "CONFIG_BUSYBOX=m\nCONFIG_LINUX=n\nCONFIG_FOO=mod\n"
        assert parse_buildroot_fragment(text) == [
            "CONFIG_BUSYBOX",
            "CONFIG_LINUX",
            "CONFIG_FOO",
        ]

    def test_ignores_non_config_lines(self):
        text = "SOME_OTHER_VAR=1\nCONFIG_BUSYBOX=y\n"
        assert parse_buildroot_fragment(text) == ["CONFIG_BUSYBOX"]


# --------------------------------------------------------------------------- #
# Markdown report
# --------------------------------------------------------------------------- #


class TestReport:
    def test_contains_component_count(self):
        report = emit_report(_pins("linux", "busybox"))
        assert "Components: 2" in report

    def test_contains_editions(self):
        report = emit_report(_pins("linux", "busybox"))
        assert "Editions: core" in report

    def test_unassigned_editions_shows_none(self):
        report = emit_report(parse_pins({"component": [component_table("linux")]}))
        assert "(none assigned)" in report

    def test_table_has_every_component(self):
        report = emit_report(_pins("linux", "busybox"))
        assert "busybox" in report
        assert "linux" in report
        # The table header + separator are present.
        assert "| name |" in report
        assert "| --- |" in report

    def test_name_sorted(self):
        report = emit_report(_pins("zebra", "alpha"))
        assert report.index("alpha") < report.index("zebra")

    def test_edition_listed(self):
        report = emit_report(_pins("linux"))
        assert "core" in report

    def test_deterministic(self):
        pins = _pins("linux", "busybox")
        assert emit_report(pins) == emit_report(pins)


# --------------------------------------------------------------------------- #
# JSON report
# --------------------------------------------------------------------------- #


class TestJson:
    def test_valid_json(self):
        text = emit_json(_pins("linux", "busybox"))
        payload = json.loads(text)
        assert payload["component_count"] == 2

    def test_component_fields_present(self):
        payload = json.loads(emit_json(_pins("linux")))
        (comp,) = payload["components"]
        assert set(comp) == {
            "name",
            "upstream",
            "version",
            "url",
            "sha256",
            "license",
            "edition",
        }

    def test_roundtrip_recovers_data(self):
        pins = _pins("linux", "busybox")
        payload = json.loads(emit_json(pins))
        names = sorted(c["name"] for c in payload["components"])
        assert names == ["busybox", "linux"]
        assert payload["editions"] == ["core"]

    def test_deterministic(self):
        pins = _pins("linux", "busybox")
        assert emit_json(pins) == emit_json(pins)

    def test_trailing_newline(self):
        assert emit_json(_pins("linux")).endswith("\n")


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #


class TestEmitDispatch:
    def test_all_formats_produce_strings(self):
        pins = _pins("linux")
        for fmt in ALL_FORMATS:
            assert isinstance(emit(pins, fmt), str)

    def test_unknown_format_raises(self):
        with pytest.raises(ValueError, match="unknown format"):
            emit(_pins("linux"), "xml")


# --------------------------------------------------------------------------- #
# CLI: emit subcommand
# --------------------------------------------------------------------------- #


def _run_cli(*argv: str, **env: str) -> subprocess.CompletedProcess[str]:
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


class TestCliEmit:
    def test_emit_default_format_is_buildroot_fragment(self, capsys):
        rc = main(["emit", str(FIXTURES / "valid.toml")])
        assert rc == 0
        out = capsys.readouterr().out
        assert out.startswith("#")
        assert "CONFIG_BUSYBOX=y" in out
        assert "CONFIG_LINUX=y" in out

    def test_emit_buildroot_fragment(self, capsys):
        rc = main(
            ["emit", str(FIXTURES / "valid.toml"), "--format", "buildroot-fragment"]
        )
        assert rc == 0
        assert "CONFIG_BUSYBOX=y" in capsys.readouterr().out

    def test_emit_report(self, capsys):
        rc = main(["emit", str(FIXTURES / "valid.toml"), "--format", "report"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "PengyOS pins report" in out
        assert "Components: 2" in out

    def test_emit_json(self, capsys):
        rc = main(["emit", str(FIXTURES / "valid.toml"), "--format", "json"])
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)  # must be valid JSON
        assert payload["component_count"] == 2

    def test_emit_requires_valid_manifest(self, capsys):
        # The SPDX fixture is schema-valid but fails validation; emit must refuse.
        rc = main(["emit", str(FIXTURES / "bad_spdx.toml")])
        assert rc == 1
        assert "invalid:" in capsys.readouterr().err

    def test_emit_missing_file_returns_1(self, capsys):
        rc = main(["emit", str(FIXTURES / "does-not-exist.toml")])
        assert rc == 1
        assert "invalid:" in capsys.readouterr().err

    def test_emit_invalid_format_exits_2(self):
        # argparse rejects an unknown --format value with exit code 2.
        with pytest.raises(SystemExit) as exc:
            main(["emit", str(FIXTURES / "valid.toml"), "--format", "xml"])
        assert exc.value.code == 2

    def test_emit_default_path_absent(self):
        # No path given -> defaults to pins.toml, which is not in the repo yet.
        proc = _run_cli("emit")
        assert proc.returncode == 1
        assert "invalid:" in proc.stderr

    def test_emit_buildroot_subprocess(self):
        proc = _run_cli("emit", str(FIXTURES / "valid.toml"))
        assert proc.returncode == 0
        assert "CONFIG_BUSYBOX=y" in proc.stdout

    def test_emit_json_subprocess_roundtrip(self):
        proc = _run_cli(
            "emit", str(FIXTURES / "valid.toml"), "--format", "json"
        )
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert payload["component_count"] == 2


class TestDeterministicOrdering:
    """Determinism is order-independence, not just idempotency.

    The emitters sort by component name, so the *input* order of a manifest
    must not affect the emitted bytes.  These tests build a :class:`Pins` from
    several shuffled input orders and assert byte-identical output for every
    format, plus that each emitter is itself in name-sorted order.
    """

    # A spread of names (varying length, digits, underscores, dashes) that
    # would expose any ordering bug if the emitter trusted input order.
    NAMES = [
        "zlib",
        "busybox",
        "linux-libre",
        "gcc",
        "binutils",
        "musl",
        "glibc",
        "ncurses",
        "grep",
        "sed",
        "coreutils",
        "findutils",
    ]

    @staticmethod
    def _pins_in(order: list[str]) -> "object":
        """Build a :class:`Pins` whose components are in the given order.

        ``version``/``sha256`` are a pure function of the *name*, so shuffling
        the input order changes only the order — never the component data.
        """
        tables = []
        for name in order:
            digest = sum(ord(c) for c in name)
            tables.append(
                component_table(
                    name,
                    version=f"1.0.{digest % 100}",
                    sha256=f"{digest % 16:01x}{'a' * 63}",
                    edition_membership="core",
                )
            )
        return parse_pins({"component": tables})

    def _canonical(self, fmt: str) -> str:
        """Emit ``fmt`` once from a canonical (name-sorted) Pins."""
        return emit(self._pins_in(sorted(self.NAMES)), fmt)

    @pytest.mark.parametrize("fmt", ALL_FORMATS)
    def test_input_order_does_not_matter(self, fmt: str):
        # A handful of deliberately scrambled input orders.
        orders = [
            self.NAMES,
            list(reversed(self.NAMES)),
            ["gcc", "busybox", "zlib", "musl", "glibc", "sed", "grep",
             "coreutils", "findutils", "ncurses", "binutils", "linux-libre"],
            ["findutils", "gcc", "musl", "busybox", "zlib", "coreutils",
             "glibc", "sed", "grep", "ncurses", "linux-libre", "binutils"],
        ]
        expected = self._canonical(fmt)
        for order in orders:
            assert emit(self._pins_in(order), fmt) == expected

    @pytest.mark.parametrize("fmt", ALL_FORMATS)
    def test_output_is_name_sorted(self, fmt: str):
        text = self._canonical(fmt)
        symbols = parse_buildroot_fragment(text) if fmt == "buildroot-fragment" else []
        if fmt == "buildroot-fragment":
            expected = [f"CONFIG_{name.upper().replace('-', '_')}" for name in sorted(self.NAMES)]
            assert symbols == expected
        elif fmt == "json":
            import json

            payload = json.loads(text)
            names = [c["name"] for c in payload["components"]]
            assert names == sorted(self.NAMES)
        else:  # report
            positions = [text.index(name) for name in sorted(self.NAMES)]
            assert positions == sorted(positions)

    def test_edition_list_is_sorted_and_deduped(self):
        # Repeated editions across a larger input still sort + de-dup cleanly.
        tables = [
            component_table(name, edition_membership=["core", "base", "crypto"][i % 3])
            for i, name in enumerate(self.NAMES)
        ]
        pins = parse_pins({"component": tables})
        assert pins.edition_names == ["base", "core", "crypto"]


def test_emit_module_importable():
    # Smoke check that the package exposes the emit surface.
    import pengyos_pins.emit  # noqa: F401

    assert pengyos_pins.emit.emit_json(_pins("linux"))
