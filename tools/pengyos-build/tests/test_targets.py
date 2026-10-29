"""Tests for targets.toml parsing and validation (pengyos_build.targets)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import pengyos_build
from pengyos_build.cli import build_parser, main
from pengyos_build.targets import (
    Target,
    TargetsFileError,
    get_target,
    load_target,
    load_targets,
    parse_targets,
)

ROOT = Path(__file__).resolve().parents[1]


def _valid_doc() -> dict:
    return {
        "targets": [
            {
                "name": "pengyos-server",
                "br_version": "2026.02.3",
                "external_tree": "buildroot-external",
                "defconfig": "pengyos_server_defconfig",
            },
            {
                "name": "pengyos-core",
                "br_version": "2026.08",
                "external_tree": "/abs/path/ext",
                "defconfig": "pengyos_core_defconfig",
                "board": "pengyos/qemu-x86_64",
            },
        ]
    }


class TestParseValid:
    def test_two_targets(self):
        targets = parse_targets(_valid_doc(), base_dir="/base")
        assert [t.name for t in targets] == ["pengyos-server", "pengyos-core"]

    def test_relative_external_tree_resolved_against_base_dir(self):
        targets = parse_targets(_valid_doc(), base_dir="/base")
        assert targets[0].external_tree == Path("/base/buildroot-external")

    def test_absolute_external_tree_passes_through(self):
        targets = parse_targets(_valid_doc(), base_dir="/base")
        assert targets[1].external_tree == Path("/abs/path/ext")

    def test_board_optional_defaults_to_empty(self):
        targets = parse_targets(_valid_doc(), base_dir=".")
        assert targets[0].board == ""
        assert targets[1].board == "pengyos/qemu-x86_64"

    def test_target_is_frozen_dataclass(self):
        t = parse_targets(_valid_doc())[0]
        assert isinstance(t, Target)
        with pytest.raises(Exception):
            t.name = "other"  # type: ignore[misc]

    def test_br_external_env(self):
        t = parse_targets(_valid_doc(), base_dir="/base")[0]
        assert t.br_external_env == "/base/buildroot-external"


class TestStructuralErrors:
    def test_top_level_not_a_table(self):
        with pytest.raises(TargetsFileError, match="top level must be a table"):
            parse_targets(["targets"])

    def test_missing_targets_key(self):
        with pytest.raises(TargetsFileError, match="missing top-level 'targets'"):
            parse_targets({"other": []})

    def test_targets_not_an_array(self):
        with pytest.raises(TargetsFileError, match="'targets' must be an array"):
            parse_targets({"targets": {}})

    def test_targets_empty(self):
        with pytest.raises(TargetsFileError, match="must not be empty"):
            parse_targets({"targets": []})

    def test_entry_not_a_table(self):
        with pytest.raises(TargetsFileError, match=r"targets\[0\]: expected a table"):
            parse_targets({"targets": ["nope"]})

    def test_error_carries_source_name(self):
        with pytest.raises(TargetsFileError, match="my-targets.toml"):
            parse_targets({"nope": 1}, source="my-targets.toml")


class TestFieldErrors:
    @pytest.mark.parametrize("field", ["name", "br_version", "external_tree", "defconfig"])
    def test_missing_required_field(self, field):
        doc = _valid_doc()
        del doc["targets"][1][field]
        with pytest.raises(
            TargetsFileError,
            match=f"targets\\[1\\]: missing required field '{field}'",
        ):
            parse_targets(doc)

    def test_non_string_field(self):
        doc = _valid_doc()
        doc["targets"][0]["br_version"] = 2026
        with pytest.raises(
            TargetsFileError, match=r"expected a string, got int"
        ):
            parse_targets(doc)

    def test_empty_string_field(self):
        doc = _valid_doc()
        doc["targets"][0]["name"] = "   "
        with pytest.raises(
            TargetsFileError, match=r"targets\[0\].name: must not be empty"
        ):
            parse_targets(doc)

    def test_unknown_field(self):
        doc = _valid_doc()
        doc["targets"][0]["extra"] = 1
        with pytest.raises(
            TargetsFileError, match=r"targets\[0\]: unknown field\(s\): extra"
        ):
            parse_targets(doc)

    def test_unknown_fields_sorted_and_joined(self):
        doc = _valid_doc()
        doc["targets"][0].update({"zeta": 1, "alpha": 2})
        with pytest.raises(
            TargetsFileError, match=r"unknown field\(s\): alpha, zeta"
        ):
            parse_targets(doc)


class TestValueErrors:
    @pytest.mark.parametrize(
        "name",
        ["Bad", "has space", "dot.name", "with/slash", "_lead", "-lead"],
    )
    def test_invalid_name(self, name):
        doc = _valid_doc()
        doc["targets"][0]["name"] = name
        with pytest.raises(TargetsFileError, match="must match \\[a-z0-9\\]"):
            parse_targets(doc)

    def test_digit_start_name_is_valid(self):
        doc = _valid_doc()
        doc["targets"][0]["name"] = "1ok-ok"
        assert parse_targets(doc)[0].name == "1ok-ok"

    @pytest.mark.parametrize(
        "version",
        ["26.02.3", "2026.2", "2026.02.x", "v2026.02", "2026.02.3.1"],
    )
    def test_invalid_br_version(self, version):
        doc = _valid_doc()
        doc["targets"][0]["br_version"] = version
        with pytest.raises(
            TargetsFileError, match=r"is not a valid Buildroot version"
        ):
            parse_targets(doc)

    @pytest.mark.parametrize("version", ["2026.02", "2026.02.3", "2026.10.12"])
    def test_valid_br_versions(self, version):
        doc = _valid_doc()
        doc["targets"][0]["br_version"] = version
        assert parse_targets(doc)[0].br_version == version

    def test_external_tree_no_dotdot(self):
        doc = _valid_doc()
        doc["targets"][0]["external_tree"] = "../outside"
        with pytest.raises(
            TargetsFileError, match=r"must not contain '\.\.' path components"
        ):
            parse_targets(doc)

    def test_defconfig_must_be_bare_filename(self):
        doc = _valid_doc()
        doc["targets"][0]["defconfig"] = "configs/pengyos_server_defconfig"
        with pytest.raises(
            TargetsFileError, match=r"defconfig: 'configs/.*' must be a bare file name"
        ):
            parse_targets(doc)

    def test_duplicate_name(self):
        doc = _valid_doc()
        doc["targets"][1]["name"] = "pengyos-server"
        with pytest.raises(
            TargetsFileError, match=r"target 'pengyos-server': duplicate target name"
        ):
            parse_targets(doc)

    def test_board_non_string(self):
        doc = _valid_doc()
        doc["targets"][0]["board"] = 3
        with pytest.raises(
            TargetsFileError, match=r"board: expected a string, got int"
        ):
            parse_targets(doc)


class TestLookup:
    def test_get_target_found(self):
        targets = parse_targets(_valid_doc())
        assert get_target(targets, "pengyos-core").br_version == "2026.08"

    def test_get_target_unknown(self):
        targets = parse_targets(_valid_doc())
        with pytest.raises(
            TargetsFileError,
            match=r"unknown target 'nope' \(known targets: pengyos-server, pengyos-core\)",
        ):
            get_target(targets, "nope")

    def test_get_target_empty_list(self):
        with pytest.raises(
            TargetsFileError, match=r"unknown target 'x' \(known targets: <none>\)"
        ):
            get_target([], "x")


class TestLoadFromDisk:
    def test_load_targets_roundtrip(self, tmp_path: Path):
        toml_file = tmp_path / "targets.toml"
        toml_file.write_text(
            textwrap.dedent(
                """\
                [[targets]]
                name = "t1"
                br_version = "2026.02.3"
                external_tree = "ext"
                defconfig = "t1_defconfig"
                """
            )
        )
        targets = load_targets(toml_file)
        assert targets[0].external_tree == tmp_path / "ext"
        assert targets[0].board == ""

    def test_load_targets_missing_file(self, tmp_path: Path):
        with pytest.raises(TargetsFileError, match="targets file not found"):
            load_targets(tmp_path / "nope.toml")

    def test_load_targets_malformed_toml(self, tmp_path: Path):
        import tomllib

        bad = tmp_path / "bad.toml"
        bad.write_text("targets = [[ not valid")
        with pytest.raises(tomllib.TOMLDecodeError):
            load_targets(bad)

    def test_load_target(self, tmp_path: Path):
        toml_file = tmp_path / "targets.toml"
        toml_file.write_text(
            textwrap.dedent(
                """\
                [[targets]]
                name = "only"
                br_version = "2026.02.3"
                external_tree = "ext"
                defconfig = "only_defconfig"
                """
            )
        )
        assert load_target(toml_file, "only").defconfig == "only_defconfig"
        with pytest.raises(TargetsFileError, match="unknown target 'ghost'"):
            load_target(toml_file, "ghost")


class TestRepoTargetsToml:
    """The committed targets.toml must itself parse and match the pin."""

    def test_committed_targets_file_valid(self):
        repo = ROOT.parents[1]
        targets = load_targets(repo / "targets.toml")
        assert len(targets) >= 1
        server = get_target(targets, "pengyos-server")
        # Buildroot pin: 2026.08.1 (the 2026.08 series has the 7.1 headers; 2026.02.3
        # lacks them). Every target pins the same tree.
        assert server.br_version == "2026.08.1"
        assert {t.br_version for t in targets} == {"2026.08.1"}
        assert server.defconfig == "pengyos_server_defconfig"
        assert server.external_tree == repo / "buildroot-external"


class TestCliList:
    def _make(self, tmp_path: Path, body: str) -> Path:
        f = tmp_path / "targets.toml"
        f.write_text(body)
        return f

    def test_list_prints_all_targets(self, tmp_path: Path, capsys):
        f = self._make(
            tmp_path,
            textwrap.dedent(
                """\
                [[targets]]
                name = "a"
                br_version = "2026.02.3"
                external_tree = "ext"
                defconfig = "a_defconfig"

                [[targets]]
                name = "b"
                br_version = "2026.08"
                external_tree = "ext"
                defconfig = "b_defconfig"
                board = "pengyos/qemu"
                """
            ),
        )
        rc = main(["list", "--targets", str(f)])
        out = capsys.readouterr().out.splitlines()
        assert rc == 0
        assert out[0] == (
            f"a\tbr_version=2026.02.3\tdefconfig=a_defconfig\t"
            f"external_tree={f.parent / 'ext'}"
        )
        assert out[1] == (
            f"b\tbr_version=2026.08\tdefconfig=b_defconfig\tboard=pengyos/qemu\t"
            f"external_tree={f.parent / 'ext'}"
        )

    def test_list_missing_file_returns_1(self, tmp_path: Path, capsys):
        rc = main(["list", "--targets", str(tmp_path / "nope.toml")])
        err = capsys.readouterr().err
        assert rc == 1
        assert "error:" in err
        assert "targets file not found" in err

    def test_list_validation_error_returns_1(self, tmp_path: Path, capsys):
        f = self._make(
            tmp_path,
            textwrap.dedent(
                """\
                [[targets]]
                name = "a"
                br_version = "2026.02.3"
                external_tree = "ext"
                """
            ),  # missing defconfig
        )
        rc = main(["list", "--targets", str(f)])
        err = capsys.readouterr().err
        assert rc == 1
        assert "missing required field 'defconfig'" in err

    def test_help_mentions_list(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "list" in out

    def test_parser_list_defaults(self):
        args = build_parser().parse_args(["list"])
        assert args.command == "list"
        assert args.targets == "targets.toml"


class TestProcessEntryPoint:
    def _run(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "pengyos_build", *argv],
            capture_output=True,
            text=True,
            check=False,
            env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
        )

    def test_list_against_repo_targets_file(self):
        proc = self._run("list", "--targets", str(ROOT.parents[1] / "targets.toml"))
        assert proc.returncode == 0
        assert "pengyos-server" in proc.stdout
        assert "br_version=2026.08" in proc.stdout
