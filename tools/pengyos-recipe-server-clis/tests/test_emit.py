"""Tests for the Buildroot recipe emitter (``pengyos_recipe_server_clis.emit``).

Covers the "Implement emit" subtask: each tool emits a **golden, deterministic**
recipe set — ``Config.in`` + ``<tool>.mk`` + ``<tool>.hash`` — mirroring the
``fzf`` / ``starship`` shape already in ``pengyos-server-toolset``, with:

* the correct ``BR2_PACKAGE_<TOOL>`` symbol,
* the right build-macro per upstream family (cargo / golang / cmake),
* the hidden-``config`` vs ``menuconfig`` choice (all hidden — no sub-options),
* a pinned version, a ``$(call github, …)`` source URL and a declared license,
* a ``.hash`` carrying the committed source sha256.

The golden output is compared against committed fixtures under
``tests/fixtures/emit``; the structural assertions (macro per tool, symbol
format, host-dep presence, hash content) are written *independent* of the
emitter's own tables so they have teeth against a regression.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pengyos_recipe_server_clis import emit
from pengyos_recipe_server_clis.catalogue import TOOL_NAMES, load_catalogue

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "emit"

# The build-macro each upstream family maps to — taken straight from Buildroot
# conventions, NOT from the emitter's FAMILIES table, so a wrong mapping here is
# a real failure.
EXPECTED_MACRO = {
    "zoxide": "cargo-package",      # Rust (Buildroot itself ships it as cargo)
    "sd": "cargo-package",          # Rust
    "node_exporter": "golang-package",  # Go
    "btop": "cmake-package",        # C/C++ (CMake)
    "helix": "cargo-package",       # Rust
    "neovim": "cmake-package",      # C/C++ (CMake)
}

# Tools that need a ``<VAR>_GOMOD`` line in the .mk (only the Go family).
GOMOD_TOOLS = {"node_exporter"}

# Tools whose family needs a host-toolchain arch-support + select line.
HOST_DEP_TOOLS = {"zoxide", "sd", "node_exporter", "helix"}  # cargo + golang


# --------------------------------------------------------------------------- #
# tiny parsers for the emitted recipe files
# --------------------------------------------------------------------------- #


def _read(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _mk_var(text: str, name: str) -> str | None:
    """Return the RHS of ``NAME = <rhs>`` in a ``.mk``, or ``None`` if absent."""
    m = re.search(rf"^{name} = (.*)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _hash_line(text: str, algo: str, source: str) -> str | None:
    """Return the digest for ``algo  …  <source>`` in a ``.hash``, or ``None``."""
    m = re.search(rf"^{algo}  ([0-9a-f]+)  {re.escape(source)}$", text, re.MULTILINE)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# golden output — byte-for-byte against the committed fixtures
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tool", load_catalogue(), ids=lambda t: t.name)
def test_emit_matches_golden_fixture(tool: object, tmp_path: Path) -> None:
    """The emitted files are byte-for-byte the golden fixtures (regression guard)."""
    pkg = tmp_path / "package" / tool.name  # tool is a Tool
    emit.emit_tool(tool, tmp_path)
    for rel in ("Config.in", f"{tool.name}.mk", f"{tool.name}.hash"):
        got = _read(pkg / rel)
        want = _read(FIXTURE_ROOT / "package" / tool.name / rel)
        assert got == want, f"{tool.name}/{rel} drifted from the golden fixture"


def test_emit_writes_exactly_three_files_per_tool(tmp_path: Path) -> None:
    written = emit.emit_all(load_catalogue(), tmp_path)
    assert set(written) == set(TOOL_NAMES)
    for name in TOOL_NAMES:
        paths = written[name]
        assert [p.name for p in paths] == [
            "Config.in",
            f"{name}.mk",
            f"{name}.hash",
        ]
        # recipes land in <out>/package/<tool>/ (Buildroot external-tree layout)
        for p in paths:
            assert p.parent.parent.name == "package", p
            assert p.parent.name == name, p
            assert p.stat().st_size > 0, f"{p} is empty"


def test_emit_is_deterministic(tmp_path: Path) -> None:
    """Two emits of the same catalogue are byte-identical (no timestamps/random)."""
    first = tmp_path / "a"
    second = tmp_path / "b"
    emit.emit_all(load_catalogue(), first)
    emit.emit_all(load_catalogue(), second)
    for name in TOOL_NAMES:
        for rel in ("Config.in", f"{name}.mk", f"{name}.hash"):
            assert _read(first / "package" / name / rel) == _read(
                second / "package" / name / rel
            ), (
                f"{name}/{rel} is not deterministic"
            )


# --------------------------------------------------------------------------- #
# structural teeth — the recipe contract, checked independently of emit internals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tool", load_catalogue(), ids=lambda t: t.name)
def test_config_in_uses_hidden_config_not_menuconfig(tool: object) -> None:
    """A package with no sub-options is a hidden ``config`` (like fzf/starship)."""
    text = emit.render_config_in(tool, emit.family_of(tool))
    assert re.match(r"config BR2_PACKAGE_", text.splitlines()[0]), (
        "Config.in must open with a hidden `config` symbol"
    )
    assert "menuconfig" not in text, "no sub-options => must not be `menuconfig`"


@pytest.mark.parametrize("tool", load_catalogue(), ids=lambda t: t.name)
def test_config_in_symbol_matches_tool(tool: object) -> None:
    symbol = f"BR2_PACKAGE_{tool.name.upper()}"
    text = emit.render_config_in(tool, emit.family_of(tool))
    assert text.startswith(f"config {symbol}\n")
    assert f'\tbool "{tool.name}"' in text


@pytest.mark.parametrize("tool", load_catalogue(), ids=lambda t: t.name)
def test_config_in_help_contains_upstream_url(tool: object) -> None:
    text = emit.render_config_in(tool, emit.family_of(tool))
    assert tool.homepage in text, "Config.in help must link the upstream repo"


@pytest.mark.parametrize("tool", load_catalogue(), ids=lambda t: t.name)
def test_config_in_host_dep_matches_family(tool: object) -> None:
    text = emit.render_config_in(tool, emit.family_of(tool))
    needs_host = tool.name in HOST_DEP_TOOLS
    has_dep = "depends on BR2_PACKAGE_HOST_" in text
    has_select = "select BR2_PACKAGE_HOST_" in text
    assert has_dep == needs_host, (
        f"{tool.name}: host-dep line present={has_dep}, expected={needs_host}"
    )
    assert has_select == needs_host, (
        f"{tool.name}: host-select line present={has_select}, expected={needs_host}"
    )
    if needs_host:
        assert "TARGET_ARCH_SUPPORTS" in text


@pytest.mark.parametrize("tool", load_catalogue(), ids=lambda t: t.name)
def test_mk_uses_expected_build_macro(tool: object) -> None:
    macro = emit.family_of(tool).macro
    assert macro == EXPECTED_MACRO[tool.name], (
        f"{tool.name}: family macro {macro!r} != expected {EXPECTED_MACRO[tool.name]!r}"
    )
    text = emit.render_mk(tool, emit.family_of(tool))
    assert f"$(eval $({macro}))" in text


def test_mk_eval_line_matches_buildroot_shape() -> None:
    """Every eval line is exactly "$(eval $(<macro>))" — the fzf/starship shape.

    The canonical shape is taken from Buildroot's own fzf recipe
    (`$(eval $(golang-package))`); the tool-specific macro is swapped in.
    """
    ref_eval = "$(eval $(golang-package))"
    assert re.fullmatch(r"\$\(eval \$\([a-z-]+\)\)$", ref_eval)
    for tool in load_catalogue():
        eval_line = next(
            l for l in emit.render_mk(tool, emit.family_of(tool)).splitlines()
            if l.startswith("$(")
        )
        expected = ref_eval.replace("golang-package", emit.family_of(tool).macro)
        assert eval_line == expected, (
            f"{tool.name}: eval {eval_line!r} != buildroot shape {expected!r}"
        )


@pytest.mark.parametrize("tool", load_catalogue(), ids=lambda t: t.name)
def test_mk_version_is_pinned(tool: object) -> None:
    text = emit.render_mk(tool, emit.family_of(tool))
    version = _mk_var(text, f"{tool.name.upper()}_VERSION")
    assert version == tool.version, f"{tool.name}: VERSION {version!r} != {tool.version!r}"
    assert version not in ("latest", "HEAD", "master", "main"), "version must be pinned"


@pytest.mark.parametrize("tool", load_catalogue(), ids=lambda t: t.name)
def test_mk_site_uses_github_macro_with_site_version(tool: object) -> None:
    text = emit.render_mk(tool, emit.family_of(tool))
    site = _mk_var(text, f"{tool.name.upper()}_SITE")
    owner, _, repo = tool.repo.partition("/")
    expected = f"$(call github,{owner},{repo},{tool.site_version})"
    assert site == expected, (
        f"{tool.name}: SITE {site!r} != {expected!r}"
    )
    assert site.startswith("$(call github,")


@pytest.mark.parametrize("tool", load_catalogue(), ids=lambda t: t.name)
def test_mk_license_declared(tool: object) -> None:
    text = emit.render_mk(tool, emit.family_of(tool))
    assert _mk_var(text, f"{tool.name.upper()}_LICENSE") == tool.license
    assert _mk_var(text, f"{tool.name.upper()}_LICENSE_FILES") == tool.license_file


@pytest.mark.parametrize("tool", load_catalogue(), ids=lambda t: t.name)
def test_mk_gomod_present_iff_golang(tool: object) -> None:
    text = emit.render_mk(tool, emit.family_of(tool))
    has_gomod = _mk_var(text, f"{tool.name.upper()}_GOMOD") is not None
    assert has_gomod == (tool.name in GOMOD_TOOLS), (
        f"{tool.name}: GOMOD present={has_gomod}, expected {tool.name in GOMOD_TOOLS}"
    )


@pytest.mark.parametrize("tool", load_catalogue(), ids=lambda t: t.name)
def test_hash_carries_committed_sha256(tool: object) -> None:
    text = emit.render_hash(tool)
    digest = _hash_line(text, "sha256", tool.source)
    assert digest == tool.sha256, (
        f"{tool.name}: hash digest drifted from the committed catalogue"
    )
    # The .hash names the pinned tarball, never a moving ref.
    assert tool.source.endswith(f"{tool.version}.tar.gz")


def test_hash_has_no_moving_ref_source() -> None:
    for tool in load_catalogue():
        text = emit.render_hash(tool)
        assert "latest" not in text and "HEAD" not in text


# --------------------------------------------------------------------------- #
# CLI: emit writes files; list reports the catalogue
# --------------------------------------------------------------------------- #


def test_cli_emit_writes_recipes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "recipes"
    rc = emit.main(["emit", "--out", str(out)])
    assert rc == 0
    for name in TOOL_NAMES:
        assert (out / "package" / name / "Config.in").is_file()
        assert (out / "package" / name / f"{name}.mk").is_file()
        assert (out / "package" / name / f"{name}.hash").is_file()
    captured = capsys.readouterr().out
    for name in TOOL_NAMES:
        assert f"{name}:" in captured


def test_cli_list_reports_all_tools(capsys: pytest.CaptureFixture[str]) -> None:
    rc = emit.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    for name in TOOL_NAMES:
        assert name in out


def test_cli_list_json_is_valid(capsys: pytest.CaptureFixture[str]) -> None:
    import json

    assert emit.main(["list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {t["name"] for t in payload["tools"]} == set(TOOL_NAMES)
    for entry in payload["tools"]:
        assert entry["symbol"] == f"BR2_PACKAGE_{entry['name'].upper()}"
        assert entry["build"] == EXPECTED_MACRO[entry["name"]]
