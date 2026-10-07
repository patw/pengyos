"""Tests for the pengyos-cosmic-build vendor-all engine (:mod:`vendor_all`).

The vendoring engine is driven through an injectable runner (see
:class:`VendorRunner`): :class:`FakeVendorRunner` stands in for the real engine
in most tests (no network / cargo / real sources), and :class:`VendEngineRunner`
drives the real ``pengyos-cosmic_pkg.vendor`` engine against a committed-shape
fixture (a ``Cargo.lock`` + a ``git-sources`` mirror) so the real tarball +
digest path is exercised offline.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_cosmic_build.records import (
    VENDORED_SITE_PLACEHOLDER,
    load_record,
)
from pengyos_cosmic_build.vendor_all import (
    ComponentContext,
    FakeVendorRunner,
    VendEngineRunner,
    VendorAllError,
    vend_all,
)

# --- a cosmic-recipes-shape manifest ---------------------------------------


def _manifest(tmp_path: Path) -> Path:
    """A cosmic-recipes-shape manifest (``[[component]]`` entries).

    Matches the real ``cosmic.toml`` shape: a list of ``[[component]]`` tables,
    each with ``name`` / ``group`` / ``git_url`` / ``tag`` / ``license`` (+
    optional ``features`` / ``binaries`` / ``deps``). ``deps`` must reference
    another component in the manifest (the loader asserts this).
    """
    text = """
[[component]]
name = "cosmic-comp"
group = "core"
git_url = "https://github.com/pop-os/libcosmic/cosmic-comp"
tag = "v0.6.0"
license = "MIT OR Apache-2.0"
features = ["default"]
binaries = ["cosmic-comp"]
deps = ["cosmic-session"]

[[component]]
name = "cosmic-session"
group = "core"
git_url = "https://github.com/pop-os/libcosmic/cosmic-session"
tag = "v0.6.0"
license = "MIT OR Apache-2.0"
features = []
binaries = ["cosmic-session"]
deps = []
"""
    path = tmp_path / "cosmic.toml"
    path.write_text(text, encoding="utf-8")
    return path


# --- fixtures: a checked-out component source + git-sources mirror ----------


def _make_component(
    sources_dir: Path,
    name: str,
    tag: str,
    *,
    git_deps: list[tuple[str, str]] | None = None,
) -> None:
    """Write ``<sources_dir>/<name>/Cargo.lock`` (+ ``git-sources/<slug>/``).

    ``git_deps`` is a list of ``(url, slug)``; each gets a ``git+<url>?tag=<tag>#<sha>``
    entry in the lock and a checked-out crate dir under ``git-sources/<slug>/``.
    """
    comp_dir = sources_dir / name
    comp_dir.mkdir(parents=True, exist_ok=True)

    # Render each package as a proper TOML [[package]] table (not JSON).
    lines = ['version = "3"', ""]

    def _table(fields: dict[str, str]) -> str:
        body = "\n".join(f'{k} = "{v}"' for k, v in fields.items())
        return f"[[package]]\n{body}"

    # The root app.
    lines.append(_table({"name": name, "version": "0.0.0", "edition": "2021"}))
    for git_url, slug in git_deps or []:
        source = f"git+{git_url}?tag={tag}#{'a' * 40}"
        lines.append(_table({"name": slug, "version": "0.0.0", "source": source}))

    cargo_lock = comp_dir / "Cargo.lock"
    cargo_lock.write_text("\n\n".join(lines) + "\n", encoding="utf-8")

    # Each git dep's checked-out crate lives under git-sources/<slug>/.
    git_mirror = comp_dir / "git-sources"
    for git_url, slug in git_deps or []:
        crate_dir = git_mirror / slug
        crate_dir.mkdir(parents=True, exist_ok=True)
        (crate_dir / "Cargo.toml").write_text(
            f'[package]\nname = "{slug}"\nversion = "0.0.0"\n',
            encoding="utf-8",
        )
        src = crate_dir / "src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "lib.rs").write_text("// fixture crate\n", encoding="utf-8")


# --- FakeVendorRunner -------------------------------------------------------


def test_fake_runner_is_deterministic():
    ctx = ComponentContext(
        name="cosmic-comp",
        git_url="https://github.com/pop-os/libcosmic/cosmic-comp",
        tag="v0.6.0",
        license="MIT OR Apache-2.0",
        features=("default",),
        binaries=("cosmic-comp",),
        sources_dir=Path("/tmp"),
    )
    outcome1 = FakeVendorRunner()(ctx, Path("/tmp"), Path("/tmp/dl"))
    outcome2 = FakeVendorRunner()(ctx, Path("/tmp"), Path("/tmp/dl"))
    assert outcome1 == outcome2
    assert outcome1.tarball == "cosmic-comp-v0.6.0.tar.gz"
    assert len(outcome1.sha256) == 64  # 64 hex digits


# --- vend_all with the fake runner ------------------------------------------


def test_vend_all_with_fake_runner(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = tmp_path / "sources"
    (sources / "cosmic-comp").mkdir(parents=True)
    (sources / "cosmic-session").mkdir(parents=True)
    dl = tmp_path / "dl"
    out = tmp_path / "out"

    report = vend_all(
        manifest,
        sources,
        dl,
        out,
        runner=FakeVendorRunner(),
    )

    assert report.names == ("cosmic-comp", "cosmic-session")
    # One record per component.
    for name in report.names:
        record = load_record(out / f"{name}.json")
        assert record.name == name
        record.validate()  # no placeholder, 64-hex sha256, real SITE


def test_vend_all_site_is_real_not_placeholder(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = tmp_path / "sources"
    (sources / "cosmic-comp").mkdir(parents=True)
    dl = tmp_path / "dl"
    out = tmp_path / "out"

    report = vend_all(
        manifest, sources, dl, out, runner=FakeVendorRunner()
    )
    assert report.site.startswith("file://")
    assert report.site != VENDORED_SITE_PLACEHOLDER
    # The file:// URI resolves to the real dl dir on disk (created by the
    # runner when it writes each component's vendored tree).
    from urllib.parse import urlsplit

    resolved = Path(urlsplit(report.site).path)
    assert resolved.is_dir()


def test_vend_all_group_filter(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = tmp_path / "sources"
    (sources / "cosmic-comp").mkdir(parents=True)
    (sources / "cosmic-session").mkdir(parents=True)
    dl = tmp_path / "dl"
    out = tmp_path / "out"

    report = vend_all(
        manifest,
        sources,
        dl,
        out,
        group="core",
        runner=FakeVendorRunner(),
    )
    assert report.names == ("cosmic-comp", "cosmic-session")

    # A group with no components is a reported error.
    with pytest.raises(VendorAllError):
        vend_all(
            manifest, sources, dl, out, group="apps", runner=FakeVendorRunner()
        )


def test_vend_all_empty_group_reported(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = tmp_path / "sources"
    (sources / "cosmic-comp").mkdir(parents=True)
    (sources / "cosmic-session").mkdir(parents=True)
    dl = tmp_path / "dl"
    out = tmp_path / "out"
    # No component is in the 'apps' group -> a reported error.
    with pytest.raises(VendorAllError) as excinfo:
        vend_all(
            manifest,
            sources,
            dl,
            out,
            group="apps",
            runner=FakeVendorRunner(),
        )
    assert "apps" in str(excinfo.value)


# --- vend_all with the REAL engine (offline fixture) ------------------------


def test_vend_all_real_engine_writes_tarball(tmp_path: Path):
    """The real engine vendors a git dep from the mirror and pins a digest."""
    manifest = _manifest(tmp_path)
    sources = tmp_path / "sources"
    _make_component(
        sources,
        "cosmic-comp",
        "v0.6.0",
        git_deps=[("https://github.com/pop-os/cosmic-protocols", "cosmic-protocols")],
    )
    _make_component(
        sources,
        "cosmic-session",
        "v0.6.0",
        git_deps=[("https://github.com/pop-os/cosmic-session-dep", "cosmic-session-dep")],
    )
    dl = tmp_path / "dl"
    out = tmp_path / "out"

    report = vend_all(
        manifest, sources, dl, out, runner=VendEngineRunner()
    )

    comp = next(r for r in report.records if r.name == "cosmic-comp")
    # The tarball the engine named actually lives in the component's vendored
    # dir under the dl dir (``<dl>/<name>/<tarball>``).
    tarball = (dl / "cosmic-comp" / comp.tarball)
    assert tarball.is_file(), f"expected {tarball}"
    # The pinned digest matches the tarball on disk (real, not a placeholder).
    import hashlib

    h = hashlib.sha256()
    with tarball.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    assert comp.sha256 == h.hexdigest()
    assert comp.sha256 != "0" * 64
    record = load_record(out / "cosmic-comp.json")
    assert record.sha256 == comp.sha256


def test_vend_all_real_engine_is_deterministic(tmp_path: Path):
    """Two runs over the same fixture produce the same digest (byte-for-byte)."""
    manifest = _manifest(tmp_path)
    sources = tmp_path / "sources"
    _make_component(
        sources,
        "cosmic-comp",
        "v0.6.0",
        git_deps=[("https://github.com/pop-os/cosmic-protocols", "cosmic-protocols")],
    )
    _make_component(
        sources,
        "cosmic-session",
        "v0.6.0",
        git_deps=[("https://github.com/pop-os/cosmic-session-dep", "cosmic-session-dep")],
    )

    dl1 = tmp_path / "dl1"
    out1 = tmp_path / "out1"
    report1 = vend_all(manifest, sources, dl1, out1, runner=VendEngineRunner())

    dl2 = tmp_path / "dl2"
    out2 = tmp_path / "out2"
    report2 = vend_all(manifest, sources, dl2, out2, runner=VendEngineRunner())

    comp1 = next(r for r in report1.records if r.name == "cosmic-comp")
    comp2 = next(r for r in report2.records if r.name == "cosmic-comp")
    assert comp1.sha256 == comp2.sha256


# --- missing Cargo.lock is a reported error, not a crash --------------------


def test_vend_all_missing_cargo_lock_reported(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = tmp_path / "sources"
    # cosmic-comp has NO Cargo.lock (only the dir exists).
    (sources / "cosmic-comp").mkdir(parents=True)
    (sources / "cosmic-session").mkdir(parents=True)
    dl = tmp_path / "dl"
    out = tmp_path / "out"

    with pytest.raises(VendorAllError) as excinfo:
        vend_all(manifest, sources, dl, out, runner=VendEngineRunner())
    assert "cosmic-comp" in str(excinfo.value)
    assert "Cargo.lock" in str(excinfo.value)


# --- report shape -----------------------------------------------------------


def test_report_as_dict_is_json_serialisable(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = tmp_path / "sources"
    (sources / "cosmic-comp").mkdir(parents=True)
    dl = tmp_path / "dl"
    out = tmp_path / "out"
    report = vend_all(manifest, sources, dl, out, runner=FakeVendorRunner())
    payload = report.as_dict()
    assert payload["command"] == "vendor-all"
    assert payload["ok"] is True
    assert payload["count"] == 2
    assert {c["name"] for c in payload["components"]} == {
        "cosmic-comp",
        "cosmic-session",
    }
    # Round-trips through JSON (the CLI emits exactly this).
    json.dumps(payload)


# --- CLI --------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    """Run ``python -m pengyos_cosmic_build`` as a subprocess.

    The engine + sibling lanes live in-tree (not pip-installed), so the
    subprocess needs them on ``PYTHONPATH`` — exactly what a real user would do
    (``export PYTHONPATH=.../tools/pengyos-cosmic-pkg:…``). This mirrors
    ``conftest.py``'s sys.path wiring, but for the child process.
    """
    import os

    # tools/<sibling> — three parents up from this file (tests/ -> package/ -> tools/).
    tools_dir = Path(__file__).resolve().parent.parent.parent
    env = dict(os.environ)
    siblings = ("pengyos-cosmic-pkg", "pengyos-cosmic-recipes", "pengyos-cosmic-native-deps")
    parts = [str(tools_dir / name) for name in siblings]
    env["PYTHONPATH"] = os.pathsep.join(parts) + (
        os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else ""
    )
    return subprocess.run(
        [sys.executable, "-m", "pengyos_cosmic_build", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_vendor_all_success(tmp_path: Path, repo_root: Path):
    manifest = _manifest(tmp_path)
    sources = tmp_path / "sources"
    _make_component(
        sources,
        "cosmic-comp",
        "v0.6.0",
        git_deps=[("https://github.com/pop-os/cosmic-protocols", "cosmic-protocols")],
    )
    _make_component(
        sources,
        "cosmic-session",
        "v0.6.0",
        git_deps=[("https://github.com/pop-os/cosmic-session-dep", "cosmic-session-dep")],
    )
    dl = tmp_path / "dl"
    out = tmp_path / "out"

    proc = _run(
        "vendor-all",
        "--manifest",
        str(manifest),
        "--sources",
        str(sources),
        "--dl",
        str(dl),
        "--out",
        str(out),
    )
    assert proc.returncode == 0, proc.stderr
    assert "cosmic-comp" in proc.stdout


def test_cli_vendor_all_missing_cargo_lock_reports_cleanly(tmp_path: Path):
    manifest = _manifest(tmp_path)
    sources = tmp_path / "sources"
    (sources / "cosmic-comp").mkdir(parents=True)  # no Cargo.lock
    dl = tmp_path / "dl"
    out = tmp_path / "out"

    proc = _run(
        "vendor-all",
        "--manifest",
        str(manifest),
        "--sources",
        str(sources),
        "--dl",
        str(dl),
        "--out",
        str(out),
    )
    assert proc.returncode == 1
    assert "Cargo.lock" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_cli_vendor_all_json_mode(tmp_path: Path, repo_root: Path):
    manifest = _manifest(tmp_path)
    sources = tmp_path / "sources"
    _make_component(
        sources,
        "cosmic-comp",
        "v0.6.0",
        git_deps=[("https://github.com/pop-os/cosmic-protocols", "cosmic-protocols")],
    )
    _make_component(
        sources,
        "cosmic-session",
        "v0.6.0",
        git_deps=[("https://github.com/pop-os/cosmic-session-dep", "cosmic-session-dep")],
    )
    dl = tmp_path / "dl"
    out = tmp_path / "out"

    proc = _run(
        "vendor-all",
        "--manifest",
        str(manifest),
        "--sources",
        str(sources),
        "--dl",
        str(dl),
        "--out",
        str(out),
        "--json",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["command"] == "vendor-all"
    assert payload["ok"] is True
    assert payload["count"] == 2
    assert {c["name"] for c in payload["components"]} == {
        "cosmic-comp",
        "cosmic-session",
    }
