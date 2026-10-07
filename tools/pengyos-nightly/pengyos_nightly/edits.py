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


def expand(raw: dict, prefix: str, version: str, name: str) -> dict:
    """Expand the download coordinates with `version` substituted for VERSION.

    Handles `$(PKG_VAR)` references (nested) and Buildroot's
    `$(call github,owner,name,ref)` macro. Anything it cannot resolve stays
    unresolved, so the caller sees a broken URL instead of a plausible wrong one.
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
        out["SOURCE"] = f"{name}-{version}{out.get('SOURCE_SUFFIX') or '.tar.gz'}"
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
    """Hash the artifact ourselves. Honest about what that means."""
    return HashInfo(sha256=sha256_of(get(url)), filename=filename,
                    source="self-attested (downloaded here)", url=url)


# --- the shapes --------------------------------------------------------------

def carried_patches(cves: list[dict]) -> list[dict]:
    """The patches `patch_dir` will write, and the CVEs each one closes.

    One entry per upstream fix, in a stable order: two CVEs fixed by the same commit
    or the same review request are carried by **one** patch and both count as closed
    by it — deduplicating the patch must not shrink the claim. A CVE whose record
    names no fetchable fix is absent from the result: not carried, and not claimed.

    Returns `[{"source": <the fix_commit dict>, "cves": [cve id, ...]}, ...]`.
    """
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
        groups.setdefault(key, {"source": src, "cves": []})["cves"].append(c["cve"])
    return [groups[k] for k in sorted(groups)]


def carried_cves(cves: list[dict]) -> list[str]:
    """Every CVE the patch set closes — the union over `carried_patches`.

    One rule, in one place: the planner takes the action's `closes` from this, the
    guard asks it what is fetchable, and `patch_dir` writes the patches from
    `carried_patches`. The tree and the security claim are computed from the same
    function, so they cannot drift apart.
    """
    return sorted({cve for g in carried_patches(cves) for cve in g["cves"]})


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
              get=fetch_bytes, upstream: str = "") -> tuple[list[Edit], list[dict]]:
    """A stock Buildroot package: carry the upstream fixes as patches.

    The subset is deliberate, twice over: a CVE whose record names no fetchable fix is
    not carried (see `carried_patches`), and a fetched patch that is not a fix for this
    package is not carried either (see `classify_patch`). The return value is the pair
    the planner needs to stay honest — the edits, and the CVEs refused with the reason
    for each.

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
    for group in carried_patches(cves):
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
    """A package we author: change its own .mk and its own .hash."""
    name = Path(pkg).name
    prefix = recipe_prefix(mk_text, "")
    raw = read_recipe(mk_text, prefix)
    current = raw.get("VERSION", "")
    coords = expand(raw, prefix, target, name)
    filename = coords.get("SOURCE") or f"{name}-{target}.tar.gz"

    hi = upstream_hash(hash_text, current, target, filename, get)
    if hi is None:
        site = coords.get("SITE", "")
        hi = download_hash(f"{site.rstrip('/')}/{filename}", filename, get)
    hi.extra = carry_license_hashes(hash_text, current)

    old_line = f"{prefix}_VERSION = {current}"
    new_line = f"{prefix}_VERSION = {target}"
    edits = [Edit(path=f"buildroot-external/package/{name}/{name}.mk", kind="replace",
                  old=old_line, new=new_line,
                  note=f"authored recipe: {name} {current} -> {target}"),
             Edit(path=f"buildroot-external/package/{name}/{name}.hash", kind="replace",
                  old=_tarball_lines(hash_text, current),
                  new=_hash_block(hi, comment=f"# From {hi.url}" if hi.url else
                                  f"# {hi.source}: {filename}"),
                  note=f"tarball hash for {filename} ({hi.source})")]
    return edits, hi


def _tarball_lines(hash_text: str, version: str) -> str:
    lines = [l for l in hash_text.splitlines() if version in l]
    return "\n".join(lines)


def _hash_block(hi: HashInfo, comment: str) -> str:
    lines = [comment, f"sha256  {hi.sha256}  {hi.filename}"]
    lines += hi.extra
    return "\n".join(lines) + "\n"


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
