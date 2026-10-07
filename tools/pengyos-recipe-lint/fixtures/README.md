# Fixtures: good and bad recipes

Committed example recipes used by the test suite (and as a demo for the
`pengyos-recipe-lint` CLI). They exercise the four implemented rules:

1. **version_pinned** — an explicit, non-auto `_VERSION`.
2. **hash_present** — a `.hash` sibling carrying a `sha256` for the source.
3. **license_declared** — a non-empty `_LICENSE`.
4. **no_unverified_downloads** — the download site (`_SITE`) is a pinned
   `https` mirror (an `http://` or non-`https` site fails).

## Layout

```
fixtures/
  good/   fully-compliant recipes: every rule passes
    uutils.mk            (+ uutils.hash)
    dbus-broker.mk       (+ dbus-broker.hash)
    signify.mk           (+ signify.hash)   # a PengyOS-branded (PENGYOS_*) recipe
  bad/    recipes that each break exactly one rule (plus one that breaks all)
    unpinned_version.mk  (+ .hash)   fails: version_pinned        (_VERSION = HEAD)
    missing_hash.mk                fails: hash_present             (no .hash sibling)
    missing_license.mk   (+ .hash)  fails: license_declared         (no _LICENSE)
    wrong_source_hash.mk (+ .hash)  fails: hash_present             (sha256 is for another file)
    unverified_site.mk   (+ .hash)  fails: no_unverified_downloads (_SITE = http://, not https)
    broken_all.mk                fails: version_pinned, hash_present, license_declared
```

Every `bad` recipe is otherwise well-formed, so it fails **exactly** the one
rule named above (the four rules are independent). `broken_all` is the one
exception: it is missing everything, so it fails three rules. Note the `bad`
recipes use `https://example.com/...` for their `_SITE`, which *passes* the
`no_unverified_downloads` rule — so it never accidentally flags them.

The digests in the `.hash` files are realistic 64-hex sha256 values; the linter
checks only that a `sha256` line exists and its filename matches the recipe's
`_SOURCE`, so the digests are not the real upstream hashes.
