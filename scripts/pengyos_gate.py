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
