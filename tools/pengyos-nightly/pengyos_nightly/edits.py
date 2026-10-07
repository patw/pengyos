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

Get = "callable"  # documented: every fetch here takes an injectable get(url)->bytes

UA = "pengyos-nightly/0.4 (+https://pengyos.catbee.ca)"


@dataclass
class Edit:
    """One file change. `kind` is replace | create | append."""
    path: str          # repo-relative
    kind: str
    old: str = ""
    new: str = ""
    note: str = ""

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

def patch_dir(repo: Path, pkg: str, current: str, cves: list[dict], editions: list[str],
              get=fetch_bytes) -> list[Edit]:
    """A stock Buildroot package: carry the upstream fix commits as patches.

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
    seen: set[str] = set()
    n = 0
    for c in sorted(cves, key=lambda c: c["cve"]):
        commit = c.get("fix_commit") or {}
        sha, url = commit.get("sha", ""), commit.get("url", "")
        if not sha or sha in seen:
            continue
        seen.add(sha)
        n += 1
        text = fetch_patch(url, get)
        slug = patch_slug(text, sha)
        edits.append(Edit(
            path=f"buildroot-external/patches/{pkg}/{current}/{n:04d}-{slug}.patch",
            kind="create", new=text,
            note=f"{c['cve']} ({c['priority']}) fix from {commit.get('host', '')} {sha[:12]}"))
    if not edits:
        raise ValueError(f"no upstream fix commit to carry for {pkg}")
    edits.append(Edit(path="buildroot-external/configs/layers/core.config", kind="ensure",
                      new='BR2_GLOBAL_PATCH_DIR="$(BR2_EXTERNAL_PENGYOS_PATH)/patches"',
                      note="the dir Buildroot searches for patches and per-version hashes "
                           "(package/pkg-utils.mk:pkg-patch-hash-dirs); defconfigs are "
                           "recomposed after"))
    return edits


_PATCH_HOSTS = ("github.com", "gitlab.com", "gitlab.gnome.org", "gitlab.freedesktop.org")


def patch_url(commit_url: str) -> str | None:
    """The .patch form of a commit URL, for the hosts we know serve one (both were
    verified against real fix commits on 2026-10-05)."""
    if commit_url and any(h in commit_url for h in _PATCH_HOSTS):
        return commit_url.rstrip("/") + ".patch"
    return None


def fetch_patch(commit_url: str, get=fetch_bytes) -> str:
    url = patch_url(commit_url)
    if not url:
        raise ValueError(f"no .patch form known for {commit_url!r}")
    return get(url).decode("utf-8", "replace")


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
