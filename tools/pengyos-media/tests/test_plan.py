"""Tests for ``build_plan`` / ``read_release_manifest`` and the ``plan`` CLI
.

Covers: building a validated plan from a release manifest, the committed golden
plan, determinism, the ``plan --json``/``--out`` CLI behaviour, and the build
failures (missing version, unreadable manifest, bad JSON).
"""

import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_media.model import MediumKind, MediumPlan, make_plan
from pengyos_media.plan import PlanBuildError, build_plan, read_release_manifest
from pengyos_media.cli import ExitCode, main

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIXTURES / "media-desktop.json"
RELEASE = FIXTURES / "release-desktop.json"


def _write_release(tmp_path: Path, obj: dict) -> Path:
    path = tmp_path / "release.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_read_release_manifest_parses(tmp_path: Path):
    path = _write_release(tmp_path, {"version": "2026.09.1", "channel": "stable"})
    assert read_release_manifest(path) == {"version": "2026.09.1", "channel": "stable"}


def test_read_release_manifest_missing_file():
    with pytest.raises(PlanBuildError) as exc:
        read_release_manifest("/no/such/release.json")
    assert exc.value.code == "manifest-unreadable"


def test_read_release_manifest_bad_json(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PlanBuildError) as exc:
        read_release_manifest(path)
    assert exc.value.code == "manifest-bad-json"


def test_read_release_manifest_not_object(tmp_path: Path):
    path = tmp_path / "arr.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(PlanBuildError) as exc:
        read_release_manifest(path)
    assert exc.value.code == "manifest-not-object"


def test_build_plan_from_manifest():
    plan = build_plan("desktop", {"version": "2026.09.1", "channel": "testing"})
    assert plan.kind == MediumKind.ISO
    assert plan.edition.value == "desktop"
    assert plan.release.version == "2026.09.1"
    assert plan.release.channel == "testing"


def test_build_plan_reads_version_and_channel():
    plan = build_plan("core", {"version": "9.9", "channel": "stable"})
    assert plan.release.version == "9.9"
    assert plan.release.channel == "stable"


def test_build_plan_channel_defaults_to_testing():
    plan = build_plan("core", {"version": "9.9"})
    assert plan.release.channel == "testing"


def test_build_plan_requires_version():
    with pytest.raises(PlanBuildError) as exc:
        build_plan("desktop", {})
    assert exc.value.code == "release-missing-version"


def test_build_plan_trims_whitespace_version():
    plan = build_plan("desktop", {"version": "  2026.09.1  "})
    assert plan.release.version == "2026.09.1"


def test_build_plan_returns_valid_plan():
    from pengyos_media.validate import plan_is_valid

    assert plan_is_valid(build_plan("desktop", {"version": "2026.09.1"}))


def test_golden_plan_matches_committed_fixture():
    assert make_plan("desktop", "2026.09.1", channel="testing").to_json() == GOLDEN.read_text()


def test_cli_plan_json_matches_golden(tmp_path: Path):
    # Point `plan` at the committed release fixture: it must emit the committed
    # golden plan byte-for-byte (the release fixture is the input, the golden
    # plan is the deterministic output — they are grounded together).
    out = tmp_path / "media.json"
    code = main(
        ["plan", "--edition", "desktop", "--release", str(RELEASE),
         "--out", str(out), "--json"]
    )
    assert code == ExitCode.OK
    assert out.read_text() == GOLDEN.read_text()


def test_cli_plan_writes_file(tmp_path: Path):
    release = _write_release(tmp_path, {"version": "2026.09.1"})
    out = tmp_path / "media.json"
    code = main(
        ["plan", "--edition", "core", "--release", str(release), "--out", str(out)]
    )
    assert code == ExitCode.OK
    data = json.loads(out.read_text())
    assert data["edition"] == "core"
    assert data["release"]["version"] == "2026.09.1"


def test_cli_plan_text_output(tmp_path: Path):
    release = _write_release(tmp_path, {"version": "2026.09.1"})
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(
            ["plan", "--edition", "desktop", "--release", str(release),
             "--out", str(tmp_path / "m.json")]
        )
    assert code == ExitCode.OK
    text = out.getvalue()
    for needle in ("iso", "desktop", "2026.09.1", "pengyos-install", "efi"):
        assert needle in text


def test_cli_plan_json_to_stdout(tmp_path: Path):
    release = _write_release(tmp_path, {"version": "2026.09.1"})
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(
            ["plan", "--edition", "desktop", "--release", str(release),
             "--out", str(tmp_path / "m.json"), "--json"]
        )
    assert code == ExitCode.OK
    data = json.loads(out.getvalue())
    assert data["edition"] == "desktop"
    assert data["release"]["version"] == "2026.09.1"


def test_cli_plan_text_emits_build_argv(tmp_path: Path):
    # Item 3: the plan's text output emits the ordered build argv, not just the
    # layout. Every step's command is present.
    release = _write_release(tmp_path, {"version": "2026.09.1"})
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(
            ["plan", "--edition", "desktop", "--release", str(release),
             "--out", str(tmp_path / "m.json")]
        )
    assert code == ExitCode.OK
    text = out.getvalue()
    assert "build steps (3):" in text
    for command in ("mksquashfs", "assemble-esp-boot", "xorriso"):
        assert command in text
    # The xorriso step burns a hybrid ISO with the edition in its name.
    assert "desktop-install.iso" in text
    assert "-isohybrid-gpt-basdat" in text


def test_cli_plan_json_still_layout_only(tmp_path: Path):
    # The JSON is the serialisable MediumPlan (layout); the build steps are a
    # pure function of it, derivable by build. So the JSON is unchanged from the
    # golden fixture (item 3 does not mutate the plan data).
    release = _write_release(tmp_path, {"version": "2026.09.1"})
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(
            ["plan", "--edition", "desktop", "--release", str(release),
             "--out", str(tmp_path / "m.json"), "--json"]
        )
    assert code == ExitCode.OK
    assert out.getvalue() == GOLDEN.read_text()


def test_cli_plan_build_error_missing_version(tmp_path: Path):
    release = _write_release(tmp_path, {})
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(
            ["plan", "--edition", "desktop", "--release", str(release),
             "--out", str(tmp_path / "m.json")]
        )
    assert code == ExitCode.ERROR
    assert "version" in err.getvalue().lower()


def test_cli_plan_build_error_unreadable(tmp_path: Path):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(
            ["plan", "--edition", "desktop", "--release", "/no/such.json",
             "--out", str(tmp_path / "m.json")]
        )
    assert code == ExitCode.ERROR
    assert "read" in err.getvalue().lower()


def test_cli_plan_does_not_write_on_error(tmp_path: Path):
    release = _write_release(tmp_path, {})  # missing version
    out = tmp_path / "media.json"
    code = main(
        ["plan", "--edition", "desktop", "--release", str(release), "--out", str(out)]
    )
    assert code == ExitCode.ERROR
    assert not out.exists()


def test_cli_python_m_runs_plan(tmp_path: Path):
    release = _write_release(tmp_path, {"version": "2026.09.1"})
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_media", "plan", "--edition", "desktop",
         "--release", str(release), "--json"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["edition"] == "desktop"
