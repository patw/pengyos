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
   `<PKG>_IGNORE_CVES` in `external.mk`, each entry preceded by a comment naming the
   patch file — Buildroot's own convention
   (`docs/manual/adding-packages-generic.adoc`). Without it, `pkg-stats` cannot see
   the patch at all: `set_patch_count()` walks only the package's own directory, so
   a patch delivered through the global patch dir leaves the package reporting
   *"affected by CVEs"* forever. With it, `pkg-stats` suppresses the CVE **and**
   reports a *stale CVE ignore* once the version moves — which is the expiry signal
   doing its job.
4. **Patches are an emergency lane.** They exist for the case where a fix exists and
   no bump is reachable. The preferred shape for anything longer-lived is a named
   fork with a ledger entry and a pay-down plan
   ([`docs/stack-down-replace.md`](../../docs/stack-down-replace.md)).

## Currently carried

| Package | Version | Patches | Ledger entry | Closes |
|---|---|---|---|---|
| libxml2 | 2.15.3 | 7 | `libxml2-2.15.3-cve-2026` | CVE-2026-86137, -86138, -86139, -86140, -86142, -86143, -86144 |

Expiry for that entry: a Buildroot point release (2026.08.1 or newer) that pins
libxml2 ≥ 2.15.4, at which point the patch set is deleted.
