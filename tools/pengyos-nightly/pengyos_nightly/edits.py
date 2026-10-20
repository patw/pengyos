"""The edit shapes the act lane may make, and the hashes they need.

Buildroot 2026.08 will not let a br2-external tree change a stock package, and
that was proven the hard way on 2026-10-05, not read off the docs:

* copying a stock recipe into `buildroot-external/package/` is refused outright
  (`package/pkg-generic.mk`: "Package 'X' defined a second time in '<pkgdir>'");
* reassigning its version in `external.mk` half-works and then breaks: Buildroot
  *downloads* the new version, but the pattern rule `$(BUILD_DIR)/%/.stamp_extracted`
  (`package/pkg-generic.mk:206`) fixed the stamp path and `$(@D)` when the stock
  recipe was parsed, and `include package/Makefile.in` (Makefile:526) runs before
  `include $(BR2_EXTERNAL_FILE)` (Makefile:559). The recipe *body* expands later and
  sees the new version. Result, in a real tree:

      libxml2-2.15.4.tar.xz: OK (sha256: 98087fd181d9...)
      >>> libxml2 2.15.4 Extracting
      tar: .../build/libxml2-2.15.4: Cannot open: No such file or directory
      make[1]: *** [.../build/libxml2-2.15.3/.stamp_extracted] Error 2

  `libxml2-dirclean` + `make libxml2` fails identically. There is no version bump
  for a stock package from here.

So two shapes:

* `recipe_bump` — a package we already author: change VERSION in its own .mk and
  the tarball hash in its own .hash.
* `patch_dir` — a stock package: carry the upstream fix commits as
  `$(BR2_GLOBAL_PATCH_DIR)/<pkg>/<version>/*.patch`. Proven in the same tree:
  `Applying 0001-xmlregexp-...patch using patch: patching file xmlregexp.c`, then a
  clean configure/build/install. The version is untouched, so the recipe's own hash
  file stays valid.

A version bump needs a hash or `support/download/check-hash` exits 3 ("hash file
exists and there was no hash to check the file against"), which dl-wrapper treats
as fatal. Hashes come from upstream's own published checksum file when the stock
.hash names one (GNOME publishes `<pkg>-<ver>.sha256sum`, and the existing entry
says so in a comment); otherwise the artifact is downloaded and hashed here, which
the plan labels `self-attested` rather than pretending otherwise.
"""
from __future__ import annotations

import hashlib
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from pengyos_nightly import __version__

Get = "callable"  # documented: every fetch here takes an injectable get(url)->bytes

# The version we present to upstream is the version we are running, not the one that
# happened to be current when this line was first written (it read 0.4 for six releases).
UA = f"pengyos-nightly/{__version__} (+https://pengyos.catbee.ca)"


@dataclass
class Edit:
    """One file change. `kind` is replace | create | append."""
    path: str          # repo-relative
    kind: str
    old: str = ""
    new: str = ""
    note: str = ""
    # For a carried patch: the CVEs this one file closes and the upstream fix it came
    # from. The act lane needs both to re-compute its claim when a patch is dropped in
    # pre-flight (see act.triage_package), and plan.json is the record of that mapping.
    cves: list[str] = field(default_factory=list)
    fix_url: str = ""

    def preview(self) -> str:
        if self.kind == "append":
            return f"--- a/{self.path}\n+++ b/{self.path}\n@@ append @@\n" + \
                   "\n".join(f"+{l}" for l in self.new.rstrip("\n").splitlines())
        if self.kind == "create":
            return f"--- /dev/null\n+++ b/{self.path}\n" + \
                   "\n".join(f"+{l}" for l in self.new.rstrip("\n").splitlines())
        return (f"--- a/{self.path}\n+++ b/{self.path}\n"
                f"-{self.old.rstrip()}\n+{self.new.rstrip()}")


@dataclass
class HashInfo:
    sha256: str
    filename: str
    source: str          # "upstream .sha256sum" | "self-attested (downloaded)"
    url: str = ""
    extra: list[str] = field(default_factory=list)   # license-file hashes to carry over


# --- reading a recipe's download coordinates ---------------------------------

_VAR = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=\s*(.*)$")


def _join_continuations(text: str) -> str:
    """Buildroot .mk files continue a value onto the next line with a backslash."""
    return re.sub(r"\\\n[ \t]*", " ", text)


def recipe_prefix(mk_text: str, current: str = "") -> str:
    """The make prefix a recipe uses, read from the recipe itself.

    The package name is not the prefix: Buildroot's `gstreamer1` recipe assigns
    `GSTREAMER_1_VERSION`, and its sub-package `gst1-plugins-good` assigns
    `GST1_PLUGINS_GOOD_VERSION`. Reading it from the file - preferring the one whose
    value is the version actually shipped - means the edit targets the variable that
    decides what gets downloaded, instead of a name-derived guess that silently
    reassigns nothing.
    """
    candidates = re.findall(r"^([A-Z][A-Z0-9_]*)_VERSION\s*=\s*(.*)$",
                            _join_continuations(mk_text), re.M)
    if current:
        for prefix, value in candidates:
            if value.strip() == current:
                return prefix
    if candidates:
        return candidates[0][0]
    raise ValueError("no *_VERSION assignment in the recipe")


def read_recipe(mk_text: str, prefix: str) -> dict:
    """Every `PREFIX_* = value` assignment in a Buildroot .mk, short-keyed.

    Only the variables that decide what gets downloaded matter, but they reference
    each other (`LIBXML2_SITE` uses `$(LIBXML2_VERSION_MAJOR)`), so the whole
    `PREFIX_*` set is kept rather than just VERSION/SITE/SOURCE.
    """
    raw: dict[str, str] = {}
    for line in _join_continuations(mk_text).splitlines():
        m = _VAR.match(line.strip())
        if not m:
            continue
        name, value = m.group(1), m.group(2).strip()
        if name == prefix or name.startswith(prefix + "_"):
            raw[name[len(prefix):].lstrip("_")] = value
    return raw


def _match_paren(text: str, i: int) -> int:
    """Index of the ')' matching the '(' at i."""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return len(text)


def _split_top(args: str) -> list[str]:
    """Split a macro's arguments on commas that are not inside parentheses."""
    out, cur, depth = [], "", 0
    for ch in args:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
            continue
        cur += ch
    out.append(cur)
    return [a.strip() for a in out]


# Buildroot does not always fetch the archive its SITE names. For a package it pulls
# from a VCS it *rebuilds* the archive itself, and Buildroot decides both its name and
# its bytes:
#
#   package/pkg-generic.mk:572   $(2)_SOURCE ?= $(2)_BASENAME_RAW$(call pkg_source_ext,$(2))
#   package/pkg-utils.mk:51      pkg_source_ext = $(BR_FMT_VERSION_$($(1)_SITE_METHOD))
#                                        $(BR_FMT_VERSION_$($(1)_DOWNLOAD_POST_PROCESS)).tar.gz
#   package/pkg-download.mk:12-15   BR_FMT_VERSION_git = -git4, -svn5, -go2, -cargo6
#   package/pkg-golang.mk:101    $(2)_DOWNLOAD_POST_PROCESS = go
#   package/pkg-cargo.mk:190     $(2)_DOWNLOAD_POST_PROCESS = cargo
#
# So an authored golang recipe with no explicit *_SOURCE ships `fzf-0.74.5-go2.tar.gz`,
# and the sha256 check-hash wants is the sha256 of an archive Buildroot generated. A hash
# written under `<name>-<version>.tar.gz` names a file the build never produces: check-hash
# exits 3, dl-wrapper treats that as fatal, and the download step dies. Measured on
# 2026-10-12: fzf 0.74.5 failed exactly this way, and the component was then blacklisted.
BR_FMT_VERSION = {"git": "-git4", "svn": "-svn5", "go": "-go2", "cargo": "-cargo6"}

# The infra that sets a DOWNLOAD_POST_PROCESS, read from the recipe's own eval line.
INFRA_POST_PROCESS = {"golang": "go", "cargo": "cargo"}


def infra_post_process(mk_text: str) -> str:
    """Which download post-process Buildroot applies, from the recipe's own infra line."""
    m = re.search(r"\$\(eval\s+\$\((golang|cargo)-package\)\)", mk_text)
    return INFRA_POST_PROCESS.get(m.group(1), "") if m else ""


def site_method(raw: dict, coords: dict) -> str:
    """Buildroot's `$(PKG)_SITE_METHOD`: the recipe's own, else the SITE's URI scheme.

    package/pkg-generic.mk:606-611. Only git and svn carry a BR_FMT_VERSION, which is why
    an ordinary https tarball gets no suffix at all.
    """
    explicit = (raw.get("SITE_METHOD") or "").strip()
    if explicit:
        return explicit
    m = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://", coords.get("SITE") or "")
    return m.group(1) if m else ""


def source_suffix(mk_text: str, raw: dict, coords: dict) -> str:
    """Buildroot's `pkg_source_ext` for this recipe: the archive extension, suffix included."""
    post = (raw.get("DOWNLOAD_POST_PROCESS") or "").strip() or infra_post_process(mk_text)
    ext = BR_FMT_VERSION.get(site_method(raw, coords), "") + BR_FMT_VERSION.get(post, "")
    return ext + ".tar.gz"


def hash_is_generated(mk_text: str, raw: dict, coords: dict) -> bool:
    """True when Buildroot *builds* the archive, so its sha256 exists only after a build.

    The honest answer to "what is this tarball's hash?" for a golang/cargo recipe is not a
    download: Buildroot clones the repo and re-tars it. Hashing the SITE archive here
    produces a real digest of the wrong file - measured 2026-10-12, fzf 0.74.5's generated
    archive is 45fa3fb8..., GitHub's own tarball at the SITE URL is 5529d088...
    An explicit *_SOURCE does not make a golang/cargo archive downloadable. Buildroot
    downloads to that name, then the infra vendors the crates and re-packs under the same
    name, and check-hash runs on the re-packed file. Four authored recipes are that shape
    - cosmic-portal, greetd, uutils-coreutils, zram-generator - and greetd's own .hash
    says it in words: "the hash below is therefore of the POST-PROCESSED tarball".
    Treating uutils-coreutils as downloadable made the plan write the raw GitHub digest,
    a true digest of the wrong file, and the rehearsal of 2026-10-19 died on its download
    step: the fzf 0.74.5 failure of 2026-10-12 in a different recipe. This short-circuit
    was added in 0.12.1 for cosmic-portal and outlived the case it was drawn from.
    """
    post = (raw.get("DOWNLOAD_POST_PROCESS") or "").strip() or infra_post_process(mk_text)
    return post in ("go", "cargo")


def expand(raw: dict, prefix: str, version: str, name: str,
           mk_text: str = "") -> dict:
    """Expand the download coordinates with `version` substituted for VERSION.

    Handles `$(PKG_VAR)` references (nested) and Buildroot's
    `$(call github,owner,name,ref)` macro. Anything it cannot resolve stays
    unresolved, so the caller sees a broken URL instead of a plausible wrong one.

    `mk_text` lets the default source name follow Buildroot's own rule (see
    `source_suffix`); without it the caller keeps the pre-2026.10 behaviour of a bare
    `.tar.gz`, which is what a plain https tarball actually is.
    """
    values = dict(raw)
    values["VERSION"] = version

    def lookup(var: str) -> str:
        if var == "VERSION":
            return version
        short = var[len(prefix) + 1:] if var.startswith(prefix + "_") else var
        return render(values.get(short, ""))

    def render(text: str, depth: int = 0) -> str:
        if depth > 8 or not text:
            return text.strip()
        out, i = [], 0
        while i < len(text):
            if text.startswith("$(", i):
                j = _match_paren(text, i + 1)
                inner = text[i + 2:j]
                if inner.startswith("call github,"):
                    a = _split_top(inner[len("call github,"):])
                    owner = render(a[0], depth + 1) if a else ""
                    repo = render(a[1], depth + 1) if len(a) > 1 else ""
                    ref = render(a[2], depth + 1) if len(a) > 2 else ""
                    out.append(f"https://github.com/{owner}/{repo}/archive/{ref}")
                elif re.fullmatch(r"[A-Za-z0-9_]+", inner):
                    out.append(lookup(inner))
                else:
                    out.append(render(inner, depth + 1))
                i = j + 1
            else:
                out.append(text[i])
                i += 1
        return "".join(out).strip()

    out = {k: render(values[k]) for k in ("VERSION", "SITE", "SOURCE", "SOURCE_SUFFIX")
           if k in values}
    if not out.get("SOURCE"):
        # A recipe's own *_SOURCE_SUFFIX is an explicit override; otherwise the extension
        # is Buildroot's, suffix included - the part that was missing before 2026.10.14.
        out["SOURCE"] = f"{name}-{version}{out.get('SOURCE_SUFFIX') or source_suffix(mk_text, raw, out)}"
    return out


# --- hashes ------------------------------------------------------------------

def fetch_bytes(url: str, timeout: float = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def carry_license_hashes(hash_text: str, version: str) -> list[str]:
    """Hash lines that are not the tarball (license files) travel unchanged."""
    keep = []
    for line in hash_text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if version in line:
            continue
        keep.append(line.rstrip())
    return keep


class ArtifactUnreachable(Exception):
    """The target version's artifact does not resolve at the recipe's SITE.

    Named apart from a recipe that cannot be read, because the operator's fix is
    different and the tree is not the problem: the .mk and .hash parsed fine, the URL
    the recipe builds for the NEW version is a 404. A phantom version (release-monitoring
    naming a release upstream never tagged) means the finding should be dropped; a SITE
    pattern that moved upstream means the recipe needs fixing. Found 2026-10-19: bolt
    1.3.1 and rtkit 0.14 have no upstream tag at all, and libqalculate 5.13.2's release
    ships only Windows assets - all three reported as "could not read the recipe to
    edit: HTTP Error 404", which pointed at a broken tree that was not broken.
    """

    def __init__(self, url: str, filename: str, detail: str = ""):
        self.url, self.filename, self.detail = url, filename, detail
        super().__init__(f"{filename} is not fetchable at {url}"
                         + (f" ({detail})" if detail else ""))


def upstream_hash(hash_text: str, old_version: str, new_version: str, filename: str,
                  get=fetch_bytes) -> HashInfo | None:
    """Follow the stock .hash's own '# From <url>' to upstream's checksum file."""
    url = None
    for line in hash_text.splitlines():
        m = re.match(r"#\s*From\s+(\S*\.sha256sum)\s*$", line.strip())
        if m and old_version in m.group(1):
            url = m.group(1).replace(old_version, new_version)
            break
    if not url:
        return None
    try:
        text = get(url).decode("utf-8", "replace")
    except Exception:
        return None
    for line in text.splitlines():
        m = re.match(r"^\s*([0-9a-f]{64})\s+\*?(\S+)\s*$", line)
        if m and m.group(2) == filename:
            return HashInfo(sha256=m.group(1), filename=filename,
                            source="upstream .sha256sum", url=url)
    return None


def download_hash(url: str, filename: str, get=fetch_bytes) -> HashInfo:
    """Hash the artifact ourselves. Honest about what that means.

    Raises ArtifactUnreachable when the URL does not resolve. That is the only way
    the planner learns the difference between "no such release" and "no recipe":
    urllib's HTTPError is an OSError, so an unlabelled fetch failure arrives at the
    planner's recipe-read handler and gets reported as one.
    """
    try:
        data = get(url)
    except Exception as exc:
        raise ArtifactUnreachable(url, filename, str(exc)) from exc
    return HashInfo(sha256=sha256_of(data), filename=filename,
                    source="self-attested (downloaded here)", url=url)


class GeneratedHashError(Exception):
    """Buildroot could not produce the archive, or produced a file we cannot read."""


NO_HASH = "No hash found for"


def _make_source(run, build_root: Path, br_version: str, external_tree: str,
                 target: str, pkg: str):
    import os
    env = {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}
    return run(["make", "-C", str(build_root / f"buildroot-{br_version}"),
                f"O={build_root / 'output' / target}",
                f"BR2_EXTERNAL={external_tree}", f"{pkg}-source"],
               capture_output=True, text=True, env=env)


def generated_hash(run, build_root: Path, br_version: str, external_tree: str,
                   target: str, pkg: str, filename: str) -> HashInfo:
    """Let Buildroot build the archive, then hash the file Buildroot actually produced.

    This is the only way to get a true hash for a golang/cargo recipe: Buildroot clones the
    repo and re-tars it (`-go2` / `-cargo6`), so the digest `check-hash` will compare against
    exists only after `<pkg>-source` has run. `run` is injected so this is testable without
    a build tree, exactly like `dirclean_package`.

    The chicken-and-egg, and why a failed first pass is EXPECTED: `make <pkg>-source` runs the
    download step, and that step ends at `check-hash`, which cannot find a hash for a version
    whose hash is precisely what we are about to compute. `support/download/dl-wrapper` writes
    the generated archive into `$(DL_DIR)/<pkg>/` *before* it exits with the hash verdict - it
    cats the post-processed file to the final path, then exits with rc - so the bytes we need
    are on disk even though the step reports failure. Measured 2026-10-14: `make fzf-source`
    exited 2 with "ERROR: No hash found for fzf-0.74.5-go2.tar.gz" and
    dl/fzf/fzf-0.74.5-go2.tar.gz was there.

    So a failure naming the missing hash is the expected bootstrap state; any other failure is
    a real one and raises. The digest returned is proved by `verify_source` once written.
    """
    src = build_root / f"buildroot-{br_version}"
    p = _make_source(run, build_root, br_version, external_tree, target, pkg)
    out = (p.stdout or "") + (p.stderr or "")
    # BR2_DL_DIR = $(TOPDIR)/dl (see output/<target>/.config), shared across editions.
    archive = src / "dl" / pkg / filename
    if p.returncode != 0 and NO_HASH not in out:
        raise GeneratedHashError(
            f"make {pkg}-source exited {p.returncode} without reaching the hash check, so "
            f"the archive was never generated: {out[-400:]}")
    if not archive.is_file():
        raise GeneratedHashError(
            f"{archive} is not on disk, so there is nothing to hash (make {pkg}-source "
            f"exited {p.returncode})")
    return HashInfo(sha256=sha256_of(archive.read_bytes()), filename=filename,
                    source=f"generated by Buildroot (make {pkg}-source, {target})")


def verify_source(run, build_root: Path, br_version: str, external_tree: str,
                  target: str, pkg: str) -> None:
    """Re-run the download step with the hash written, and require it to pass.

    This is the proof that the digest just recorded is the one Buildroot accepts - the only
    check that closes the chicken-and-egg honestly. If it still fails, the hash we wrote is
    wrong, and publishing on the strength of it is the fzf 2026-10-12 failure all over again.
    """
    p = _make_source(run, build_root, br_version, external_tree, target, pkg)
    if p.returncode != 0:
        out = (p.stdout or "") + (p.stderr or "")
        raise GeneratedHashError(
            f"make {pkg}-source still fails after the hash was written, so the recorded hash "
            f"is not the one Buildroot accepts: {out[-400:]}")


# --- the shapes --------------------------------------------------------------

def carried_patches(cves: list[dict], known_bad=()) -> list[dict]:
    """The patches `patch_dir` will write, and the CVEs each one closes.

    One entry per upstream fix, in a stable order: two CVEs fixed by the same commit
    or the same review request are carried by **one** patch and both count as closed
    by it — deduplicating the patch must not shrink the claim. A CVE whose record
    names no fetchable fix is absent from the result: not carried, and not claimed.

    `known_bad` is the set of fix URLs a pre-flight already proved do not apply (see
    `guards.known_bad_fixes`). A group whose fix is one of them is dropped here rather
    than beside here, so the guard's question, the claim in the notes and the patches
    written to the tree are all the same filtered set: a known-bad fix is never fetched
    and never claimed, and the CVEs whose only fix was that one fall out of `closes`
    into `still_open`.

    Returns `[{"source": <the fix_commit dict>, "cves": [cve id, ...]}, ...]`.
    """
    bad = {f for f in (known_bad or ()) if f}
    groups: dict[str, dict] = {}
    for c in sorted(cves, key=lambda c: c["cve"]):
        src = c.get("fix_commit") or {}
        url = src.get("url", "")
        # Fetchable, not merely named: a commit on a host that serves no .patch is a
        # fact about the world the loop cannot act on, and the guard must not allow
        # what fetch_patch would then fail on.
        if not url or patch_url(url) is None:
            continue
        key = src.get("sha") or url
        # Matched on both spellings: the record stores the URL NVD named, the group is
        # keyed by the commit sha when the patch text names one.
        if url in bad or (src.get("sha") and src["sha"] in bad):
            continue
        groups.setdefault(key, {"source": src, "cves": []})["cves"].append(c["cve"])
    return [groups[k] for k in sorted(groups)]


def carried_cves(cves: list[dict], known_bad=()) -> list[str]:
    """Every CVE the patch set closes — the union over `carried_patches`.

    One rule, in one place: the planner takes the action's `closes` from this, the
    guards ask it what is fetchable, and `patch_dir` writes the patches from
    `carried_patches`. The tree and the security claim are computed from the same
    function, so they cannot drift apart — including the known-bad filter, which both
    guards therefore ask through the same set.
    """
    return sorted({cve for g in carried_patches(cves, known_bad) for cve in g["cves"]})


# --- is this patch a fix for THIS package? -----------------------------------

class NoCarriableFix(ValueError):
    """None of a candidate's named fixes can be carried for this package.

    Carries the per-CVE reasons so the plan can say *why* rather than reporting a
    generic failure: "NVD's commit for these CVEs is the advisory document, not a change
    to the code" is actionable information; "patch failed" is not.
    """

    def __init__(self, message: str, refused: list[dict]):
        super().__init__(message)
        self.refused = refused


# Upstream monorepos (gstreamer, and anything else that commits to one tree while
# shipping one tarball per component) put every file under this prefix.
_MONOREPO = "subprojects/"
# The lines `git format-patch` uses to name files. Hunk bodies are left alone:
# re-rooting is about where a patch lands, never about what it changes.
_PATH_LINES = ("--- a/", "+++ b/", "diff --git a/", "rename from ", "rename to ",
               "copy from ", "copy to ")


def patch_paths(text: str) -> list[str]:
    """The files a patch touches, from its own diff control lines."""
    out = []
    for line in (text or "").splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            path = line[4:].strip()
            if path == "/dev/null":
                continue
            out.append(re.sub(r"^[ab]/", "", path))
    return sorted(set(out))


def patch_components(text: str) -> set[str]:
    """Which upstream components a monorepo patch touches (empty if it is not
    monorepo-shaped)."""
    return {p.split("/")[1] for p in patch_paths(text) if p.startswith(_MONOREPO)}


def patch_subject(patch_text: str) -> str:
    """The commit subject a patch carries, for a human reading a refusal."""
    for line in (patch_text or "").splitlines():
        if line.startswith("Subject:"):
            subject = re.sub(r"^\[[^\]]*\]\s*", "", line[len("Subject:"):].strip())
            return subject.strip()[:80]
    return ""


def classify_patch(text: str, upstream: str) -> dict:
    """Is this fetched patch a fix for this package, and how must it be applied?

    Three things a named upstream commit can be that are not a fix we can carry:

    * **an advisory document.** GStreamer publishes the advisory as its own commit and
      NVD links that as the "fix" — measured 2026-10-07, 7 of gstreamer1's 10 named
      fixes were `security-advisories/sa-2026-*.md`, new files only. Carrying them adds
      markdown to the source tree, closes nothing, and would let the notes claim eight
      CVEs fixed.
    * **a fix for a sibling component.** NVD's CPE product is `gstreamer`, so a
      gst-plugins-ugly fix is matched against our `gstreamer1`. Those files are not in
      this tarball, so the patch can only fail.
    * **a patch spanning several components**, which no single tarball can take.

    A monorepo patch for *this* component is re-rooted: Buildroot downloads one
    standalone tarball per component (`GST1_PLUGINS_GOOD_SITE = .../src/gst-plugins-good`),
    so `subprojects/gst-plugins-good/gst/...` has to become `gst/...`. Buildroot cannot
    do this itself — its series format documents a `-pN` field, but `apply-patches.sh`
    cuts it away and hardcodes `-p1` (line 119; its own comment says "We assume this
    field to always be -p1"), verified against a real gstreamer patch on 2026-10-07.
    """
    lines = (text or "").splitlines()
    if not any(l.startswith("--- a/") for l in lines):
        return {"ok": False, "prefix": "",
                "reason": "adds new files only - it changes nothing the package ships"}
    paths = patch_paths(text)
    if not paths:
        return {"ok": False, "prefix": "", "reason": "names no file to change"}
    comps = patch_components(text)
    if len(comps) > 1:
        return {"ok": False, "prefix": "",
                "reason": f"spans upstream components {', '.join(sorted(comps))}"}
    if comps:
        comp = next(iter(comps))
        if comp != upstream:
            return {"ok": False, "prefix": "",
                    "reason": f"the fix is for {comp}, not for {upstream} (NVD matches the "
                              "project, not the component)"}
        stray = [p for p in paths if not p.startswith(_MONOREPO)]
        if stray:
            return {"ok": False, "prefix": "", "reason": "mixes monorepo paths with "
                                                         "package-root paths"}
        return {"ok": True, "prefix": f"{_MONOREPO}{comp}/",
                "reason": f"re-rooted from {_MONOREPO}{comp}/ for the standalone tarball"}
    return {"ok": True, "prefix": "", "reason": ""}


def reroot_patch(text: str, prefix: str) -> str:
    """Strip a monorepo prefix from a patch's file paths, leaving its hunks alone."""
    if not prefix:
        return text
    return "".join(line.replace(prefix, "") if line.startswith(_PATH_LINES) else line
                   for line in text.splitlines(True))


def patch_dir(repo: Path, pkg: str, current: str, cves: list[dict], editions: list[str],
              get=fetch_bytes, upstream: str = "",
              known_bad=()) -> tuple[list[Edit], list[dict]]:
    """A stock Buildroot package: carry the upstream fixes as patches.

    The subset is deliberate, three times over: a CVE whose record names no fetchable
    fix is not carried (see `carried_patches`), a fix a pre-flight already proved does
    not apply is not carried *or fetched* (`known_bad` — the network cost is real and
    Buildroot has already answered that question), and a fetched patch that is not a
    fix for this package is not carried either (see `classify_patch`). The return value
    is the pair the planner needs to stay honest — the edits, and the CVEs refused with
    the reason for each.

    This is the shape the build proved (2026-10-05). The version override did not:
    `LIBXML2_VERSION = 2.15.4` in external.mk made Buildroot *download* 2.15.4 while
    the pattern rule `$(BUILD_DIR)/%/.stamp_extracted` (package/pkg-generic.mk:206)
    had already fixed the stamp path and `$(@D)` to 2.15.3, because
    `include package/Makefile.in` (Makefile:526) runs before
    `include $(BR2_EXTERNAL_FILE)` (Makefile:559). Extract then died:
    `tar: .../build/libxml2-2.15.4: Cannot open: No such file or directory`.
    Patches go through the same global dir and apply cleanly:
    `Applying 0001-xmlregexp-...patch using patch: patching file xmlregexp.c`.

    The version is untouched, so the recipe's own hash file stays valid and the
    build stays byte-comparable to every other edition.
    """
    edits: list[Edit] = []
    refused: list[dict] = []
    n = 0
    for group in carried_patches(cves, known_bad):
        src = group["source"]
        # One review request can be several commits: each becomes its own patch file,
        # because Buildroot pipes one file to one `patch` invocation.
        for text in fetch_patches(src.get("url", ""), get):
            verdict = classify_patch(text, upstream or pkg)
            if not verdict["ok"]:
                refused.append({"cves": list(group["cves"]), "fix_url": src.get("url", ""),
                                "reason": verdict["reason"], "subject": patch_subject(text)})
                continue
            text = reroot_patch(text, verdict["prefix"])
            sha = src.get("sha") or patch_sha(text)
            n += 1
            edits.append(Edit(
                path=f"buildroot-external/patches/{pkg}/{current}/{n:04d}-"
                     f"{patch_slug(text, sha)}.patch",
                kind="create", new=text,
                note=f"{', '.join(group['cves'])} ({src.get('kind', 'commit')} from "
                     f"{src.get('host', '')} {(sha or src.get('url', ''))[:12]}"
                     + (f"; {verdict['reason']}" if verdict["prefix"] else ""),
                cves=list(group["cves"]), fix_url=src.get("url", "")))
    if not edits:
        raise NoCarriableFix(
            f"no upstream fix can be carried for {pkg}: "
            + "; ".join(sorted({r["reason"] for r in refused})), refused)
    edits.append(Edit(path="buildroot-external/configs/layers/core.config", kind="ensure",
                      new='BR2_GLOBAL_PATCH_DIR="$(BR2_EXTERNAL_PENGYOS_PATH)/patches"',
                      note="the dir Buildroot searches for patches and per-version hashes "
                           "(package/pkg-utils.mk:pkg-patch-hash-dirs); defconfigs are "
                           "recomposed after"))
    return edits, refused


# Hosts verified (2026-10-05, 2026-10-06) to serve `<url>.patch` as git
# format-patch: GitHub commits and pulls, the three GitLab instances (commits and
# merge requests), and FFmpeg's own GitLab, where 16 of the ffmpeg fix references
# actually live and which the old list silently could not fetch.
_PATCH_HOSTS = ("github.com", "gitlab.com", "gitlab.gnome.org", "gitlab.freedesktop.org",
                "code.ffmpeg.org")


def patch_url(source_url: str) -> str | None:
    """The git format-patch form of an upstream fix URL — a commit or a review
    request — for the hosts we know serve one.

    NVD sometimes hands us the patch form directly (gstreamer's advisory references
    end in `.patch`), so that case is passed through rather than doubled.
    """
    if not source_url:
        return None
    url = source_url.rstrip("/")
    if url.endswith(".patch"):
        return url
    if any(h in url for h in _PATCH_HOSTS):
        return url + ".patch"
    return None


def patch_sha(patch_text: str) -> str:
    """The commit a fetched format-patch came from.

    `git format-patch` opens with `From <sha> Mon Sep 17 00:00:00 2001`, so a fix
    NVD named only as a pull/merge request still ends up recorded against a real
    commit sha — the same evidence a named commit gives.
    """
    m = re.match(r"^From ([0-9a-f]{7,40}) ", patch_text or "")
    return m.group(1) if m else ""


# The line `git format-patch` writes at the head of every patch it emits. It is also
# the only thing that separates one patch from the next when a whole review request
# is fetched at once.
_PATCH_HEAD = re.compile(r"^From ([0-9a-f]{7,40}) Mon Sep 17 00:00:00 2001$", re.M)


def split_patches(text: str) -> list[str]:
    """Split a fetched review request into the individual patches it contains.

    A GitLab merge request or GitHub pull request `.patch` is the whole series
    concatenated — measured 2026-10-07 on the live plan, gstreamer's MR 11243 came back
    as 4 patches in one file and MR 12xxx as 6. Buildroot applies one file per patch
    (`apply-patches.sh` pipes each file to `patch` once), so a concatenated response
    makes patch read the next email's headers as garbage: that is the `|-- |GitLab`
    noise in a real `gstreamer1-patch` failure. Splitting is what makes a review
    request carriable at all.
    """
    marks = list(_PATCH_HEAD.finditer(text or ""))
    if not marks:
        return []
    ends = [m.start() for m in marks[1:]] + [len(text)]
    return [text[m.start():end] for m, end in zip(marks, ends)]


def fetch_patches(source_url: str, get=fetch_bytes) -> list[str]:
    """Fetch an upstream fix as one or more git patches.

    One review request can be several commits, so this returns a list. A response that
    is not a git format-patch at all (an HTML page, an error page) raises rather than
    becoming a `.patch` file that poisons the tree.
    """
    url = patch_url(source_url)
    if not url:
        raise ValueError(f"no .patch form known for {source_url!r}")
    parts = split_patches(get(url).decode("utf-8", "replace"))
    if not parts:
        raise ValueError(f"{url} is not a git format-patch")
    return parts


def fetch_patch(commit_url: str, get=fetch_bytes) -> str:
    """The single patch a commit URL serves. Review requests are handled by
    `fetch_patches`, which is what `patch_dir` uses."""
    return fetch_patches(commit_url, get)[0]


def carried_patch_files(repo: Path) -> list[str]:
    """Every patch we currently carry, repo-relative.

    These are the loop's own security maintenance. A tree move has to account for each one:
    the new line either already carries the fix (the patch is retired and the CVE is
    recorded as closed by the release) or it does not (the patch moves to the new version's
    directory). Keeping them all silently is how a loop re-fixes what upstream fixed.
    """
    root = repo / "buildroot-external" / "patches"
    if not root.is_dir():
        return []
    return sorted(str(p.relative_to(repo)) for p in root.rglob("*.patch"))


def tree_move(repo: Path, target: str) -> list[Edit]:
    """Move every edition to a new Buildroot release series.

    The edit is small because the tree choice is declared, not implied: `targets.toml`
    names `br_version` for every edition and pengyos-build resolves the checkout from it.

    What this edit does **not** do is the part that is not built yet: fetch and verify the
    new line, recompose the external layers against its package set, re-resolve the
    COSMIC / NVIDIA / kernel pins, rebuild every edition from scratch, and retire the
    carried patches the new line already contains. `guards.tree_move_executable` refuses
    the action so none of that is implied by a plan that looks executable.
    """
    text = (repo / "targets.toml").read_text()
    found = re.findall(r'br_version = "([^"]+)"', text)
    if not found:
        raise ValueError("targets.toml names no br_version")
    current = sorted(set(found))
    new = re.sub(r'br_version = "[^"]+"', f'br_version = "{target}"', text)
    note = (f"Buildroot {'/'.join(current)} -> {target} for {len(found)} edition(s); every "
            f"patch under buildroot-external/patches must be re-checked against {target} "
            f"and retired if that line already carries the fix")
    return [Edit(path="targets.toml", kind="replace", old=text, new=new, note=note)]


# --- declaring a carried fix to the tooling that reads the tree --------------

CVE_DECLARATIONS = "buildroot-external/patches/cve-declarations.mk"

_DECL_HEADER = """# Carried upstream fixes, declared to the tooling that reads this tree.
#
# Written by pengyos-nightly from the patches that actually survived pre-flight: one line
# per CVE, each preceded by a comment naming the patch file - Buildroot's own convention
# (docs/manual/adding-packages-generic.adoc). pkg-stats cannot see a patch delivered
# through BR2_GLOBAL_PATCH_DIR (set_patch_count() walks only the package's own directory),
# so without this a package keeps reporting "affected by CVEs" after the fix shipped, and
# the divergence ledger says closed while the tree says open.
#
# Each block is regenerated whole from the patch files the tree carries, so a patch taken
# out of the tree takes its declaration with it: an ignore that outlives its patch would
# hide a real exposure. Included from ../external.mk, and checked against divergence.toml
# by scripts/pengyos_gate.py:check_patch_declarations.
"""


def _mk_prefix(component: str) -> str:
    """Buildroot's variable prefix for a package name: uppercased, dashes as underscores."""
    return component.upper().replace("-", "_")


def _hand_declarations(repo: Path) -> dict[str, set[str]]:
    """CVEs declared by a human, in external.mk or in a recipe we author.

    Deliberately excludes the generated file: that one is regenerated from the tree, so a
    CVE sitting in its current block is not evidence it is already declared.
    """
    out: dict[str, set[str]] = {}
    files = [repo / "buildroot-external/external.mk",
             *sorted((repo / "buildroot-external" / "package").glob("*/*.mk"))]
    for f in files:
        try:
            text = f.read_text()
        except OSError:
            continue
        for m in re.finditer(r"^\s*([A-Z][A-Z0-9_]*)_IGNORE_CVES\s*\+?=\s*(.*)$", text, re.M):
            out.setdefault(m.group(1), set()).update(c for c in m.group(2).split()
                                                     if c.startswith("CVE-"))
    return out


def declared_cves(repo: Path) -> dict[str, set[str]]:
    """Every CVE this tree declares, generated blocks and hand-written overrides together."""
    out = {k: set(v) for k, v in _hand_declarations(repo).items()}
    try:
        text = (repo / CVE_DECLARATIONS).read_text()
    except OSError:
        return out
    for m in re.finditer(r"^\s*([A-Z][A-Z0-9_]*)_IGNORE_CVES\s*\+?=\s*(.*)$", text, re.M):
        out.setdefault(m.group(1), set()).update(c for c in m.group(2).split()
                                                 if c.startswith("CVE-"))
    return out


def cve_declaration_edit(repo: Path, component: str, carried: list[dict]) -> Edit | None:
    """Declare the fixes this batch actually carries — and retire the ones it does not.

    The block for one package is regenerated from `carried`, the patches that survived
    pre-flight, not from what the plan promised. A fix Buildroot rejected therefore gets no
    declaration, which is the whole point: the tree may claim only what it carries.

    A CVE already declared by hand (in `external.mk` or in a recipe we author) is left
    where it is rather than duplicated. A package left with nothing to declare loses its
    block entirely.
    """
    prefix = _mk_prefix(component)
    hand = _hand_declarations(repo).get(prefix, set())
    rows: list[tuple[str, list[str]]] = []
    for e in sorted(carried, key=lambda x: x.get("path", "")):
        cves = sorted({c for c in (e.get("cves") or []) if c not in hand})
        if cves:
            rows.append((Path(e["path"]).name, cves))

    path = repo / CVE_DECLARATIONS
    text = path.read_text() if path.is_file() else _DECL_HEADER
    start, end = f"# >>> {component}", f"# <<< {component}"
    block = ""
    if rows:
        lines = [f"{start} (regenerated by pengyos-nightly from the patches in the tree)"]
        for name, cves in rows:
            lines.append(f"# {name}")
            lines.append(f"{prefix}_IGNORE_CVES += {' '.join(cves)}")
        lines.append(end)
        block = "\n".join(lines) + "\n"

    pat = re.compile(re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.S)
    if pat.search(text):
        new = pat.sub((block if block else ""), text, count=1)
    else:
        new = text.rstrip("\n") + ("\n\n" + block if block else "\n")
    if new == text:
        return None
    total = sum(len(c) for _, c in rows)
    note = (f"declare {total} carried fix(es) for {component} to pkg-stats via "
            f"{prefix}_IGNORE_CVES; the block is regenerated from the patch files that "
            "survived pre-flight, so a dropped patch drops its declaration"
            if rows else f"retire {component}'s declaration: the tree carries no patch for it")
    kind = "create" if not path.is_file() else "replace"
    return Edit(path=CVE_DECLARATIONS, kind=kind, old=text if kind == "replace" else "",
                new=new, note=note)


# --- writing the divergence down ---------------------------------------------

LEDGER = "divergence.toml"

_LEDGER_NOTE = """# Written by pengyos-nightly when it lands a patch set. Same principle as
# cve-declarations.mk: the tree may claim only what it carries, and a divergence with no
# entry has no expiry. scripts/pengyos_gate.py:check_ledger_covers_patch_sets fails the
# repo when a patch set is missing from here, so an unattended lane cannot skip it.
"""

_LEDGER_HEADER = """# PengyOS divergence ledger
#
# See the comment block at the top of the tracked file for the rule. This file was created
# by pengyos-nightly, which owns the entries it writes here.

schema = 1
"""


def _ledger_blocks(text: str) -> list[tuple[int, int, str]]:
    """`[(start, end, block)]` for every `[[divergence]]` table, in file order."""
    starts = [m.start() for m in re.finditer(r"^\[\[divergence\]\]", text, re.M)]
    out = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        out.append((start, end, text[start:end]))
    return out


def _render_closes(rows: list[tuple[str, str]]) -> str:
    """The `closes` array, one CVE per line with the patch file that carries it.

    The comment is the point: it is the only place in the ledger that says *which* patch
    closes *which* CVE without cross-reading cve-declarations.mk.
    """
    if not rows:
        return "closes = []\n"
    return "\n".join(["closes = ["]
                     + [f'  "{cve}",  # {name}' for cve, name in rows]
                     + ["]", ""])


def _closes_span(block: str) -> tuple[int, int] | None:
    """Offsets of the `closes = ...` value inside one block, single- or multi-line."""
    m = re.search(r"^closes\s*=\s*\[", block, re.M)
    if not m:
        return None
    i = m.end()
    single = re.match(r"[^\n]*\]\s*\n", block[i:])
    if single:
        return (m.start(), i + single.end())
    close = re.search(r"^\]\s*\n", block[i:], re.M)
    if not close:
        return None
    return (m.start(), i + close.end())


def _patches_on_disk(repo: Path, patches_dir: str) -> list[str]:
    d = repo / patches_dir
    return sorted(p.name for p in d.glob("*.patch")) if d.is_dir() else []


def _entry_patches_dir(block: str) -> str:
    m = re.search(r'^patches_dir\s*=\s*"([^"]*)"', block, re.M)
    return m.group(1) if m else ""


def _entry_component(block: str) -> str:
    m = re.search(r'^component\s*=\s*"([^"]*)"', block, re.M)
    return m.group(1) if m else ""


def ledger_edit(repo: Path, component: str, carried: list[dict],
                editions: list[str], release: str, today: str) -> Edit | None:
    """Write the patch set into `divergence.toml`, or take it back out.

    The ledger's own rule: every divergence gets an entry *before* it lands, and an entry
    with no expiry is not an approved divergence. Until 2026-10-18 that rule was the
    operator's to keep, and the operator is the slowest part of the project - three patch
    sets (ffmpeg, gst1-plugins-good, gst1-plugins-bad) sat in the tree with no entry for
    eight days because writing them down was a human step after an unattended one.

    So the lane writes it, on the same terms as the pkg-stats declaration: from what
    survived pre-flight, whole, and regenerated rather than appended. Closes map to the
    patch file that carries them. Everything else an entry can say (costs, natural fix
    versions, what was learned) stays a human's to add - this writes the fields the gate
    requires and leaves the prose alone.

    An entry a human wrote is *updated*, not replaced: the closes array is the part the
    lane owns, because the lane is what changes which patches the tree carries.
    """
    path = repo / LEDGER
    original = path.read_text() if path.is_file() else _LEDGER_HEADER
    text = original
    blocks = _ledger_blocks(text)
    notes: list[str] = []
    changed = False

    d = ""
    rows: list[tuple[str, str]] = []
    if carried:
        d = Path(sorted(c["path"] for c in carried)[0]).parent.as_posix()
        seen: set[str] = set()
        for c in sorted(carried, key=lambda x: x["path"]):
            name = Path(c["path"]).name
            for cve in c.get("cves") or []:
                if cve and cve not in seen:
                    seen.add(cve)
                    rows.append((cve, name))

    hit = next((b for b in blocks if _entry_patches_dir(b[2]) == d), None) if d else None
    if hit:
        start, end, block = hit
        span = _closes_span(block)
        rendered = _render_closes(rows)
        if span is not None:
            current = block[span[0]:span[1]]
            # Compare what the array *says*, not how it is written. A night that carries the
            # same set must not reformat an entry a human annotated - the patch filename a
            # generated line carries is poorer than the prose comment already there.
            if re.findall(r'"(CVE-[^"]+)"', current) != [cve for cve, _ in rows]:
                text = text[:start] + block[:span[0]] + rendered + block[span[1]:] + text[end:]
                changed = True
                notes.append(f"{len(rows)} CVE(s) closes")
        else:
            # An entry the gate would already reject (no closes field at all). Put one in
            # after patches_dir rather than rewriting a block someone may be mid-editing.
            m = re.search(r'^patches_dir\s*=\s*"[^"]*"\n', block, re.M)
            if m:
                new_block = block[:m.end()] + rendered + block[m.end():]
                text = text[:start] + new_block + text[end:]
                changed = True
                notes.append(f"{len(rows)} CVE(s) closes")

    if not hit:
        if carried:
            version = Path(d).name
            entry = ("\n[[divergence]]\n"
                     f'id        = "{component}-{version}-carried"\n'
                     'kind      = "patch-set"\n'
                     f'component = "{component}"\n'
                     f'pinned_version = "{version}"\n'
                     f'editions  = [{", ".join(chr(34) + e + chr(34) for e in editions)}]\n'
                     f'carried_since = "{today}"\n'
                     f'carried_in    = ["{release}"]\n'
                     f'patches_dir   = "{d}"\n'
                     + _render_closes(rows)
                     + f'expiry = "Buildroot pins {component} outside the NVD range of the '
                       f'CVEs this set closes; pkg-stats then reports the ignores as stale '
                       f'and the patch set is deleted"\n'
                     'observe_source = "pkg-stats"\n'
                     'upstream_status = "merged"\n')
            text = text.rstrip("\n") + "\n\n" + _LEDGER_NOTE + entry
            changed = True
            notes.append(f"write {component} {version} down: {len(rows)} CVE(s) from "
                         f"{len(carried)} patch(es)")

    # An entry whose patch set is no longer in the tree is worse than no entry: the gate
    # fails on it, and it reads as a divergence we are still carrying.
    stale = [b for b in _ledger_blocks(text)
             if _entry_component(b[2]) == component
             and _entry_patches_dir(b[2])
             and _entry_patches_dir(b[2]) != d
             and not _patches_on_disk(repo, _entry_patches_dir(b[2]))]
    for start, end, block in reversed(stale):
        text = text[:start] + text[end:]
        changed = True
        notes.append(f"retire {component}: {_entry_patches_dir(block)} carries no patch")

    if not changed:
        return None
    text = re.sub(r"\n{3,}(?=\[\[divergence\]\])", "\n\n", text)
    text = text.rstrip("\n") + "\n"
    kind = "replace" if path.is_file() else "create"
    return Edit(path=LEDGER, kind=kind, old=original, new=text,
                note="; ".join(notes))


def patch_slug(patch_text: str, sha: str) -> str:
    """A Buildroot-style patch name, from the commit's own Subject line.

    git format-patch writes `Subject: [PATCH 1/3] xmlregexp: Prevent ...`; the
    bracketed part is not part of the title, so it is dropped rather than becoming
    a `patch-` prefix on every file.
    """
    subject = ""
    for line in patch_text.splitlines():
        if line.startswith("Subject:"):
            subject = line[len("Subject:"):].strip()
            break
    subject = re.sub(r"^\[[^\]]*\]\s*", "", subject)
    slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:60]
    return slug or f"fix-{sha[:12]}"


def recipe_bump(repo: Path, pkg: str, mk_text: str, hash_text: str, target: str,
                get=fetch_bytes) -> tuple[list[Edit], HashInfo]:
    """A package we author: change its own .mk and its own .hash.

    A golang/cargo recipe gets no .hash edit from here, on purpose: Buildroot generates
    that archive itself, so its sha256 does not exist until `<pkg>-source` has run. The
    returned HashInfo says so (`source="generated ..."`) with the filename Buildroot will
    look for, and the act lane fills it in from the file the build produced
    (see act.run_batch's generated-hash step). Writing a download-and-hash here would name
    a file the build never produces and blacklist the component - that is how fzf 0.74.5
    was lost on 2026-10-12.
    """
    name = Path(pkg).name
    prefix = recipe_prefix(mk_text, "")
    raw = read_recipe(mk_text, prefix)
    current = raw.get("VERSION", "")
    coords = expand(raw, prefix, target, name, mk_text)
    filename = coords.get("SOURCE") or f"{name}-{target}.tar.gz"

    if hash_is_generated(mk_text, raw, coords):
        hi = HashInfo(sha256="", filename=filename,
                      source="generated by Buildroot (make "
                             f"{name}-source); sha256 read from the archive it produces")
        return [Edit(path=f"buildroot-external/package/{name}/{name}.mk", kind="replace",
                     old=f"{prefix}_VERSION = {current}",
                     new=f"{prefix}_VERSION = {target}",
                     note=f"authored recipe: {name} {current} -> {target}")], hi

    hi = upstream_hash(hash_text, current, target, filename, get)
    if hi is None:
        site = coords.get("SITE", "")
        hi = download_hash(f"{site.rstrip('/')}/{filename}", filename, get)
    anchor, extra = _hash_entry(hash_text, current)
    hi.extra = extra

    old_line = f"{prefix}_VERSION = {current}"
    new_line = f"{prefix}_VERSION = {target}"
    edits = [Edit(path=f"buildroot-external/package/{name}/{name}.mk", kind="replace",
                  old=old_line, new=new_line,
                  note=f"authored recipe: {name} {current} -> {target}"),
             Edit(path=f"buildroot-external/package/{name}/{name}.hash", kind="replace",
                  old=anchor,
                  new=_hash_block(hi, comment=f"# From {hi.url}" if hi.url else
                                  f"# {hi.source}: {filename}"),
                  note=f"tarball hash for {filename} ({hi.source})")]
    return edits, hi


def _tarball_lines(hash_text: str, version: str) -> str:
    """The lines describing this version's tarball hash, as a CONTIGUOUS slice.

    `apply_edits` replaces this text with the new hash block, so the anchor has to be a
    real slice of the file. Taking every line that mentions the version and joining them
    is not a slice: a recipe that documents its hash in a comment paragraph -
    uutils-coreutils explains the cargo re-pack over four comment lines, one of which
    names no version - makes the matching lines non-adjacent, and the joined text then
    exists nowhere in the file. The act lane bailed on the whole batch with "did not
    find the line to replace" on 2026-10-16, and again in rehearsal on 2026-10-19:
    a night lost each time, and the message named a file edit, not the anchor bug.

    The span from the first match to the last is contiguous by construction, and the
    paragraph is about that hash, so it travels with it. It runs on past the tarball line,
    over the licence-hash lines that follow it, because `_hash_block` re-emits them: an
    anchor that stops at the tarball line leaves them in the file and the replacement
    writes them a second time. The tree carries the result of every bump before this fix -
    alsa-ucm-conf and libmtp each have two identical LICENSE / COPYING lines, written by
    the loop on 2026-10-05 and 2026-10-06.
    """
    lines = hash_text.splitlines()
    hits = [i for i, line in enumerate(lines) if version in line]
    if not hits:
        return ""
    end = hits[-1]
    while end + 1 < len(lines) and re.match(r"^\s*sha\d+\s", lines[end + 1]):
        end += 1
    return "\n".join(lines[hits[0]:end + 1])


def _hash_entry(hash_text: str, version: str) -> tuple[str, list[str]]:
    """(the contiguous slice to replace, the licence lines the new block must re-emit).

    Both come from the same slice. Taking the licence lines from the whole file writes a
    second copy of every line the anchor already replaced - which is what the tree has
    from every `recipe_bump` before 0.15.0.
    """
    anchor = _tarball_lines(hash_text, version)
    return anchor, carry_license_hashes(anchor, version)


def _hash_block(hi: HashInfo, comment: str) -> str:
    lines = [comment, f"sha256  {hi.sha256}  {hi.filename}"]
    lines += hi.extra
    return "\n".join(lines) + "\n"


def generated_hash_edit(pkg: str, hash_text: str, current: str, hi: HashInfo) -> Edit:
    """The .hash edit recording a hash Buildroot's own build produced.

    Same file shape as a `recipe_bump` hash edit, and deliberately built here rather than
    in the act lane: the .hash format belongs to this module, and a second writer of it in
    act.py is a second format to drift. The comment says where the digest came from, so a
    reader of the recipe can tell a generated archive from an upstream checksum.
    """
    name = Path(pkg).name
    anchor, hi.extra = _hash_entry(hash_text, current)
    return Edit(path=f"buildroot-external/package/{name}/{name}.hash", kind="replace",
                old=anchor,
                new=_hash_block(hi, comment=f"# Generated by Buildroot (make {name}-source)"),
                note=f"tarball hash for {hi.filename} ({hi.source})")


def apply_edits(repo: Path, edits: list[Edit]) -> list[str]:
    """Apply the edits for real. Returns the paths touched.

    `ensure` is idempotent: append the line only if it is not already there, so a
    second action on another stock package does not duplicate BR2_GLOBAL_PATCH_DIR.
    """
    touched = []
    for e in edits:
        p = repo / e.path
        if e.kind == "append":
            p.parent.mkdir(parents=True, exist_ok=True)
            text = p.read_text() if p.exists() else ""
            if e.new.strip() and e.new.strip() in text:
                touched.append(e.path)
                continue
            p.write_text(text.rstrip("\n") + "\n" + e.new)
        elif e.kind == "ensure":
            p.parent.mkdir(parents=True, exist_ok=True)
            text = p.read_text() if p.exists() else ""
            if e.new not in text:
                p.write_text(text.rstrip("\n") + "\n" + e.new + "\n")
        elif e.kind == "create":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(e.new)
        elif e.kind == "replace":
            text = p.read_text()
            if e.old not in text:
                raise ValueError(f"{e.path}: did not find the line to replace "
                                 f"({e.old!r})")
            p.write_text(text.replace(e.old, e.new, 1))
        else:
            raise ValueError(f"unknown edit kind {e.kind!r}")
        touched.append(e.path)
    return touched
