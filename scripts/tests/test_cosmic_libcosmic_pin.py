"""Tests for scripts/cosmic-libcosmic-pin.py.

Run: python3 -m pytest scripts/tests -q   (from the repo root)

These exist because the first real run of the helper broke the COSMIC build twice:
once on a cargo rule (a patch may not point at the source it replaces) and once on
a TOML rule (nine components already declare a libcosmic patch section, usually
single-quoted, and a second one is a duplicate key). Both are reproduced here from
the shapes the real component manifests have.
"""

import subprocess
import sys
from pathlib import Path

import tomllib

TOOL = Path(__file__).resolve().parents[1] / "cosmic-libcosmic-pin.py"
REV = "87ab8179e1bd9880239c340855ae8862034bd0e8"
SOURCE = "https://github.com/pop-os/libcosmic"


def run(component: Path, rev: str = REV) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), str(component), rev],
                          capture_output=True, text=True)


def make(tmp_path, cargo_toml: str, lock: str | None = None) -> Path:
    comp = tmp_path / "cosmic-x"
    comp.mkdir()
    (comp / "Cargo.toml").write_text(cargo_toml)
    if lock is not None:
        (comp / "Cargo.lock").write_text(lock)
    return comp


LOCK = """[[package]]
name = "iced_core"
source = "git+https://github.com/pop-os/libcosmic#2f0b3334914e4ab1b0f3df821eeadd7ad700566f"

[[package]]
name = "cosmic-config"
source = "git+https://github.com/pop-os/libcosmic#2f0b3334914e4ab1b0f3df821eeadd7ad700566f"
"""


def patched(toml_path: Path) -> dict:
    data = tomllib.loads(toml_path.read_text())
    return data.get("patch", {}).get(SOURCE, {})


# --- the merge case: cosmic-osd's single-quoted section ---------------------

def test_merges_into_a_single_quoted_section_without_duplicating_it(tmp_path):
    """`[patch.'https://github.com/pop-os/libcosmic']` is the SAME TOML key as
    `[patch."..."]`; appending a second one is a parse error, which is exactly
    what broke the first real run."""
    comp = make(tmp_path, """[package]
name = "cosmic-osd"

[patch.'https://github.com/pop-os/libcosmic']
# libcosmic = { path = "../libcosmic" }
# iced_futures = { git = "https://github.com/pop-os/libcosmic//", branch = "theme-v2" }

[profile.release]
lto = "thin"
""", LOCK)
    result = run(comp)
    assert result.returncode == 0, result.stderr
    entries = patched(comp / "Cargo.toml")
    assert set(entries) == {"iced_core", "cosmic-config"}
    assert {e["rev"] for e in entries.values()} == {REV}
    # The section boundary survived: [profile.release] is still its own table.
    assert tomllib.loads((comp / "Cargo.toml").read_text())["profile"]["release"]["lto"] == "thin"


def test_entries_land_inside_the_section_not_after_the_next_one(tmp_path):
    """Inserting after the header but past the following table header would put
    the patch entries in the wrong table."""
    comp = make(tmp_path, """[package]
name = "cosmic-x"

[patch."https://github.com/pop-os/libcosmic"]

[dependencies]
serde = "1"
""", LOCK)
    run(comp)
    data = tomllib.loads((comp / "Cargo.toml").read_text())
    assert "iced_core" not in data.get("dependencies", {})
    assert "iced_core" in patched(comp / "Cargo.toml")


def test_a_conflicting_active_entry_is_commented_out_not_left_beside_ours(tmp_path):
    """Two keys of one name in one table is a TOML error, and the coherence pin
    has to win over an upstream branch pin."""
    comp = make(tmp_path, """[package]
name = "cosmic-x"

[patch."https://github.com/pop-os/libcosmic"]
iced_core = { git = "https://github.com/pop-os/libcosmic//", branch = "theme-v2" }
""", LOCK)
    run(comp)
    entries = patched(comp / "Cargo.toml")
    assert entries["iced_core"].get("rev") == REV
    assert "branch" not in entries["iced_core"]
    text = (comp / "Cargo.toml").read_text()
    assert text.count("iced_core = {") == 2          # ours + the commented-out one
    commented = [l for l in text.splitlines()
                 if l.lstrip().startswith("# iced_core = {")]
    assert commented, "the superseded entry should be kept as a comment"


# --- the no-section case ----------------------------------------------------

def test_creates_one_section_when_the_component_has_none(tmp_path):
    comp = make(tmp_path, '[package]\nname = "cosmic-idle"\n', LOCK)
    run(comp)
    text = (comp / "Cargo.toml").read_text()
    assert text.count(f'[patch."{SOURCE}"]') == 1
    assert set(patched(comp / "Cargo.toml")) == {"iced_core", "cosmic-config"}


def test_the_patch_url_carries_the_trailing_slash_that_cargo_requires(tmp_path):
    """cargo rejects a patch pointing at the same source it replaces:
    "patches must point to different sources". pop-os' own idiom is `//`."""
    comp = make(tmp_path, '[package]\nname = "cosmic-x"\n', LOCK)
    run(comp)
    for name, entry in patched(comp / "Cargo.toml").items():
        assert entry["git"] == f"{SOURCE}//", f"{name}: patch source must differ from the dep"


def test_no_lock_still_pins_libcosmic_itself(tmp_path):
    """Six components ship no Cargo.lock upstream, so cargo resolves at build
    time and the patch has to be in place before it does."""
    comp = make(tmp_path, '[package]\nname = "cosmic-session"\n')
    run(comp)
    assert "libcosmic" in patched(comp / "Cargo.toml")


# --- idempotency and validation --------------------------------------------

def test_running_twice_does_not_stack_blocks(tmp_path):
    comp = make(tmp_path, """[package]
name = "cosmic-x"

[patch.'https://github.com/pop-os/libcosmic']
# commented example
""", LOCK)
    run(comp)
    run(comp)
    text = (comp / "Cargo.toml").read_text()
    assert text.count("iced_core = {") == 1
    assert len(patched(comp / "Cargo.toml")) == 2
    patched(comp / "Cargo.toml")            # still parses


def test_rejects_a_rev_that_is_not_a_commit(tmp_path):
    comp = make(tmp_path, '[package]\nname = "cosmic-x"\n', LOCK)
    result = run(comp, rev="main")
    assert result.returncode == 2
    assert "commit sha" in result.stderr
    assert "pengyos" not in (comp / "Cargo.toml").read_text()


def test_non_cargo_components_are_left_alone(tmp_path):
    comp = make(tmp_path, "")
    comp.joinpath("Cargo.toml").unlink()
    result = run(comp)
    assert result.returncode == 0
    assert not (comp / "Cargo.toml").exists()


# --- --verify: the step that caught the pin silently not taking effect -----

OLD_REV = "2f0b3334914e4ab1b0f3df821eeadd7ad700566f"


def verify(component: Path, rev: str = REV) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), "--verify", str(component), rev],
                          capture_output=True, text=True)


def test_verify_fails_a_lock_that_kept_the_old_rev(tmp_path):
    """What actually happened to cosmic-idle: the patch was accepted, cargo said
    "patch was not used in the crate graph", and the lock kept a 2025 libcosmic."""
    comp = make(tmp_path, '[package]\nname = "cosmic-idle"\n',
                f'[[package]]\nname = "iced_core"\nversion = "0.14.0-dev"\n'
                f'source = "git+https://github.com/pop-os/libcosmic#{OLD_REV}"\n')
    result = verify(comp)
    assert result.returncode == 1
    assert "INCOHERENT" in result.stdout and OLD_REV[:12] in result.stdout


def test_verify_accepts_a_lock_that_resolved_to_the_pin(tmp_path):
    comp = make(tmp_path, '[package]\nname = "cosmic-comp"\n',
                f'[[package]]\nname = "iced_core"\nversion = "0.14.0"\n'
                f'source = "git+https://github.com/pop-os/libcosmic//?rev={REV}#{REV}"\n')
    assert verify(comp).returncode == 0


def test_verify_catches_a_mixed_lock(tmp_path):
    """Two revs in one lock is the epoch-1.9 cosmic-settings shape."""
    comp = make(tmp_path, '[package]\nname = "cosmic-settings"\n',
                f'[[package]]\nname = "libcosmic"\nsource = "git+https://github.com/pop-os/libcosmic//?rev={REV}#{REV}"\n'
                f'\n[[package]]\nname = "cosmic-config"\nsource = "git+https://github.com/pop-os/libcosmic#{OLD_REV}"\n')
    result = verify(comp)
    assert result.returncode == 1
    assert OLD_REV[:12] in result.stdout and REV[:12] in result.stdout


def test_verify_ignores_a_component_with_no_libcosmic_dependency(tmp_path):
    comp = make(tmp_path, '[package]\nname = "cosmic-icons"\n',
                '[[package]]\nname = "serde"\nversion = "1.0.0"\n')
    result = verify(comp)
    assert result.returncode == 0
    assert "no libcosmic dependency" in result.stdout


def test_verify_rejects_a_bad_rev_argument(tmp_path):
    comp = make(tmp_path, '[package]\nname = "x"\n')
    assert verify(comp, rev="main").returncode == 2


# --- mutation checks: the promises that would be invisible if broken -------

def test_mutation_two_sections_in_one_file_is_what_the_merge_prevents(tmp_path):
    """Prove the failure mode is real: a naive append of a second section does not
    parse. If this test ever passes, the merge logic is no longer needed."""
    broken = '[package]\nname = "x"\n\n[patch."%s"]\n\n[patch."%s"]\n' % (SOURCE, SOURCE)
    comp = make(tmp_path, broken, LOCK)
    try:
        tomllib.loads((comp / "Cargo.toml").read_text())
    except tomllib.TOMLDecodeError:
        return
    raise AssertionError("expected a duplicate-table error; the merge guard is pointless now")
