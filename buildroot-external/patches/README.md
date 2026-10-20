# `patches/` — carried upstream fixes

Patches here are **upstream fix commits we carry on a pinned version** because no
bump is reachable from a `BR2_EXTERNAL` tree (see
[`tools/pengyos-nightly/pengyos_nightly/edits.py`](../../tools/pengyos-nightly/pengyos_nightly/edits.py)
for the build that proved the constraint).

Layout, as reached by `BR2_GLOBAL_PATCH_DIR`
(`configs/layers/core.config`):

```
patches/<package>/<version>/<NNNN>-<short-description>.patch
```

## The rules

1. **Upstream commits only.** A carried patch is a real upstream fix commit, taken
   from the commit NVD names for the CVE. We do not write our own fixes to other
   people's packages here; our own fixes belong in a recipe we author.
2. **Every divergence has a ledger entry.** The rationale, the CVEs closed, the
   upstream commit ids, the version that would have closed it naturally, and the
   **expiry condition** live in [`divergence.toml`](../../divergence.toml) at the
   repository root. A patch with no ledger entry is not approved.
3. **Declare it to the tooling.** A carried patch must also be declared with
   `<PKG>_IGNORE_CVES`, each entry preceded by a comment naming the patch file —
   Buildroot's own convention (`docs/manual/adding-packages-generic.adoc`). Without it,
   `pkg-stats` cannot see the patch at all: `set_patch_count()` walks only the package's
   own directory, so a patch delivered through the global patch dir leaves the package
   reporting *"affected by CVEs"* forever. With it, `pkg-stats` suppresses the CVE **and**
   reports a *stale CVE ignore* once the version moves — which is the expiry signal doing
   its job. The loop writes its own declarations to
   [`patches/cve-declarations.mk`](cve-declarations.mk), included from `external.mk`, from
   the patches that actually survived pre-flight; a hand-written override can still live in
   `external.mk` and the loop will not duplicate it. This is enforced, not advised:
   `scripts/pengyos_gate.py` compares every patch set in `divergence.toml` against the
   declarations in the tree and fails on a missing one, on an empty patch directory, and on
   a declaration the ledger does not back.
4. **Patches are an emergency lane.** They exist for the case where a fix exists and
   no bump is reachable. The preferred shape for anything longer-lived is a named
   fork with a ledger entry and a pay-down plan
   ([`docs/stack-down-replace.md`](../../docs/stack-down-replace.md)).

## Currently carried

Regenerated from `divergence.toml` against the tree on 2026-10-19. The table listed
only libxml2 for a while, which was stale: five more sets were carried and none of
them appeared here. If you add a patch set, this table and the ledger move together —
`scripts/pengyos_gate.py:check_ledger_covers_patch_sets` fails a patch directory with
no ledger entry, so the ledger cannot drift from the tree; this table is the human
copy of it.

| Package | Version | Patches | Ledger entry | Closes |
|---|---|---|---|---|
| libxml2 | 2.15.3 | 7 | `libxml2-2.15.3-cve-2026` | CVE-2026-86137, -86138, -86139, -86140, -86142, -86143, -86144 |
| gst1-plugins-good | 1.24.13 | 6 | `gst1-plugins-good-1.24.13-cve-2026-73433` | CVE-2026-73433, CVE-2026-73434 (avidemux; 4 of the 6 are the upstream series the two fixes sit in) |
| gst1-plugins-bad | 1.24.13 | 1 | `gst1-plugins-bad-1.24.13-cve-2026-2923` | CVE-2026-2923 (attributed here from gstreamer1 by the fix's own paths) |
| gst1-libav-pengyos | 1.24.13 | 1 | `gst1-libav-pengyos-1.24.13-ffmpeg9` | nothing — a build fix that follows from the ffmpeg 9 fork, not a CVE |
| podman | v5.7.0 | 1 | `podman-v5.7.0-cve-2026-55686` | CVE-2026-55686 |
| upower | 0.99.19 | 1 | `upower-0.99.19-capacity-level` | nothing — a functional fix, not a CVE |

Two of these are not CVE fixes, and that is allowed by rule 1 for the same reason the
ffmpeg and hplip fork patches are: they are carried on a recipe we author
(`gst1-libav-pengyos`) or on a package where the change is ours to make (`upower`).

Patches a recipe carries in its **own** directory are not listed here, because they
are not delivered through `BR2_GLOBAL_PATCH_DIR`. `package/hplip-pengyos/` carries four
(2 regenerated from Buildroot's, 2 authored here) and
`package/openssh-pengyos/` carries none.

Expiry for the libxml2 entry: a Buildroot point release (2026.08.1 or newer) that pins
libxml2 ≥ 2.15.4, at which point the patch set is deleted. Each other entry names its
own expiry in the ledger.
