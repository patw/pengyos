"""Tests for the pengyos-cosmic-build emit engine (:mod:`emit`).

:func:`emit` joins the recipes-lane manifest with the vendored-source records
produced by :mod:`.vendor_all` and renders, for every component in a group, the
three Buildroot recipe files (``Config.in`` / ``<pkg>.mk`` / ``<pkg>.hash``)
pinning the **real** digest + SITE (never the placeholder) and keyed off the
defconfig's ``BR2_PACKAGE_*`` symbol (see :func:`symbol_for`).

The renderers (:func:`render_config_in` / :func:`render_mk` / :func:`render_hash`)
are pure and unit-tested directly; :func:`emit_group` / :func:`emit_one` are
tested against the real cosmic-recipes manifest + committed-shape vendor records
(no network / engine / cargo); the CLI path is exercised end-to-end as a
subprocess (with the sibling lanes on ``PYTHONPATH``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from pengyos_cosmic_build.emit import (
    EmitError,
    emit_group,
    render_config_in,
    render_hash,
    render_mk,
)
from pengyos_cosmic_build.records import (
    VENDORED_SITE_PLACEHOLDER,
    VendorRecord,
    symbol_for,
    write_record,
)

# --- a fake component (the manifest shape emit reads) -----------------------


def _component(
    name: str = "cosmic-comp",
    *,
    git_url: str = "https://github.com/pop-os/libcosmic/cosmic-comp",
    license: str = "MIT OR Apache-2.0",
    features: tuple[str, ...] = (),
    binaries: tuple[str, ...] = (),
) -> types.SimpleNamespace:
    """A manifest-component stand-in (name/git_url/license/features/binaries)."""
    return types.SimpleNamespace(
        name=name,
        git_url=git_url,
        license=license,
        features=features,
        binaries=binaries,
    )


def _record(
    name: str = "cosmic-comp",
    *,
    version: str = "v0.6.0",
    sha256: str = "b" * 64,
    site: str = "file:///home/ops/dl/cosmic-comp",
) -> VendorRecord:
    """A valid VendorRecord, overridable per-test."""
    return VendorRecord(
        name=name,
        version=version,
        tarball=f"{name}-{version}.tar.gz",
        sha256=sha256,
        site=site,
    )


# --- render_config_in -------------------------------------------------------


def test_render_config_in_uses_defconfig_symbol():
    text = render_config_in(symbol_for("cosmic-comp"), "cosmic-comp", "https://x")
    assert text.startswith(f"config {symbol_for('cosmic-comp')}\n")
    assert '\tbool "cosmic-comp"' in text
    # A cargo package depends on + selects the host Rust toolchain.
    assert "depends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS" in text
    assert "select BR2_PACKAGE_HOST_RUSTC" in text
    assert "https://x" in text


def test_render_config_in_help_is_tab_indent():
    # Buildroot's check-package wants <tab><2 spaces> for help lines.
    text = render_config_in(symbol_for("greetd"), "greetd", "https://x")
    assert "\thelp\n" in text
    assert "\t  greetd — a COSMIC git component." in text


def test_render_config_in_is_check_package_clean():
    """The rendered ``Config.in`` is check-package-clean (no whitespace lint).

    Buildroot's ``utils/check-package`` (the cheapest verification rung) fails
    on a bare ``help`` keyword, help body that is not ``<tab><2 spaces>``, a
    *blank* help line that still carries the ``<tab><2 spaces>`` indent (that is
    "trailing whitespace"), and a trailing blank line at end of file. The
    reference ``buildroot-external`` recipe is clean, so the emitted one must be
    too — this locks in that shape so a future edit can't reintroduce the
    engine's own (unclean) bare-``help`` form.
    """
    text = render_config_in(symbol_for("cosmic-comp"), "cosmic-comp", "https://x")
    lines = text.split("\n")
    # The help keyword is tab-indented (the engine's bare "help" fails).
    assert "\thelp\n" in text
    # Every non-empty line is free of trailing whitespace.
    for line in lines:
        if line.strip():
            assert line == line.rstrip(), f"trailing whitespace in {line!r}"
    # No trailing blank line at end of file (the file ends right after the URL).
    assert not text.endswith("\n\n"), "Config.in must not end with a blank line"
    # The blank lines *between* help lines are truly empty (not <tab><2 spaces>).
    assert "\n\n" in text, "expected truly-empty blank lines in the help body"
    assert "\t  \n" not in text, "blank help lines must not carry the indent"


# --- render_mk --------------------------------------------------------------


def test_render_mk_uses_name_not_symbol_as_var_prefix():
    # The Make var is <UPPER_NAME> (COSMIC_COMP), NOT the BR2_PACKAGE_ symbol.
    text = render_mk(
        name="cosmic-comp",
        version="v0.6.0",
        tarball="cosmic-comp-v0.6.0.tar.gz",
        site="file:///home/ops/dl/cosmic-comp",
        license_expr="MIT OR Apache-2.0",
        binaries=("cosmic-comp",),
    )
    assert "COSMIC_COMP_VERSION = v0.6.0" in text
    assert "COSMIC_COMP_SOURCE = cosmic-comp-v0.6.0.tar.gz" in text
    assert "COSMIC_COMP_SITE = file:///home/ops/dl/cosmic-comp" in text
    assert "COSMIC_COMP_LICENSE = MIT OR Apache-2.0" in text
    assert "COSMIC_COMP_CARGO_BINARIES = cosmic-comp" in text
    assert "$(eval $(cargo-package))" in text
    # The BR2_PACKAGE_ prefix must NOT leak into the Make vars.
    assert "BR2_PACKAGE_COSMIC_COMP_VERSION" not in text


def test_render_mk_real_site_never_placeholder():
    text = render_mk(
        name="cosmic-comp",
        version="v0.6.0",
        tarball="cosmic-comp-v0.6.0.tar.gz",
        site="file:///home/ops/dl/cosmic-comp",
        license_expr="MIT OR Apache-2.0",
    )
    assert VENDORED_SITE_PLACEHOLDER not in text
    assert "COSMIC_COMP_SITE = file:///home/ops/dl/cosmic-comp" in text


def test_render_mk_license_files_mapped():
    text = render_mk(
        name="cosmic-comp",
        version="v0.6.0",
        tarball="cosmic-comp-v0.6.0.tar.gz",
        site="file:///home/ops/dl/cosmic-comp",
        license_expr="MIT OR Apache-2.0",
    )
    assert "COSMIC_COMP_LICENSE_FILES = LICENSE-MIT LICENSE-APACHE" in text


def test_render_mk_features_and_binaries_optional():
    with_feat = render_mk(
        name="cosmic-comp",
        version="v0.6.0",
        tarball="cosmic-comp-v0.6.0.tar.gz",
        site="file:///home/ops/dl/cosmic-comp",
        license_expr="MIT",
        features=("default", "extra"),
        binaries=("cosmic-comp",),
    )
    assert "COSMIC_COMP_CARGO_FEATURES = default extra" in with_feat
    no_feat = render_mk(
        name="cosmic-protocols",
        version="v0.6.0",
        tarball="cosmic-protocols-v0.6.0.tar.gz",
        site="file:///home/ops/dl/cosmic-protocols",
        license_expr="MIT",
    )
    assert "CARGO_FEATURES" not in no_feat
    assert "CARGO_BINARIES" not in no_feat


# --- render_hash ------------------------------------------------------------


def test_render_hash_shape():
    body = render_hash("cosmic-comp-v0.6.0.tar.gz", "b" * 64)
    assert body == f"sha256  {'b' * 64}  cosmic-comp-v0.6.0.tar.gz\n"


# --- emit_group -------------------------------------------------------------


def _manifest(tmp_path: Path) -> Path:
    """A cosmic-recipes-shape manifest (matches the committed fixture shape)."""
    text = """
[[component]]
name = "cosmic-comp"
group = "core"
git_url = "https://github.com/pop-os/libcosmic/cosmic-comp"
tag = "v0.6.0"
license = "MIT OR Apache-2.0"
features = []
binaries = ["cosmic-comp"]
deps = []

[[component]]
name = "cosmic-protocols"
group = "core"
git_url = "https://github.com/pop-os/cosmic-protocols"
tag = "v0.6.0"
license = "MIT"
features = []
binaries = []
deps = []
"""
    path = tmp_path / "cosmic.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _write_vendor_records(vendor_dir: Path, *records: VendorRecord) -> None:
    """Write exactly the passed records (real sha256 + real SITE)."""
    vendor_dir.mkdir(parents=True, exist_ok=True)
    for rec in records:
        write_record(rec, vendor_dir / f"{rec.name}.json")


def test_emit_group_pins_real_digest_and_site(tmp_path: Path):
    from pengyos_cosmic_recipes.manifest import load_manifest

    manifest = load_manifest(_manifest(tmp_path))
    vendor = tmp_path / "vendor"
    _write_vendor_records(vendor, _record("cosmic-comp"), _record("cosmic-protocols"))
    out = tmp_path / "pkg"

    report = emit_group(manifest, "core", vendor, out)
    assert report.is_complete
    assert report.emitted_names() == ("cosmic-comp", "cosmic-protocols")

    # .mk pins the REAL site (never the placeholder).
    mk = (out / "package" / "cosmic-comp" / "cosmic-comp.mk").read_text()
    assert "COSMIC_COMP_SITE = file:///home/ops/dl/cosmic-comp" in mk
    assert VENDORED_SITE_PLACEHOLDER not in mk

    # .hash pins the REAL digest.
    hash_body = (out / "package" / "cosmic-comp" / "cosmic-comp.hash").read_text()
    assert hash_body == f"sha256  {'b' * 64}  cosmic-comp-v0.6.0.tar.gz\n"

    # Config.in is keyed off the defconfig symbol.
    config = (out / "package" / "cosmic-comp" / "Config.in").read_text()
    assert symbol_for("cosmic-comp") in config


def test_emit_group_symbols_match_defconfig_convention(tmp_path: Path):
    """Every emitted symbol uses the defconfig's transform exactly."""
    from pengyos_cosmic_recipes.manifest import load_manifest

    manifest = load_manifest(_manifest(tmp_path))
    vendor = tmp_path / "vendor"
    _write_vendor_records(vendor, _record("cosmic-comp"), _record("cosmic-protocols"))
    out = tmp_path / "pkg"
    report = emit_group(manifest, "core", vendor, out)

    # Every emitted recipe's symbol uses the defconfig's transform exactly:
    # symbol_for(name) == BR2_PACKAGE_<UPPER, dashes->underscores>. This is the
    # guarantee emit owns; membership in the target defconfig is the lint
    # step's job.
    for result in report.results:
        assert result.symbol == symbol_for(result.name)
        # And the emitted Config.in keys off that exact symbol.
        config = (
            out / "package" / result.name / "Config.in"
        ).read_text()
        assert config.startswith(f"config {result.symbol}\n")


def test_emit_group_is_idempotent(tmp_path: Path):
    from pengyos_cosmic_recipes.manifest import load_manifest

    manifest = load_manifest(_manifest(tmp_path))
    vendor = tmp_path / "vendor"
    _write_vendor_records(vendor, _record("cosmic-comp"), _record("cosmic-protocols"))

    out1 = tmp_path / "pkg1"
    out2 = tmp_path / "pkg2"
    emit_group(manifest, "core", vendor, out1)
    emit_group(manifest, "core", vendor, out2)

    for name in ("cosmic-comp", "cosmic-protocols"):
        for suffix in ("Config.in", f"{name}.mk", f"{name}.hash"):
            a = (out1 / "package" / name / suffix).read_bytes()
            b = (out2 / "package" / name / suffix).read_bytes()
            assert a == b, f"{name}/{suffix} is not deterministic"


def test_emit_group_missing_record_reported(tmp_path: Path):
    """A component with no vendor record is reported (not a crash)."""
    from pengyos_cosmic_recipes.manifest import load_manifest

    manifest = load_manifest(_manifest(tmp_path))
    vendor = tmp_path / "vendor"
    # Only cosmic-comp gets a record; cosmic-protocols is missing.
    _write_vendor_records(vendor, _record("cosmic-comp"))
    out = tmp_path / "pkg"

    report = emit_group(manifest, "core", vendor, out)
    assert report.emitted_names() == ("cosmic-comp",)
    assert report.missing_records == ("cosmic-protocols",)
    assert not report.is_complete
    # The emitted one still lands; the missing one is simply absent.
    assert (out / "package" / "cosmic-comp" / "cosmic-comp.mk").is_file()
    assert not (out / "package" / "cosmic-protocols").exists()


def test_emit_group_empty_group_raises(tmp_path: Path):
    from pengyos_cosmic_recipes.manifest import load_manifest

    manifest = load_manifest(_manifest(tmp_path))
    vendor = tmp_path / "vendor"
    _write_vendor_records(vendor, _record("cosmic-comp"), _record("cosmic-protocols"))
    with pytest.raises(EmitError) as excinfo:
        emit_group(manifest, "apps", vendor, tmp_path / "pkg")
    assert "apps" in str(excinfo.value)


def test_emit_group_placeholder_record_rejected(tmp_path: Path):
    """A placeholder SITE can never reach an emitted recipe."""
    from pengyos_cosmic_recipes.manifest import load_manifest

    manifest = load_manifest(_manifest(tmp_path))
    vendor = tmp_path / "vendor"
    _write_vendor_records(
        vendor,
        _record("cosmic-comp", site=VENDORED_SITE_PLACEHOLDER),
    )
    with pytest.raises(EmitError) as excinfo:
        emit_group(manifest, "core", vendor, tmp_path / "pkg")
    assert "cosmic-comp" in str(excinfo.value)


def test_emit_one_rejects_placeholder_record(tmp_path: Path):
    """emit_one validates the record (a placeholder raises EmitError)."""
    from pengyos_cosmic_build.emit import emit_one

    vendor = _record("cosmic-comp", site=VENDORED_SITE_PLACEHOLDER)
    with pytest.raises(EmitError):
        emit_one(_component("cosmic-comp"), vendor, out_dir=tmp_path)


# --- CLI --------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    """Run ``python -m pengyos_cosmic_build`` as a subprocess (siblings on path)."""
    import os

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


def test_cli_emit_success(tmp_path: Path):
    manifest = _manifest(tmp_path)
    vendor = tmp_path / "vendor"
    _write_vendor_records(vendor, _record("cosmic-comp"), _record("cosmic-protocols"))
    out = tmp_path / "pkg"

    proc = _run(
        "emit",
        "--manifest",
        str(manifest),
        "--vendor",
        str(vendor),
        "--out",
        str(out),
        "--group",
        "core",
    )
    assert proc.returncode == 0, proc.stderr
    assert "cosmic-comp" in proc.stdout
    assert (out / "package" / "cosmic-comp" / "cosmic-comp.mk").is_file()


def test_cli_emit_json_mode(tmp_path: Path):
    manifest = _manifest(tmp_path)
    vendor = tmp_path / "vendor"
    _write_vendor_records(vendor, _record("cosmic-comp"), _record("cosmic-protocols"))
    out = tmp_path / "pkg"

    proc = _run(
        "emit",
        "--manifest",
        str(manifest),
        "--vendor",
        str(vendor),
        "--out",
        str(out),
        "--group",
        "core",
        "--json",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["command"] == "emit"
    assert payload["ok"] is True
    assert payload["count"] == 2
    assert {c["name"] for c in payload["components"]} == {
        "cosmic-comp",
        "cosmic-protocols",
    }


def test_cli_emit_missing_record_reports_cleanly(tmp_path: Path):
    manifest = _manifest(tmp_path)
    vendor = tmp_path / "vendor"
    # cosmic-comp only; cosmic-protocols has no record.
    _write_vendor_records(vendor, _record("cosmic-comp"))

    proc = _run(
        "emit",
        "--manifest",
        str(manifest),
        "--vendor",
        str(vendor),
        "--out",
        str(tmp_path / "pkg"),
        "--group",
        "core",
    )
    assert proc.returncode == 1
    assert "cosmic-protocols" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_cli_emit_placeholder_record_reports_cleanly(tmp_path: Path):
    manifest = _manifest(tmp_path)
    vendor = tmp_path / "vendor"
    _write_vendor_records(
        vendor, _record("cosmic-comp", site=VENDORED_SITE_PLACEHOLDER)
    )

    proc = _run(
        "emit",
        "--manifest",
        str(manifest),
        "--vendor",
        str(vendor),
        "--out",
        str(tmp_path / "pkg"),
        "--group",
        "core",
        "--json",
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert "cosmic-comp" in payload["error"]
    assert "Traceback" not in proc.stderr


# --- opt-in integration: the real Buildroot check-package --------------------


def _check_package_bin() -> str | None:
    """The Buildroot ``utils/check-package`` binary, if the checkout exists.

    The cheapest verification rung (ladder step a) is Buildroot's own recipe
    linter — no build, no network. It is opt-in: the checkout is not part of the
    repo, so the ordinary suite skips when it is absent.
    """
    for candidate in (
        os.environ.get("PENGYOS_BR_CHECK_PACKAGE"),
        str(Path("~/build/pengyos-2608/buildroot/utils/check-package").expanduser()),
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


@pytest.mark.skipif(
    os.environ.get("PENGYOS_IT") != "1",
    reason="opt-in integration test (set PENGYOS_IT=1)",
)
def test_integration_emit_passes_check_package(tmp_path: Path):
    """The emitted recipes pass Buildroot's ``check-package`` (zero warnings)."""
    import subprocess

    check_package = _check_package_bin()
    if check_package is None:
        pytest.skip("Buildroot check-package not found (set PENGYOS_IT=1 + a checkout)")

    from pengyos_cosmic_recipes.manifest import load_manifest

    manifest = load_manifest(_manifest(tmp_path))
    vendor = tmp_path / "vendor"
    _write_vendor_records(
        vendor, _record("cosmic-comp"), _record("cosmic-protocols")
    )
    out = tmp_path / "pkg"
    report = emit_group(manifest, "core", vendor, out)
    assert report.is_complete

    files = sorted(
        str(p)
        for name in report.emitted_names()
        for p in (out / "package" / name).iterdir()
    )
    proc = subprocess.run(
        [check_package, "--br2-external", *files],
        capture_output=True,
        text=True,
    )
    # check-package exits non-zero on ANY warning; a clean emit is exit 0.
    assert proc.returncode == 0, proc.stderr
    assert "warnings generated" in proc.stderr
    assert "0 warnings generated" in proc.stderr
