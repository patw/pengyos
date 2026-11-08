# Third-party code

## HWall (collectors and presentation policy)

- Upstream: <https://github.com/pulpul-s/HWall>
- Pinned: commit `500cc4599254061bab93d116f9b30e0e6529fd4e` (v1.0.5)
- Licence: MIT, Copyright (c) 2026 pulpul-s (see `LICENSE.hwall`)
- Used as a cargo git dependency for `hwall-core` and `hwall-app`, unmodified.

This app is a new front-end, not a fork: HWall's GTK crate is not used at all,
and neither `hwall-core` nor `hwall-app` is patched. `pengyos-hwmon` adds the
libcosmic view (`src/app.rs`), the TJ Max policy (`src/tjmax.rs`) and the
headless `--check` mode.

## libcosmic

- Upstream: <https://github.com/pop-os/libcosmic>, MPL-2.0
- Pinned to the same rev as the other in-tree PengyOS apps; not vendored.
