"""Buildroot Go-package tests (fzf first).

The follow-up re-adds `fzf` and `starship` to the comfort set by shipping *real*
Buildroot packages for them (`package/<pkg>/Config.in` + `<pkg>.mk` + `<pkg>.hash`
+ a defconfig/structural test). "Building the image" is an explicit non-goal, so
these tests never invoke Buildroot, `make`, a Go toolchain or the network: they
parse the committed package files and assert they are a *well-formed* Buildroot
Go package — the same structural contract Buildroot's own `make <pkg>-defconfig`
and download steps rely on.

The tests have teeth: a package with a wrong symbol, a missing `golang-package`
eval, or a corrupted / mis-named hash is asserted to be flagged, so a regression
that ships a broken package fails the suite.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "package"


# --- small Kconfig / .mk / .hash parsers ------------------------------------
#
# These are deliberately tiny and defensive: they only need to recover enough of
# the Buildroot package format for the assertions below, and to raise on the
# shapes a broken package would have.


def _parse_kconfig_block(text: str, symbol: str) -> dict[str, object]:
    """Parse a single ``config <symbol>`` block from Kconfig ``text``.

    Returns a dict with ``name``, ``type`` (the ``bool``/``tristate``/… value),
    ``depends_on`` (list of ``depends on`` RHS tokens), ``select`` (list) and
    ``help`` (the indented help body). Raises :class:`ValueError` when the
    ``config`` block is missing or structurally malformed.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.fullmatch(r"config\s+(\S+)", line):
            if line.split()[1] == symbol:
                start = i
                break
    if start is None:
        raise ValueError(f"no `config {symbol}` block found")

    name = re.fullmatch(r"config\s+(\S+)", lines[start]).group(1)
    block = lines[start + 1 :]
    parsed: dict[str, object] = {"name": name, "type": None, "depends_on": [], "select": []}
    in_help = False
    for line in block:
        # A non-indented line ends the config block (next top-level entry).
        if line and not line[0].isspace():
            break
        if in_help:
            # Help body: indented lines; a less-indented/non-indented line ends
            # it. We keep the text verbatim (stripped of the leading run of
            # spaces) until a blank or de-indented line.
            if not line.strip():
                continue
            if not line[0].isspace():
                break
            body = parsed.get("help", "")
            parsed["help"] = (body + "\n" + line.strip()).strip("\n")
            continue
        body = line.strip()
        m = re.fullmatch(r"(bool|tristate|string|int|hex|composite)\s+\"[^\"]*\"", body)
        if m:
            parsed["type"] = m.group(1)
            continue
        m = re.fullmatch(r"depends on (.+)", body)
        if m:
            parsed["depends_on"].append(m.group(1).strip())
            continue
        m = re.fullmatch(r"select (\S+)", body)
        if m:
            parsed["select"].append(m.group(1))
            continue
        if body == "help":
            in_help = True
            continue
        # Unknown keyword inside the block: still accepted (Kconfig has many),
        # but a second `config` would have ended the block above.
    if not parsed["type"]:
        raise ValueError(f"config {symbol!r} has no type (bool/tristate/...)")
    return parsed


def _mk_assignment(text: str, var: str) -> str | None:
    """Return the RHS of ``VAR = <rhs>`` (first non-``+=`` assignment) in ``text``.

    Returns ``None`` when the variable is not assigned. Whitespace and the
    trailing newline are stripped.
    """
    for line in text.splitlines():
        m = re.match(rf"^\s*{var}\s*=\s*(.+?)\s*$", line)
        if m:
            return m.group(1)
    return None


def _parse_hash_file(text: str) -> dict[str, dict[str, str]]:
    """Parse a Buildroot ``.hash`` file into ``{filename: {algo: digest}}``.

    Each non-comment line is ``<algo>  <digest>  <filename>``. Raises
    :class:`ValueError` on a malformed line.
    """
    out: dict[str, dict[str, str]] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise ValueError(f"malformed hash line {lineno}: {line!r}")
        algo, digest, fname = parts
        if not re.fullmatch(r"(md5|sha1|sha224|sha256|sha384|sha512)", algo):
            raise ValueError(f"unknown hash algo {algo!r} on line {lineno}")
        out.setdefault(fname, {})[algo] = digest
    return out


# --- the validation contract ------------------------------------------------


def validate_go_package(
    pkg_dir: Path,
    symbol: str,
    version: str,
    go_mod: str,
    site_owner: str,
    site_repo: str,
    expected_go2: str,
    expected_sha256: str,
    expected_sha512: str,
) -> list[str]:
    """Return the list of problems with a Buildroot Go package directory.

    An empty list means the package is a well-formed Buildroot Go package:

    * ``Config.in`` defines ``config <symbol>`` as a ``bool`` with a host-Go
      arch-support dependency and selects ``BR2_PACKAGE_HOST_GO``;
    * ``<pkg>.mk`` pins the version, points ``SITE`` at the upstream github
      archive, sets ``GOMOD``, records the license, and evaluates the
      ``golang-package`` infrastructure (which is what makes Buildroot vendor
      and build it);
    * ``<pkg>.hash`` names the *vendored* ``<expected_go2>`` tarball (not the raw
      upstream tarball) with the expected sha256/sha512.
    """
    problems: list[str] = []
    pkg = pkg_dir / "package" if (pkg_dir / "package").is_dir() else pkg_dir
    config_in = pkg / "Config.in"
    mk = pkg / f"{pkg_dir.name}.mk"
    hashf = pkg / f"{pkg_dir.name}.hash"

    if not config_in.is_file():
        problems.append("Config.in missing")
    if not mk.is_file():
        problems.append(f"{pkg_dir.name}.mk missing")
    if not hashf.is_file():
        problems.append(f"{pkg_dir.name}.hash missing")
    if problems:
        return problems

    # Config.in
    try:
        cfg = _parse_kconfig_block(config_in.read_text(encoding="utf-8"), symbol)
    except ValueError as exc:
        problems.append(str(exc))
        cfg = None
    if cfg is not None:
        if cfg["type"] != "bool":
            problems.append(f"Config.in {symbol} type is {cfg['type']!r}, expected bool")
        if not any("HOST_GO" in d for d in cfg["depends_on"]):
            problems.append(f"Config.in {symbol} missing a host-Go dependency")
        if "BR2_PACKAGE_HOST_GO" not in cfg["select"]:
            problems.append(f"Config.in {symbol} must select BR2_PACKAGE_HOST_GO")

    # .mk
    mk_text = mk.read_text(encoding="utf-8")
    var = pkg_dir.name.upper()
    mk_version = _mk_assignment(mk_text, f"{var}_VERSION")
    if mk_version is None or mk_version.lstrip("v") != version:
        problems.append(
            f"{var}_VERSION is {mk_version!r}, expected {version!r}"
        )
    mk_site = _mk_assignment(mk_text, f"{var}_SITE") or ""
    # SITE is written via the `github` helper, and Buildroot expands
    # `$(call github,owner,repo,tag)` VERBATIM — a space after a comma lands in
    # the download URL and the fetch 404s (found 2026-10-12 wiring fzf: the
    # spaced form downloaded `https://github.com/%20%20%20fzf-0.74.4-go2...`).
    # So the no-space form is the only accepted spelling.
    if f"$(call github,{site_owner},{site_repo}," not in mk_site:
        problems.append(
            f"{var}_SITE must be $(call github,{site_owner},{site_repo},…) "
            f"(no spaces: Buildroot puts them in the URL), got {mk_site!r}"
        )
    mk_gomod = _mk_assignment(mk_text, f"{var}_GOMOD")
    if mk_gomod != go_mod:
        problems.append(f"{var}_GOMOD is {mk_gomod!r}, expected {go_mod!r}")
    if "$(eval $(golang-package))" not in mk_text:
        problems.append(f"{var}.mk does not evaluate golang-package")

    # .hash
    try:
        hashes = _parse_hash_file(hashf.read_text(encoding="utf-8"))
    except ValueError as exc:
        problems.append(str(exc))
        hashes = {}
    if hashes:
        if expected_go2 not in hashes:
            problems.append(
                f"hash file must name the vendored {expected_go2!r}, got {sorted(hashes)}"
            )
        else:
            al = hashes[expected_go2]
            if al.get("sha256") != expected_sha256:
                problems.append(f"{expected_go2} sha256 mismatch")
            if al.get("sha512") != expected_sha512:
                problems.append(f"{expected_go2} sha512 mismatch")
    else:
        problems.append("hash file has no usable entries")

    return problems


def validate_cargo_package(
    pkg_dir: Path,
    symbol: str,
    version: str,
    site_owner: str,
    site_repo: str,
    expected_tarball: str,
    expected_sha256: str,
    expected_sha512: str,
) -> list[str]:
    """Return the list of problems with a Buildroot **cargo** (Rust) package.

    An empty list means the package is a well-formed Buildroot cargo package:

    * ``Config.in`` defines ``config <symbol>`` as a ``bool`` with a host-Rust
      arch-support dependency and selects ``BR2_PACKAGE_HOST_RUSTC``;
    * ``<pkg>.mk`` pins the version, points ``SITE`` at the upstream github
      archive, records the license, and evaluates the ``cargo-package``
      infrastructure (which is what makes Buildroot vendor and build it);
    * ``<pkg>.hash`` names the raw upstream ``<expected_tarball>`` with the
      expected sha256/sha512.  Unlike a Go package (whose digest is of the
      vendored ``go2`` repack), a cargo package's digest is of the raw github
      archive tarball that ``$(call github, ...)`` downloads.
    """
    problems: list[str] = []
    pkg = pkg_dir / "package" if (pkg_dir / "package").is_dir() else pkg_dir
    config_in = pkg / "Config.in"
    mk = pkg / f"{pkg_dir.name}.mk"
    hashf = pkg / f"{pkg_dir.name}.hash"

    if not config_in.is_file():
        problems.append("Config.in missing")
    if not mk.is_file():
        problems.append(f"{pkg_dir.name}.mk missing")
    if not hashf.is_file():
        problems.append(f"{pkg_dir.name}.hash missing")
    if problems:
        return problems

    # Config.in
    try:
        cfg = _parse_kconfig_block(config_in.read_text(encoding="utf-8"), symbol)
    except ValueError as exc:
        problems.append(str(exc))
        cfg = None
    if cfg is not None:
        if cfg["type"] != "bool":
            problems.append(f"Config.in {symbol} type is {cfg['type']!r}, expected bool")
        if not any("HOST_RUSTC" in d for d in cfg["depends_on"]):
            problems.append(f"Config.in {symbol} missing a host-Rust dependency")
        if "BR2_PACKAGE_HOST_RUSTC" not in cfg["select"]:
            problems.append(f"Config.in {symbol} must select BR2_PACKAGE_HOST_RUSTC")

    # .mk
    mk_text = mk.read_text(encoding="utf-8")
    var = pkg_dir.name.upper()
    mk_version = _mk_assignment(mk_text, f"{var}_VERSION")
    if mk_version is None or mk_version.lstrip("v") != version:
        problems.append(
            f"{var}_VERSION is {mk_version!r}, expected {version!r}"
        )
    mk_site = _mk_assignment(mk_text, f"{var}_SITE") or ""
    # Same rule as the Go validator: the `github` helper's arguments must carry
    # no spaces — Buildroot expands them verbatim into the download URL.
    if f"$(call github,{site_owner},{site_repo}," not in mk_site:
        problems.append(
            f"{var}_SITE must be $(call github,{site_owner},{site_repo},…) "
            f"(no spaces: Buildroot puts them in the URL), got {mk_site!r}"
        )
    if not mk_text.strip():
        problems.append(f"{var}.mk is empty")
    if "$(eval $(cargo-package))" not in mk_text:
        problems.append(f"{var}.mk does not evaluate cargo-package")
    if not _mk_assignment(mk_text, f"{var}_LICENSE"):
        problems.append(f"{var}.mk does not record a license")

    # .hash
    try:
        hashes = _parse_hash_file(hashf.read_text(encoding="utf-8"))
    except ValueError as exc:
        problems.append(str(exc))
        hashes = {}
    if hashes:
        if expected_tarball not in hashes:
            problems.append(
                f"hash file must name the {expected_tarball!r} tarball, got {sorted(hashes)}"
            )
        else:
            al = hashes[expected_tarball]
            if al.get("sha256") != expected_sha256:
                problems.append(f"{expected_tarball} sha256 mismatch")
            if al.get("sha512") != expected_sha512:
                problems.append(f"{expected_tarball} sha512 mismatch")
    else:
        problems.append("hash file has no usable entries")

    return problems


# --- the real fzf package ---------------------------------------------------

FZF = PACKAGE_DIR / "fzf"
FZF_VERSION = "0.74.4"
FZF_GO2 = "fzf-0.74.4-go2.tar.gz"
# Reproduced from the vendored tarball Buildroot downloads (go mod vendor +
# reproducible repack). Committing the digest gives the suite
# teeth against a corrupted or mis-targeted hash file.
FZF_SHA256 = "de72d134f4d1c3ee08a7322a78d44f4bfcc76fc83830f2ac26c0d55a17429ad4"
FZF_SHA512 = (
    "eec3f9489c71a1f81eeef8d99c66c8637095bbc7d2791179809aea85fb4a6086"
    "2157337e16fd96e5f3757359b90b23352ffcd15c13a8fdd0c35fffca7803d2ee"
)


def test_fzf_package_files_exist():
    for rel in ("Config.in", "fzf.mk", "fzf.hash"):
        p = FZF / rel
        assert p.is_file(), f"missing Buildroot package file: {p}"
        assert p.stat().st_size > 0, f"{p} is empty"


def test_fzf_package_is_wellformed():
    problems = validate_go_package(
        FZF,
        symbol="BR2_PACKAGE_FZF",
        version=FZF_VERSION,
        go_mod="github.com/junegunn/fzf",
        site_owner="junegunn",
        site_repo="fzf",
                expected_go2=FZF_GO2,
        expected_sha256=FZF_SHA256,
        expected_sha512=FZF_SHA512,
    )
    assert problems == [], f"fzf package is not a well-formed Buildroot Go package:\n  - " + "\n  - ".join(problems)


def test_fzf_config_in_help_mentions_upstream():
    cfg = _parse_kconfig_block((FZF / "Config.in").read_text(encoding="utf-8"), "BR2_PACKAGE_FZF")
    assert "junegunn/fzf" in str(cfg["help"]), "Config.in help should link the upstream repo"


# --- teeth: a broken package must be flagged --------------------------------


def _write(pkg_dir: Path, config_in: str, mk: str, hashf: str) -> None:
    (pkg_dir / "package").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "package" / "Config.in").write_text(config_in, encoding="utf-8")
    (pkg_dir / "package" / "fzf.mk").write_text(mk, encoding="utf-8")
    (pkg_dir / "package" / "fzf.hash").write_text(hashf, encoding="utf-8")


_DEFAULT_MK = (
    "FZF_VERSION = 0.74.4\n"
    "FZF_SITE = $(call github,junegunn,fzf,v$(FZF_VERSION))\n"
    "FZF_GOMOD = github.com/junegunn/fzf\n"
    "FZF_LICENSE = MIT\n"
    "FZF_LICENSE_FILES = LICENSE\n\n"
    "$(eval $(golang-package))\n"
)
_DEFAULT_HASH = (
    "# Locally generated\n"
    f"sha256  {FZF_SHA256}  {FZF_GO2}\n"
    f"sha512  {FZF_SHA512}  {FZF_GO2}\n"
)


def test_flags_missing_host_go_dependency(tmp_path: Path):
    pkg = tmp_path / "fzf"
    _write(
        pkg,
        'config BR2_PACKAGE_FZF\n\tbool "fzf"\n\tselect BR2_PACKAGE_HOST_GO\n\thelp\n\t\tx\n',
        _DEFAULT_MK,
        _DEFAULT_HASH,
    )
    problems = validate_go_package(
        pkg, "BR2_PACKAGE_FZF", FZF_VERSION, "github.com/junegunn/fzf",
        "junegunn", "fzf", FZF_GO2, FZF_SHA256, FZF_SHA512,
    )
    assert any("host-Go" in p for p in problems), problems


def test_flags_wrong_symbol(tmp_path: Path):
    pkg = tmp_path / "fzf"
    _write(
        pkg,
        'config BR2_PACKAGE_FOO\n\tbool "fzf"\n\tdepends on BR2_PACKAGE_HOST_GO_TARGET_ARCH_SUPPORTS\n\tselect BR2_PACKAGE_HOST_GO\n\thelp\n\t\tx\n',
        _DEFAULT_MK,
        _DEFAULT_HASH,
    )
    problems = validate_go_package(
        pkg, "BR2_PACKAGE_FZF", FZF_VERSION, "github.com/junegunn/fzf",
        "junegunn", "fzf", FZF_GO2, FZF_SHA256, FZF_SHA512,
    )
    # A package whose symbol is not BR2_PACKAGE_FZF is rejected: the config
    # block for the expected symbol is absent.
    assert problems, problems


def test_flags_missing_golang_package(tmp_path: Path):
    pkg = tmp_path / "fzf"
    _write(
        pkg,
        'config BR2_PACKAGE_FZF\n\tbool "fzf"\n\tdepends on BR2_PACKAGE_HOST_GO_TARGET_ARCH_SUPPORTS\n\tselect BR2_PACKAGE_HOST_GO\n\thelp\n\t\tx\n',
        _DEFAULT_MK.replace("$(eval $(golang-package))", "# golang-package removed"),
        _DEFAULT_HASH,
    )
    problems = validate_go_package(
        pkg, "BR2_PACKAGE_FZF", FZF_VERSION, "github.com/junegunn/fzf",
        "junegunn", "fzf", FZF_GO2, FZF_SHA256, FZF_SHA512,
    )
    assert any("golang-package" in p for p in problems), problems


def test_flags_spaced_github_call(tmp_path: Path):
    # Buildroot expands `$(call github, owner, repo, tag)` verbatim: the spaces
    # land in the download URL and the fetch 404s. The validator must reject the
    # spaced spelling (the exact bug found wiring fzf on 2026-10-12).
    pkg = tmp_path / "fzf"
    spaced_mk = _DEFAULT_MK.replace(
        "$(call github,junegunn,fzf,", "$(call github, junegunn, fzf, "
    )
    _write(pkg, _OK_CONFIG_IN, spaced_mk, _DEFAULT_HASH)
    problems = validate_go_package(
        pkg, "BR2_PACKAGE_FZF", FZF_VERSION, "github.com/junegunn/fzf",
        "junegunn", "fzf", FZF_GO2, FZF_SHA256, FZF_SHA512,
    )
    assert any("no spaces" in p for p in problems), problems


def test_flags_wrong_hash_value(tmp_path: Path):
    pkg = tmp_path / "fzf"
    bad_hash = _DEFAULT_HASH.replace(FZF_SHA256, "0" * 64)
    _write(pkg, _OK_CONFIG_IN, _DEFAULT_MK, bad_hash)
    problems = validate_go_package(
        pkg, "BR2_PACKAGE_FZF", FZF_VERSION, "github.com/junegunn/fzf",
        "junegunn", "fzf", FZF_GO2, FZF_SHA256, FZF_SHA512,
    )
    assert any("sha256" in p for p in problems), problems


def test_flags_naming_raw_tarball_instead_of_vendored(tmp_path: Path):
    pkg = tmp_path / "fzf"
    raw = "fzf-0.74.4.tar.gz"
    hashf = (
        "# Locally generated\n"
        f"sha256  {FZF_SHA256}  {raw}\n"
        f"sha512  {FZF_SHA512}  {raw}\n"
    )
    _write(pkg, _OK_CONFIG_IN, _DEFAULT_MK, hashf)
    problems = validate_go_package(
        pkg, "BR2_PACKAGE_FZF", FZF_VERSION, "github.com/junegunn/fzf",
        "junegunn", "fzf", FZF_GO2, FZF_SHA256, FZF_SHA512,
    )
    assert any("vendored" in p for p in problems), problems


# A fully valid package body, reused by the teeth tests that only corrupt one
# field. Kept as a module constant so every test validates the same baseline.
_OK_CONFIG_IN = (
    'config BR2_PACKAGE_FZF\n'
    '\tbool "fzf"\n'
    '\tdepends on BR2_PACKAGE_HOST_GO_TARGET_ARCH_SUPPORTS\n'
    '\tselect BR2_PACKAGE_HOST_GO\n'
    '\thelp\n'
    '\t\tfzf is a command-line fuzzy finder.\n'
    '\t\thttps://github.com/junegunn/fzf\n'
)


# --- the real starship package (a Rust *cargo* package) ---------------------
#
# starship is a Rust program (Cargo.toml + Cargo.lock + build.rs, edition 2024),
# so it is a Buildroot `cargo-package`, NOT a `golang-package` like fzf. The
# digest below is of the `-cargo6` **repack** Buildroot 2026.08 downloads (the
# cargo post-process vendoring repacks the github archive; BR_FMT_VERSION_cargo
# = -cargo6), verified with `make starship-source` on 2026-10-12 — the raw
# archive digest is NOT what the hash file must carry.

STARSHIP = PACKAGE_DIR / "starship"
STARSHIP_VERSION = "1.26.0"
STARSHIP_TARBALL = "starship-1.26.0-cargo6.tar.gz"
# Computed from the Buildroot 2026.08 cargo6 repack (`make starship-source`
# vendoring of the github archive v1.26.0), verified 2026-10-12 with
# `make starship-source` (sha256sum/sha512sum of the repacked tarball).
STARSHIP_SHA256 = "c2ff3e7501b73b00f07850cef007bdb538b8a093d87a03c39589ba816c878ed6"
STARSHIP_SHA512 = (
    "2f2551aae76135d8218cb557bc613c5f4240a22db1f40497bfd59b71a0db315d"
    "7233c14f6028d1807c24819dde6df9e8084700f04cfa2013c7affb7730c5a276"
)


def test_starship_package_files_exist():
    for rel in ("Config.in", "starship.mk", "starship.hash"):
        p = STARSHIP / rel
        assert p.is_file(), f"missing Buildroot package file: {p}"
        assert p.stat().st_size > 0, f"{p} is empty"


def test_starship_package_is_wellformed():
    problems = validate_cargo_package(
        STARSHIP,
        symbol="BR2_PACKAGE_STARSHIP",
        version=STARSHIP_VERSION,
        site_owner="starship",
        site_repo="starship",
        expected_tarball=STARSHIP_TARBALL,
        expected_sha256=STARSHIP_SHA256,
        expected_sha512=STARSHIP_SHA512,
    )
    assert problems == [], f"starship package is not a well-formed Buildroot cargo package:\n  - " + "\n  - ".join(problems)


def test_starship_config_in_help_mentions_upstream():
    cfg = _parse_kconfig_block(
        (STARSHIP / "Config.in").read_text(encoding="utf-8"), "BR2_PACKAGE_STARSHIP"
    )
    assert "starship/starship" in str(cfg["help"]), "Config.in help should link the upstream repo"


# --- teeth: a broken package must be flagged --------------------------------


def _write_cargo(pkg_dir: Path, config_in: str, mk: str, hashf: str) -> None:
    (pkg_dir / "package").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "package" / "Config.in").write_text(config_in, encoding="utf-8")
    (pkg_dir / "package" / "starship.mk").write_text(mk, encoding="utf-8")
    (pkg_dir / "package" / "starship.hash").write_text(hashf, encoding="utf-8")


_DEFAULT_CARGO_MK = (
    "STARSHIP_VERSION = 1.26.0\n"
    "STARSHIP_SITE = $(call github,starship,starship,v$(STARSHIP_VERSION))\n"
    "STARSHIP_LICENSE = ISC\n"
    "STARSHIP_LICENSE_FILES = LICENSE\n\n"
    "$(eval $(cargo-package))\n"
)
_DEFAULT_CARGO_HASH = (
    "# Locally generated\n"
    f"sha256  {STARSHIP_SHA256}  {STARSHIP_TARBALL}\n"
    f"sha512  {STARSHIP_SHA512}  {STARSHIP_TARBALL}\n"
)


def test_flags_starship_missing_host_rustc_dependency(tmp_path: Path):
    pkg = tmp_path / "starship"
    _write_cargo(
        pkg,
        'config BR2_PACKAGE_STARSHIP\n\tbool "starship"\n\tselect BR2_PACKAGE_HOST_RUSTC\n\thelp\n\t\tx\n',
        _DEFAULT_CARGO_MK,
        _DEFAULT_CARGO_HASH,
    )
    problems = validate_cargo_package(
        pkg, "BR2_PACKAGE_STARSHIP", STARSHIP_VERSION,
        "starship", "starship", STARSHIP_TARBALL, STARSHIP_SHA256, STARSHIP_SHA512,
    )
    assert any("host-Rust" in p for p in problems), problems


def test_flags_starship_wrong_symbol(tmp_path: Path):
    pkg = tmp_path / "starship"
    _write_cargo(
        pkg,
        'config BR2_PACKAGE_FOO\n\tbool "starship"\n\tdepends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS\n\tselect BR2_PACKAGE_HOST_RUSTC\n\thelp\n\t\tx\n',
        _DEFAULT_CARGO_MK,
        _DEFAULT_CARGO_HASH,
    )
    problems = validate_cargo_package(
        pkg, "BR2_PACKAGE_STARSHIP", STARSHIP_VERSION,
        "starship", "starship", STARSHIP_TARBALL, STARSHIP_SHA256, STARSHIP_SHA512,
    )
    assert problems, problems


def test_flags_starship_missing_cargo_package(tmp_path: Path):
    pkg = tmp_path / "starship"
    _write_cargo(
        pkg,
        'config BR2_PACKAGE_STARSHIP\n\tbool "starship"\n\tdepends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS\n\tselect BR2_PACKAGE_HOST_RUSTC\n\thelp\n\t\tx\n',
        _DEFAULT_CARGO_MK.replace("$(eval $(cargo-package))", "# cargo-package removed"),
        _DEFAULT_CARGO_HASH,
    )
    problems = validate_cargo_package(
        pkg, "BR2_PACKAGE_STARSHIP", STARSHIP_VERSION,
        "starship", "starship", STARSHIP_TARBALL, STARSHIP_SHA256, STARSHIP_SHA512,
    )
    assert any("cargo-package" in p for p in problems), problems


def test_flags_starship_wrong_hash_value(tmp_path: Path):
    pkg = tmp_path / "starship"
    bad_hash = _DEFAULT_CARGO_HASH.replace(STARSHIP_SHA256, "0" * 64)
    _write_cargo(
        pkg,
        'config BR2_PACKAGE_STARSHIP\n\tbool "starship"\n\tdepends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS\n\tselect BR2_PACKAGE_HOST_RUSTC\n\thelp\n\t\tx\n',
        _DEFAULT_CARGO_MK,
        bad_hash,
    )
    problems = validate_cargo_package(
        pkg, "BR2_PACKAGE_STARSHIP", STARSHIP_VERSION,
        "starship", "starship", STARSHIP_TARBALL, STARSHIP_SHA256, STARSHIP_SHA512,
    )
    assert any("sha256" in p for p in problems), problems


def test_flags_starship_wrong_tarball_name(tmp_path: Path):
    pkg = tmp_path / "starship"
    wrong = "starship-9.9.9.tar.gz"
    hashf = (
        "# Locally generated\n"
        f"sha256  {STARSHIP_SHA256}  {wrong}\n"
        f"sha512  {STARSHIP_SHA512}  {wrong}\n"
    )
    _write_cargo(
        pkg,
        'config BR2_PACKAGE_STARSHIP\n\tbool "starship"\n\tdepends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS\n\tselect BR2_PACKAGE_HOST_RUSTC\n\thelp\n\t\tx\n',
        _DEFAULT_CARGO_MK,
        hashf,
    )
    problems = validate_cargo_package(
        pkg, "BR2_PACKAGE_STARSHIP", STARSHIP_VERSION,
        "starship", "starship", STARSHIP_TARBALL, STARSHIP_SHA256, STARSHIP_SHA512,
    )
    assert any("tarball" in p for p in problems), problems


def test_flags_starship_missing_license(tmp_path: Path):
    pkg = tmp_path / "starship"
    no_license = _DEFAULT_CARGO_MK.replace("STARSHIP_LICENSE = ISC\n", "")
    _write_cargo(
        pkg,
        'config BR2_PACKAGE_STARSHIP\n\tbool "starship"\n\tdepends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS\n\tselect BR2_PACKAGE_HOST_RUSTC\n\thelp\n\t\tx\n',
        no_license,
        _DEFAULT_CARGO_HASH,
    )
    problems = validate_cargo_package(
        pkg, "BR2_PACKAGE_STARSHIP", STARSHIP_VERSION,
        "starship", "starship", STARSHIP_TARBALL, STARSHIP_SHA256, STARSHIP_SHA512,
    )
    assert any("license" in p for p in problems), problems


# A fully valid package body, reused by the teeth tests that only corrupt one
# field. Kept as a module constant so every test validates the same baseline.
_OK_CARGO_CONFIG_IN = (
    'config BR2_PACKAGE_STARSHIP\n'
    '\tbool "starship"\n'
    '\tdepends on BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS\n'
    '\tselect BR2_PACKAGE_HOST_RUSTC\n'
    '\thelp\n'
    '\t\tstarship is a shell prompt, made fast.\n'
    '\t\thttps://github.com/starship/starship\n'
)
