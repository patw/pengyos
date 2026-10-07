# pengyos-witness-bundle

Present a `pengyos-build` output in the **canonical witness layout** and verify its
SBOM signature, so an external witness can actually see what was built — closing the
"witness can't find the artifact" gap.

`pengyos-build` writes its SBOM to `images/<target>.spdx.json`, but the external
verifier (the witness) looks for conventional names under `<build>/.witness/` and
expects an artifact signature and a repro report alongside it. This tool normalises
a build output into that layout and, when given a public key, verifies the SBOM
signature.

## Install

```bash
pip install -e .            # stdlib-only at runtime
pip install -e ".[dev]"     # + pytest
```

## Usage

```bash
# Print the canonical witness artifact layout this tool produces.
pengyos-witness-bundle layout
pengyos-witness-bundle layout --json

# Normalise a build output into the witness layout and verify the SBOM signature.
pengyos-witness-bundle run --build <M2_DIR> --pub <key.pub>
pengyos-witness-bundle run --build <M2_DIR> --json            # machine-readable result
pengyos-witness-bundle run --build <M2_DIR> --json out.json  # write the result to a file
pengyos-witness-bundle run --build <M2_DIR> --pub <key.pub> --link  # symlink the view

`run` materialises the canonical layout under `<build>/.witness/` by copying each
discovered artifact (default) or symlinking it (`--link`, a view of the build rather
than a duplicate — handy for the ~12 MB M2 SBOM). It then verifies the SBOM
signature when `--pub` is given, and exits `0` only when all required artifacts exist
and — if a key was given — the signature verifies. A tampered SBOM is a hard fail
(non-zero exit, reported on stderr). `--json` with no argument prints the result to
stdout; with a path it writes it there.

### Canonical layout

Whatever names `pengyos-build` actually emits are normalised to these conventional
names under `<build>/.witness/`:

| kind              | conventional name    |
| ----------------- | -------------------- |
| `sbom`            | `sbom.spdx.json`     |
| `cyclonedx`       | `sbom.cyclonedx.json`|
| `sbom_signature`  | `sbom.spdx.json.sig` |
| `repro_report`    | `repro-report.txt`   |

The authoritative, alias-aware definition (mapping pengyos-build's real output names
to these canonical names) is the data layer this tool is built around.

## Development

```bash
pytest -q            # run the suite
PENGYOS_IT=1 pytest  # opt-in integration tests (needs a real build dir)
```

Tests need no network, root, display, or hardware.

## License

MIT — Copyright (c) 2026 Pat Wendorf. See `LICENSE`.
