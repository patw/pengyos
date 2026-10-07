# pengyos-release

Publish a **signed, versioned PengyOS release** to a local update feed.

Buildroot
ships no package manager, so the *release is the unit of change*: a signed,
static directory is the whole "update server" for local testing. This tool turns
a `pengyos-build` output into that release and keeps the feed's signed index and
channel pointers up to date.

## Install

```bash
pip install -e .
# or, for the dev/test extras:
pip install -e ".[dev]"
```

## Usage

```bash
# Publish a build output as a release under <feed>/pengyos/<edition>/<version>/
# Collects the rootfs + UKI + SBOM + attestation + repro report, deterministically
# compresses the rootfs, signs every artifact, and writes a signed manifest.json.
# --key is optional: omit it to sign with a deterministic local stub, or pass a
# raw key / path to a key file for a keyed HMAC feed.
pengyos-release publish --build DIR --edition server|core|desktop --version V \
    --feed DIR [--channel stable] [--key KEY] [--json]

# Regenerate + verify the signed index and the stable/testing channel pointers
pengyos-release index --feed DIR [--json]

# Verify a feed entry (or whole feed) is well-formed, hashed, and signed
pengyos-release verify --feed DIR [--edition E] [--version V] [--json]

# Prune old versions per policy (newest N per edition; dry-run by default)
pengyos-release retain --feed DIR --keep N          # dry run: report what would be pruned
pengyos-release retain --feed DIR --keep N --apply  # actually prune + re-sign the index
```

## Feed layout

```
<feed>/index.json                     # signed table of contents + channel pointers
<feed>/channels/<name>                # plain-text channel pointer (version)
<feed>/pengyos/<edition>/<version>/manifest.json   # signed release manifest
```

`index.json` is the single signed artifact both install and update consume; the
`channels/<name>` files are a human/greppable mirror of its pointers.

## Concepts

- **Deterministic** — manifests and the index are canonicalised (sorted keys,
  UTF-8) so the bytes are reproducible across machines and runs.
- **Injectable signing** — signing is an interface (`sign`/`verify`). The
  concrete implementation is HMAC-SHA256; tests use a stub. Swap in a real
  signer for production.
- **Safe retention** — `retain` keeps the newest N versions per edition and
  never prunes the version a channel currently points at (dry run by default;
  `--apply` to actually prune + re-sign the index).
- **Index recovery** — `index` rebuilds the signed index + channel pointers from
  the on-disk manifests and the durable `channels/<name>` mirror, so a missing or
  corrupt `index.json` is regenerated as long as the pointers survive.
- **Verify** — `verify` re-reads every stored artifact and re-checks its sha256
  plus every signature, and flags a channel pointer that dangles (points at a
  version with no manifest). A stub-published feed verifies with no key; pass
  `--key` for a keyed HMAC feed.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
