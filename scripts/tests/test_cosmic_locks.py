"""Every COSMIC component builds against a lock PengyOS owns, with `--locked`.

WHY
---
build-cosmic.sh compiles ~2,500 crates, and each crate's build script and
proc-macros run on the build host. The lock decides which crates those are, and
until 2026-10-28 it was not binding: cargo ran without `--locked`, and a pin that
did not take triggered `cargo generate-lockfile`, re-resolving the whole graph to
whatever crates.io served that day. cosmic-idle's lock had 21 crates and 165
versions its upstream never named, and nobody had reviewed them.

Now scripts/cosmic-locks/<component>.Cargo.lock is the lock, cargo refuses any
change to it (verified 2026-10-28 on cosmic-bg: a changed checksum and a removed
crate both stop the build before it compiles), and only `--relock` resolves.
These tests catch the regression without cargo.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import tomllib

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
TSV = SCRIPTS / "cosmic-components.tsv"
LOCKS = SCRIPTS / "cosmic-locks"
BUILD = (SCRIPTS / "build-cosmic.sh").read_text()
PACKAGE = SCRIPTS.parent / "buildroot-external" / "package" / "cosmic" / "cosmic.mk"

spec = importlib.util.spec_from_file_location("lock_check", SCRIPTS / "cosmic-lock-check.py")
lock_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock_check)


def _built() -> list[str]:
    """Components the TSV builds with cargo (its args column is not `-`)."""
    out = []
    for line in TSV.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if line.strip() and not line.lstrip().startswith("#") and len(parts) >= 4 \
                and parts[3].strip() != "-":
            out.append(parts[0])
    return out


def test_every_built_component_has_a_committed_lock():
    missing = [c for c in _built() if not (LOCKS / f"{c}.Cargo.lock").is_file()]
    assert not missing, f"no scripts/cosmic-locks lock for {missing}: run --relock"


def test_no_lock_is_left_for_a_component_the_tsv_dropped():
    stale = sorted({p.name.removesuffix(".Cargo.lock") for p in LOCKS.glob("*.Cargo.lock")}
                   - set(_built()))
    assert not stale, f"locks for components no longer built: {stale}"


def test_every_committed_lock_parses_and_names_only_https_sources():
    for lock in sorted(LOCKS.glob("*.Cargo.lock")):
        for p in tomllib.loads(lock.read_text()).get("package", []):
            src = p.get("source", "")
            assert not src or re.match(r"^(registry|git)\+https://", src), (lock.name, src)
            if src.startswith("registry+"):
                assert len(p.get("checksum", "")) == 64, (lock.name, p["name"])
            if src.startswith("git+"):
                assert re.search(r"#[0-9a-f]{40}$", src), \
                    f"{lock.name}: {p['name']} is not pinned to a commit ({src})"


def test_the_build_never_resolves_and_always_passes_locked():
    # Outside relock() the script must not resolve a graph.
    code = "\n".join(l for l in BUILD.splitlines() if not l.lstrip().startswith("#"))
    body = re.sub(r"\nrelock\(\) \{.*?\n\}\n", "\n", code, flags=re.S)
    assert "generate-lockfile" not in body and "cargo fetch" not in body, \
        "only --relock may resolve a lock"
    assert re.search(r"cargo build --release -p \"\$\{app\}\" --features cosmic --locked", body)


def test_the_package_never_resolves_and_always_passes_locked():
    mk = "\n".join(l for l in PACKAGE.read_text().splitlines() if not l.lstrip().startswith("#"))
    assert "generate-lockfile" not in mk and "cargo fetch" not in mk
    # $$@ is make's escape for the shell's $@.
    assert "cargo() { command cargo \"$$@\" --locked; }" in mk
    assert "cosmic-locks/$(c).Cargo.lock $(@D)/$(c)/Cargo.lock" in mk


def test_the_review_lists_what_a_re_resolve_changed():
    ref = '''
[[package]]
name = "own-crate"
version = "0.1.0"

[[package]]
name = "memchr"
version = "2.7.4"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "%s"
''' % ("a" * 64)
    got = ref.replace("2.7.4", "2.7.6") + '''
[[package]]
name = "new-crate"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "%s"
''' % ("b" * 64)
    diff = lock_check.differences(ref, got)
    assert "- memchr 2.7.4 (registry+https://github.com/rust-lang/crates.io-index)" in diff
    assert "+ memchr 2.7.6 (registry+https://github.com/rust-lang/crates.io-index)" in diff
    assert any(d.startswith("+ new-crate 1.0.0") for d in diff)
    assert not any("own-crate" in d for d in diff), "a path crate is the component's own"
    assert lock_check.differences(ref, ref) == []
    assert lock_check.differences(ref, ref.replace("a" * 64, "c" * 64))[0].startswith(
        "! memchr 2.7.4: checksum")
