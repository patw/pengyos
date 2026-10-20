#!/usr/bin/env python3
"""pengyos-gate — the repo-level integration gate for PengyOS.

WHY THIS EXISTS
---------------
A tool's `verify.toml` runs the tool's OWN unit suite. That suite is
authored alongside the code, so it is *self-consistent*: it
passes while the integration is broken. Three 2026-09-27 examples, all green
unit suites, all broken in the tree:

  * pengyos-desktop-nvidia  wired modprobe.d/udev/bin at the BR2_EXTERNAL ROOT
    (not board/.../rootfs-overlay) and built its variant defconfig off the
    SERVER base — its 154 tests encoded the wrong layout;
  * pengyos-desktop-av      shipped a rootfs-overlay /etc/group that REPLACED
    the skeleton's and wiped `root:` -> target-finalize died;
  * pengyos-server-clis-wire authored recipes that were never build-verified
    (btop/neovim cmake in-source; fzf a malformed source URL).

This gate is the EXTERNAL check that catches that class. It is not owned by any
tool, and it lives in the repo (scripts/) so no single tool's change can
weaken it. It never builds a full image (that is operator/`PENGYOS_IT` work); it checks the cheap, high-signal invariants.

SCOPING (why the whole repo isn't re-checked on every change)
-----------------------------------------------------------
Checks are scoped to what the branch CHANGED (`--base`, default `main`):
  * a branch that touched `tools/<id>/` runs that tool's lint/suite only;
  * a branch that touched `buildroot-external/` runs ALL wiring lints + the
    changed overlays (a shared-tree change can break a sibling);
  * a branch that added/changed a recipe runs that recipe's source check.
Unscoped (no base) runs everything — that is the merge-time invocation.

MODES
-----
  fast   cheap; run on every change: wiring lints + shared-file guards.
  full   fast + every tool's verify.toml checks + authored-recipe source resolution.

Exit 0 iff every required check passes. `--json`/`--junit` write machine reports.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Optics: the repo-level invariants that the unit suites cannot see.
# --------------------------------------------------------------------------

# Wiring tools: emit into <repo>/buildroot-external and ship a `lint` lane that
# asserts the tree actually carries their output. (name, module).
WIRING_TOOLS = [
    ("pengyos-desktop-nvidia", "pengyos_desktop_nvidia"),
]

# A rootfs-overlay /etc/group *replaces* Buildroot's skeleton copy, so it must be
# cumulative — these entries must survive.
GROUP_OVERLAY_MUST_CONTAIN = ("root:",)

RECIPE_SOURCE_PACKAGE_DIR = "buildroot-external/package"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    repo: str
    mode: str
    base: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def _run(argv, cwd: Path, timeout: int = 900) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s"
    except FileNotFoundError as e:
        return 127, str(e)


def _tail(s: str, n: int = 400) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else "…" + s[-n:]


def _git(repo: Path, *args: str) -> str:
    rc, out = _run(["git", "-C", str(repo), *args], cwd=repo)
    return out if rc == 0 else ""


def changed_paths(repo: Path, base: str | None) -> set[str] | None:
    """Set of repo-relative paths changed from *base* to HEAD, or None (unscoped)."""
    if not base:
        return None
    if not _git(repo, "rev-parse", "--verify", base):
        return None
    out = _git(repo, "diff", "--name-only", f"{base}...HEAD")
    if not out:
        out = _git(repo, "diff", "--name-only", base)
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def _touches(changed: set[str] | None, prefix: str) -> bool:
    if changed is None:
        return True
    return any(p == prefix or p.startswith(prefix.rstrip("/") + "/") for p in changed)


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def check_wiring_lints(repo: Path, changed: set[str] | None) -> list[Check]:
    """Wiring tools' `lint` must pass against the tree they wire into.

    Scoped to the tool's OWN directory: a task is responsible for its own wiring,
    not for a sibling tool that happens to be unwired (otherwise one broken tool,
    like pengyos-desktop-nvidia while its fix is pending, would block every other
    branch). Unscoped (merge-time) runs them all so the operator sees every gap.
    """
    out: list[Check] = []
    for tool, module in WIRING_TOOLS:
        tdir = repo / "tools" / tool
        if not (tdir / "pyproject.toml").is_file():
            continue
        if changed is not None and not _touches(changed, f"tools/{tool}"):
            continue
        rc, log = _run([sys.executable, "-m", module, "lint", "--repo", str(repo / "buildroot-external")],
                       cwd=tdir)
        out.append(Check(f"wiring-lint:{tool}", rc == 0, _tail(log)))
    if not out:
        out.append(Check("wiring-lint", True, "nothing in scope"))
    return out


def check_group_overlays(repo: Path) -> list[Check]:
    """A rootfs-overlay /etc/group must be cumulative (keep the skeleton's root:)."""
    out: list[Check] = []
    found = False
    for gpath in sorted((repo / "buildroot-external" / "board").glob("*/rootfs-overlay/etc/group")):
        found = True
        text = gpath.read_text(encoding="utf-8", errors="replace")
        missing = [g for g in GROUP_OVERLAY_MUST_CONTAIN
                   if not any(ln.startswith(g) for ln in text.splitlines())]
        out.append(Check(f"group-overlay:{gpath.relative_to(repo)}", not missing,
                         "ok" if not missing else f"missing {missing} (would clobber the skeleton)"))
    if not found:
        out.append(Check("group-overlay", True, "no overlay /etc/group present"))
    return out


def _image_size(path: Path) -> tuple[int, int] | None:
    """(width, height) read from a PNG or JPEG header, stdlib only."""
    import struct
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            # SOF0..SOF15, skipping the ones that carry no frame dimensions.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg_len
    return None


def check_patch_declarations(repo: Path) -> list[Check]:
    """A carried patch must be declared to the tooling that reads the tree.

    `pkg-stats` cannot see a patch delivered through BR2_GLOBAL_PATCH_DIR:
    `set_patch_count()` walks only the package's own directory. Without
    `<PKG>_IGNORE_CVES` the package keeps reporting "affected by CVEs" for a CVE we
    shipped a fix for, so the divergence ledger says closed while the tree says open.
    Found 2026-10-07: the libxml2 patch set (7 patches, published in 26.10.8) had no
    declaration at all, even though `buildroot-external/patches/README.md` rule 3 and
    the ledger entry itself both name this as the fix.

    The ledger (`divergence.toml`) is the source of truth for which CVEs a patch set
    closes; the check is that the tree says exactly that, no more and no less. An
    ignore that outlives its patch would hide a real exposure, so an extra declaration
    fails too.
    """
    try:
        ledger = tomllib.loads((repo / "divergence.toml").read_text())
    except (OSError, ValueError) as exc:
        return [Check("patch-declaration", False, f"divergence.toml unreadable: {exc}")]
    entries = [d for d in ledger.get("divergence", []) if d.get("patches_dir")]
    if not entries:
        return [Check("patch-declaration", True, "no patch set in the ledger")]

    text = ""
    for mk in [repo / "buildroot-external/external.mk",
               repo / "buildroot-external/patches/cve-declarations.mk",
               *sorted((repo / "buildroot-external" / "package").glob("*/*.mk"))]:
        try:
            text += mk.read_text()
        except OSError:
            continue

    out: list[Check] = []
    for d in entries:
        name = d["id"]
        pdir = repo / d["patches_dir"]
        if not pdir.is_dir():
            out.append(Check(f"patch-declaration:{name}", False,
                             f"{d['patches_dir']} is in the ledger but does not exist"))
            continue
        carried = sorted(p.name for p in pdir.rglob("*.patch"))
        prefix = d["component"].upper().replace("-", "_")
        declared: set[str] = set()
        for line in text.splitlines():
            m = re.match(rf"^\s*{prefix}_IGNORE_CVES\s*\+?=\s*(.*)$", line)
            if m:
                declared |= {c for c in m.group(1).split() if c.startswith("CVE-")}
        want = set(d.get("closes") or [])
        missing, extra = sorted(want - declared), sorted(declared - want)
        if not carried:
            out.append(Check(f"patch-declaration:{name}", False,
                             f"{d['patches_dir']} holds no .patch file"))
        elif missing:
            out.append(Check(f"patch-declaration:{name}", False,
                             f"{len(carried)} patch(es) carried but pkg-stats is not told: "
                             f"{prefix}_IGNORE_CVES is missing {', '.join(missing)}"))
        elif extra:
            out.append(Check(f"patch-declaration:{name}", False,
                             f"{prefix}_IGNORE_CVES declares {', '.join(extra)}, which "
                             f"{name} does not close — an ignore outliving its patch"))
        else:
            out.append(Check(f"patch-declaration:{name}", True,
                             f"{len(carried)} patch(es), {len(want)} CVE(s) declared to "
                             f"pkg-stats via {prefix}_IGNORE_CVES"))
    return out


LEDGER_KINDS = ("patch-set", "fork", "host-built")
LEDGER_REQUIRED = ("id", "kind", "component", "carried_since", "expiry",
                   "observe_source", "upstream_status")


def check_ledger_covers_patch_sets(repo: Path) -> list[Check]:
    """Every patch set in the tree must be a ledger entry, and every entry whole.

    `check_patch_declarations` reads the ledger and asks whether the tree agrees. Nothing
    asked that question backwards, so a patch set could appear in the tree with no entry
    and no consequence: found 2026-10-18, five patch sets on disk and two entries in the
    ledger (ffmpeg, gst1-plugins-bad and gst1-plugins-good were landed by pengyos-nightly
    over three nights and never written down).

    The entry is not paperwork. It is the only place a divergence carries an `expiry` -
    the specific upstream event that ends it - and the only place it names an
    `observe_source`. A patch set with neither rots silently: the PengyR case, where
    images shipped 1.10.0 while upstream was at 1.11.1 and nothing noticed.

    Both directions are needed for the rule in divergence.toml's header ("every
    divergence gets an entry here BEFORE it lands") to mean anything. An entry missing a
    required field fails too: an entry with no expiry is not an approved divergence,
    whatever its presence suggests.
    """
    try:
        ledger = tomllib.loads((repo / "divergence.toml").read_text())
    except (OSError, ValueError) as exc:
        return [Check("patch-set-ledger", False, f"divergence.toml unreadable: {exc}")]
    entries = ledger.get("divergence", [])

    claimed: dict[str, set[str]] = {}
    for e in entries:
        pdir = str(e.get("patches_dir") or "").strip().strip("/")
        if pdir:
            claimed.setdefault(pdir, set()).update(e.get("closes") or [])

    root = repo / "buildroot-external" / "patches"
    sets: dict[str, list[str]] = {}
    for p in sorted(root.rglob("*.patch")) if root.is_dir() else []:
        sets.setdefault(p.parent.relative_to(repo).as_posix(), []).append(p.name)

    out: list[Check] = []
    for d, patches in sorted(sets.items()):
        if d in claimed:
            out.append(Check(f"patch-set-ledgered:{d}", True,
                             f"{len(patches)} patch(es), {len(claimed[d])} CVE(s) in the ledger"))
            continue
        out.append(Check(
            f"patch-set-ledgered:{d}", False,
            f"{len(patches)} patch(es) carried, no divergence.toml entry: nothing states "
            f"when this divergence ends or who observes it. Add an entry with id, "
            f"kind = \"patch-set\", component, carried_since, expiry, observe_source, "
            f"upstream_status and patches_dir = \"{d}\""))
    if not sets:
        out.append(Check("patch-set-ledgered", True, "no patch set in the tree"))

    for i, e in enumerate(entries):
        name = e.get("id") or f"#{i}"
        missing = [k for k in LEDGER_REQUIRED if not str(e.get(k) or "").strip()]
        if missing:
            out.append(Check(f"ledger-entry:{name}", False,
                             f"missing {', '.join(missing)}: an entry without an expiry is "
                             "not an approved divergence"))
        elif e.get("kind") not in LEDGER_KINDS:
            out.append(Check(f"ledger-entry:{name}", False,
                             f"kind {e.get('kind')!r} is not one of "
                             f"{', '.join(LEDGER_KINDS)}"))
        else:
            out.append(Check(f"ledger-entry:{name}", True, f"{e['kind']}, complete"))
    return out


#: The fork budget. Read from `fork_budget` in divergence.toml; this is the fallback
#: when the ledger states the budget only in prose.
DEFAULT_FORK_BUDGET = 10


def check_fork_budget(repo: Path) -> list[Check]:
    """Count the maintained forks, and make sure each one can be seen going stale.

    Two things the ledger promises and nothing enforced, both found 2026-10-19:

    1. **The budget.** `divergence.toml` opens with "Budget: at most 10 maintained forks"
       and, honestly, with the note that the gate has no fork-count check. A budget that
       is counted by hand is a budget that drifts: forks are landed on a security clock
       (openssh 2026-10-19, ffmpeg and hplip queued behind them) and nothing in the path
       makes the number visible at the moment it grows.

    2. **A fork's observability.** A fork is invisible to `pkg-stats`: it scans the stock
       recipe, and the stock recipe is switched off. The ledger requires every entry to
       name an `observe_source` for exactly this reason (the PengyR case — images shipped
       1.10.0 while upstream was at 1.11.1 and nothing noticed), and `check_ledger_covers_
       patch_sets` only checks that the field is *non-empty*. A fork that names
       `observe_source = "pkg-stats"` satisfies that and is still blind: the pin rots and
       the scanner reports a clean tree forever. OpenSSH nearly shipped exactly that, with
       an exposed sshd attached — the fork first got CPE product `openssh-pengyos`, which
       matches nothing in NVD, so pkg-stats reported zero CVEs for a package carrying
       OpenSSH.

    So a fork must name a source that is not pkg-stats, and must name the recipe it
    ships: the recipe is what the nightly compares upstream tags against, and a fork
    whose recipe path does not exist has no pin to go stale.
    """
    try:
        ledger = tomllib.loads((repo / "divergence.toml").read_text())
    except (OSError, ValueError) as exc:
        return [Check("fork-budget", False, f"divergence.toml unreadable: {exc}")]

    entries = ledger.get("divergence", [])
    forks = [e for e in entries if e.get("kind") == "fork"]
    budget = int(ledger.get("fork_budget") or DEFAULT_FORK_BUDGET)

    out: list[Check] = []
    if len(forks) > budget:
        out.append(Check(
            "fork-budget", False,
            f"{len(forks)} maintained forks, budget is {budget}: {', '.join(f.get('id', '?') for f in forks)}. "
            "Pay one down (the expiry names the upstream event that ends it) before landing "
            "another, or raise `fork_budget` in divergence.toml as a written decision."))
    else:
        out.append(Check("fork-budget", True,
                         f"{len(forks)} of {budget} forks: {', '.join(f.get('id', '?') for f in forks) or 'none'}"))

    for e in forks:
        name = e.get("id") or "?"
        observe = str(e.get("observe_source") or "").strip()
        recipe = str(e.get("recipe") or "").strip().strip("/")
        if not recipe:
            out.append(Check(f"fork-observable:{name}", False,
                             "a fork entry must name `recipe` — the nightly compares upstream "
                             "tags against the version in that recipe, and with no recipe there "
                             "is no pin that can be seen going stale"))
        elif not (repo / recipe).is_dir():
            out.append(Check(f"fork-observable:{name}", False,
                             f"`recipe = {recipe}` does not exist in the tree: the fork ships "
                             "nothing, so its observe source compares a version that is not built"))
        elif observe.lower().replace("-", "_") == "pkg_stats" or observe == "pkg-stats":
            out.append(Check(
                f"fork-observable:{name}", False,
                f"observe_source is pkg-stats, which cannot see a fork: it scans the stock "
                f"recipe and the stock recipe is switched off. The pin rots silently. Name the "
                f"pengyos-nightly observe source that watches {recipe}'s version."))
        elif not observe:
            out.append(Check(f"fork-observable:{name}", False,
                             "no observe_source: a fork invisible to pkg-stats with no source of "
                             "its own has nothing to tell it the pin went stale"))
        else:
            out.append(Check(f"fork-observable:{name}", True,
                             f"{recipe}, observed by pengyos-nightly (pkg-stats cannot see a fork)"))
    return out


def check_background_sources(repo: Path) -> list[Check]:
    """Every COSMIC background record must resolve inside its own overlay.

    `com.system76.CosmicBackground/v1/<output>` is a single Entry naming the
    ACTIVE wallpaper, so a stale `source: Path(...)` — after a rename or a
    format change — silently yields a grey desktop rather than a build error.
    Also: a file named `*-16x9` must actually be 16:9.
    """
    out: list[Check] = []
    found = False
    for cfg in sorted((repo / "buildroot-external" / "board").glob(
            "*/rootfs-overlay/usr/share/cosmic/com.system76.CosmicBackground/v1/*")):
        if cfg.is_dir():
            continue
        found = True
        overlay = next(p for p in cfg.parents if p.name == "rootfs-overlay")
        text = cfg.read_text(encoding="utf-8", errors="replace")
        refs = re.findall(r'Path\("([^"]+)"\)', text)
        if not refs:
            out.append(Check(f"backgrounds:{cfg.relative_to(repo)}", False,
                             "no source: Path(...) in the background record"))
            continue
        missing = [r for r in refs if not (overlay / r.lstrip("/")).is_file()]
        out.append(Check(f"backgrounds:{cfg.relative_to(repo)}", not missing,
                         "ok" if not missing
                         else f"source not in overlay: {missing} (desktop would be blank)"))

    for board in sorted((repo / "buildroot-external" / "board").glob("*/rootfs-overlay")):
        for img in sorted(board.glob("usr/share/backgrounds/**/*-16x9.*")):
            found = True
            size = _image_size(img)
            if size is None:
                out.append(Check(f"backgrounds-aspect:{img.relative_to(repo)}", False,
                                 "unparsable image header"))
                continue
            w, h = size
            ok = abs(w / h - 16 / 9) < 1e-6
            out.append(Check(f"backgrounds-aspect:{img.relative_to(repo)}", ok,
                             f"{w}x{h}" if ok else f"{w}x{h} is not 16:9 ({w/h:.4f})"))
    if not found:
        out.append(Check("backgrounds", True, "no COSMIC background records present"))
    return out


def check_toolkit_suite(repo: Path, changed: set[str] | None) -> list[Check]:
    """Each changed tool's own suite (or every tool's, unscoped).

    A tool declares how it is tested in ``verify.toml`` (``[[check]]`` entries
    with ``run``, ``cwd`` relative to the repo, ``expect = "exit0"`` and
    ``timeout``), which covers the Rust tools as well as the Python ones. A
    Python tool without one falls back to plain pytest.
    """
    out: list[Check] = []
    for tdir in sorted(d for d in (repo / "tools").iterdir() if d.is_dir()):
        if changed is not None and not _touches(changed, f"tools/{tdir.name}"):
            continue
        vt = tdir / "verify.toml"
        if vt.is_file():
            with vt.open("rb") as fh:
                checks = tomllib.load(fh).get("check", [])
            for c in checks:
                argv = [sys.executable if a == "python3" else a for a in c["run"]]
                if argv[:3] == [sys.executable, "-m", "pytest"]:
                    argv[3:3] = ["-o", "addopts="]
                rc, log = _run(argv, cwd=repo / c.get("cwd", f"tools/{tdir.name}"),
                               timeout=int(c.get("timeout", 900)))
                out.append(Check(f"toolkit:{tdir.name}:{c.get('name', 'check')}",
                                 rc == 0, _tail(log, 300)))
        elif (tdir / "pyproject.toml").is_file():
            rc, log = _run([sys.executable, "-m", "pytest", "-o", "addopts=", "-q"], cwd=tdir)
            out.append(Check(f"toolkit:{tdir.name}", rc == 0, _tail(log, 300)))
    if not out:
        out.append(Check("toolkit-suite", True, "nothing in scope"))
    return out


def check_scripts_suite(repo: Path) -> list[Check]:
    """Tests for the repo's own build scripts (`scripts/`).

    `scripts/` is load-bearing: build-cosmic.sh, compose-defconfigs.py and
    release.sh decide what an image contains, so a bug in them is a bug in every
    image. They are not tools, so `check_toolkit_suite` (which walks `tools/`)
    never reaches them. If `scripts/tests/` exists it is run; if it does not,
    nothing is claimed.
    """
    tests = repo / "scripts" / "tests"
    if not tests.is_dir():
        return []
    rc, log = _run([sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "tests"],
                   cwd=repo / "scripts", timeout=600)
    return [Check("scripts-suite", rc == 0, _tail(log, 400))]


def check_recipe_sources(repo: Path, changed: set[str] | None,
                         buildroot: Path | None, buildroot_out: Path | None) -> list[Check]:
    """Authored recipes' SOURCE must resolve (download + verify hash). Best-effort."""
    if not (buildroot and buildroot.is_dir() and buildroot_out and buildroot_out.is_dir()):
        return [Check("recipe-source", True, "skipped (no configured buildroot tree)")]
    out: list[Check] = []
    for mk in sorted((repo / RECIPE_SOURCE_PACKAGE_DIR).glob("*/*.mk")):
        name = mk.parent.name
        if changed is not None and not _touches(changed, f"{RECIPE_SOURCE_PACKAGE_DIR}/{name}"):
            continue
        rc, log = _run(["make", "-C", str(buildroot), f"O={buildroot_out}",
                        f"BR2_EXTERNAL={repo / 'buildroot-external'}", f"{name}-source"],
                       cwd=repo, timeout=600)
        out.append(Check(f"recipe-source:{name}", rc == 0, _tail(log, 300)))
    if not out:
        out.append(Check("recipe-source", True, "nothing in scope"))
    return out


# --------------------------------------------------------------------------
# Reporters
# --------------------------------------------------------------------------

def write_json(rep: Report, path: Path) -> None:
    path.write_text(json.dumps(
        {"repo": rep.repo, "mode": rep.mode, "base": rep.base, "ok": rep.ok,
         "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in rep.checks]},
        indent=2) + "\n")


def write_junit(rep: Report, path: Path) -> None:
    import xml.sax.saxutils as sx
    cases = "".join(
        f'<testcase classname="pengyos-gate" name="{sx.escape(c.name)}">'
        + ("" if c.ok else f'<failure message="{sx.escape(c.detail[:200])}"/>') + "</testcase>"
        for c in rep.checks)
    failures = sum(1 for c in rep.checks if not c.ok)
    path.write_text('<?xml version="1.0" encoding="utf-8"?>\n'
                    f'<testsuite name="pengyos-gate" tests="{len(rep.checks)}" '
                    f'failures="{failures}">{cases}</testsuite>\n')


_EXEC_LINE = re.compile(
    r"\s*Exec(?:StartPre|StartPost|Start|Reload|StopPost|Stop|Condition)\s*=\s*(.*)$")
_EXEC_PREFIX = re.compile(r"^[-+!@]*")


def _overlay_index(repo: Path) -> dict[str, Path]:
    """target-absolute-path -> repo path, for every file in a rootfs-overlay."""
    idx: dict[str, Path] = {}
    for ov in sorted((repo / "buildroot-external").glob("**/rootfs-overlay")):
        if not ov.is_dir():
            continue
        for f in ov.rglob("*"):
            if f.is_file() and not f.is_symlink():
                idx["/" + f.relative_to(ov).as_posix()] = f
    return idx


def check_overlay_executables(repo: Path) -> list[Check]:
    """A unit ExecStart that resolves to an overlay file must be executable.

    Buildroot copies BR2_ROOTFS_OVERLAY with rsync -a, so the repo's mode IS the
    image's mode. A script that loses its +x (an editor rewriting it 0664, say)
    then fails at boot with 203/EXEC. Found live: alsa-unity shipped 0644 in
    26.10.10 and pengyos-alsa-unity.service failed on behoweather - a green build
    and a green gate, because nothing checked the mode.
    """
    idx = _overlay_index(repo)
    units = list((repo / "buildroot-external").glob("**/rootfs-overlay/**/*.service"))
    units += list((repo / "buildroot-external").glob("**/rootfs-overlay/**/*.service.d/*.conf"))
    out: list[Check] = []
    for unit in sorted(units):
        if not unit.is_file():
            continue
        for ln in unit.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _EXEC_LINE.match(ln)
            if not m:
                continue
            cmd = m.group(1).strip()
            if not cmd or cmd.startswith(":"):
                continue
            target = _EXEC_PREFIX.sub("", cmd.split()[0])
            if not target.startswith("/"):
                continue
            f = idx.get(target)
            if f is None:
                continue
            ok = os.access(f, os.X_OK)
            out.append(Check(f"overlay-exec:{target}", ok,
                             "ok" if ok else
                             f"{f.relative_to(repo)} is not executable (would fail 203/EXEC)"))
    if not out:
        out.append(Check("overlay-exec", True, "no overlay binaries referenced by units"))
    return out


def _git_index_modes(repo: Path, paths: list[str]) -> dict[str, str]:
    """File modes from the git index — what a fresh clone actually gets."""
    if not paths:
        return {}
    try:
        p = subprocess.run(["git", "-C", str(repo), "ls-files", "-s", "--", *paths],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    out: dict[str, str] = {}
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            out[parts[3]] = parts[0]
    return out


def _post_scripts(repo: Path) -> list[str]:
    """Repo-relative paths of every script Buildroot *executes* by path: the
    BR2_ROOTFS_POST_BUILD_SCRIPT / BR2_ROOTFS_POST_IMAGE_SCRIPT lists in each
    defconfig. Sourced files (`. image-ids.sh`, /etc/profile.d/*) are not here —
    they are read, not run, and 0644 is right for them."""
    scripts: list[str] = []
    for cfg in sorted((repo / "buildroot-external/configs").glob("*_defconfig")):
        for ln in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if ln.startswith("#") or "=" not in ln:
                continue
            name, _, value = ln.partition("=")
            if name not in ("BR2_ROOTFS_POST_BUILD_SCRIPT", "BR2_ROOTFS_POST_IMAGE_SCRIPT"):
                continue
            value = value.strip().strip('"')
            for part in value.split():
                part = part.replace("$(BR2_EXTERNAL_PENGYOS_PATH)", "buildroot-external")
                if part.startswith("buildroot-external/"):
                    scripts.append(part)
    return sorted(set(scripts))


def check_board_script_executables(repo: Path) -> list[Check]:
    """Every post-build / post-image script must be executable *in git*.

    Buildroot runs each one as `<path>` from target-finalize, so the index mode is
    the failure: measured 2026-10-08, a commit that rewrote
    `board/desktop/post-build.sh` carried it 100755 -> 100644 and the build host
    died with `Permission denied` at target-finalize. The mode in the index is what
    every build host gets; a local `chmod +x` on one host hides it until the next
    clean clone. This is the third time this file has lost its +x (2026-09-25 the
    same way), which is why it is a gate check and not a habit.
    """
    scripts = _post_scripts(repo)
    if not scripts:
        return [Check("board-script-exec", True, "no post-build/post-image scripts declared")]
    modes = _git_index_modes(repo, scripts)
    out: list[Check] = []
    for rel in scripts:
        mode = modes.get(rel)
        if mode is None:
            out.append(Check(f"board-script-exec:{rel}", False, "not in the git index"))
            continue
        out.append(Check(f"board-script-exec:{rel}", mode == "100755",
                         "ok" if mode == "100755"
                         else f"git mode {mode}; Buildroot executes it and gets Permission denied"))
    return out


def check_script_executables(repo: Path) -> list[Check]:
    """A shell script under `scripts/` with a shebang must be executable *in git*.

    `scripts/release.sh` calls `scripts/build-cosmic.sh` directly, so the file mode in
    the index is the failure: measured 2026-10-08, `build-cosmic.sh` sat at 100644 and
    `release.sh` died with `Permission denied` at the COSMIC step. It stayed hidden
    because the nightly act lane always passes `--skip-cosmic`, so only a human running
    `release.sh` without it ever reached that line. The same defect in `release.sh`
    itself (commit 2d894fba) cost a night in pengyos-nightly 0.11.1.

    The mode that matters is the index's, not this disk's: a fresh clone is what every
    other machine and every build host gets. Falls back to the file mode when there is
    no git to ask.
    """
    scripts = sorted(p for p in (repo / "scripts").glob("*.sh") if p.is_file())
    if not scripts:
        return [Check("script-exec", True, "no scripts/*.sh to check")]
    rel = [str(p.relative_to(repo)) for p in scripts]
    modes = _git_index_modes(repo, rel)
    out: list[Check] = []
    for p, r in zip(scripts, rel):
        try:
            has_shebang = p.open(encoding="utf-8", errors="replace").readline().startswith("#!")
        except OSError:
            continue
        if not has_shebang:
            continue
        mode = modes.get(r)
        if mode is None:                      # not in the index: check what is on disk
            ok = os.access(p, os.X_OK)
            why = "not in the git index, checked the file mode"
        else:
            ok = mode == "100755"
            why = f"git mode {mode}"
        out.append(Check(f"script-exec:{r}", ok, "ok" if ok else
                         f"{r} has a shebang but {why}: a direct call from another "
                         "script gets Permission denied (chmod +x and commit the mode)"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PengyOS repo-level integration gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--repo", default=".")
    r.add_argument("--mode", choices=("fast", "full"), default="fast")
    r.add_argument("--base", default="main", help="scope to changes since this ref ('none' = unscoped)")
    r.add_argument("--buildroot", default=os.path.expanduser("~/build/pengyos/buildroot-2026.08"))
    r.add_argument("--buildroot-out", default=os.path.expanduser("~/build/pengyos/output/pengyos-server"))
    r.add_argument("--json", dest="json_out")
    r.add_argument("--junit")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    base = None if args.base in ("", "none") else args.base
    changed = changed_paths(repo, base)
    rep = Report(repo=str(repo), mode=args.mode, base=base or "(unscoped)")

    rep.checks += check_wiring_lints(repo, changed)
    rep.checks += check_group_overlays(repo)
    rep.checks += check_patch_declarations(repo)
    rep.checks += check_ledger_covers_patch_sets(repo)
    rep.checks += check_fork_budget(repo)
    rep.checks += check_overlay_executables(repo)
    rep.checks += check_script_executables(repo)
    rep.checks += check_board_script_executables(repo)
    rep.checks += check_background_sources(repo)
    if args.mode == "full":
        rep.checks += check_toolkit_suite(repo, changed)
        rep.checks += check_scripts_suite(repo)
        rep.checks += check_recipe_sources(
            repo, changed,
            Path(os.path.expanduser(args.buildroot)), Path(os.path.expanduser(args.buildroot_out)))

    if args.json_out:
        write_json(rep, Path(args.json_out))
    if args.junit:
        write_junit(rep, Path(args.junit))

    fails = [c for c in rep.checks if not c.ok]
    scope = "unscoped" if changed is None else f"changed vs {base} ({len(changed)} paths)"
    print(f"pengyos-gate [{rep.mode}] {repo} · {scope}: "
          f"{len(rep.checks) - len(fails)}/{len(rep.checks)} checks passed")
    for c in rep.checks:
        line = f"  [{'ok  ' if c.ok else 'FAIL'}] {c.name}"
        if not c.ok:
            last = c.detail.splitlines()[-1][:160] if c.detail else ""
            line += f" — {last}"
        print(line)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
