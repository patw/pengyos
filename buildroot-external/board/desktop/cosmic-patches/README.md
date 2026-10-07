# `cosmic-patches/` — fixes to the host-built COSMIC components

The COSMIC compositor, shell and apps are still built with cargo on the build
host (`scripts/build-cosmic.sh`: `$PENGYOS_COSMIC_SRC/<component>` ->
`$PENGYOS_COSMIC_BUILD/release`) and copied into the image by `../post-build.sh`,
until their Buildroot recipes land. Fixes we carry
on top of upstream live here, one directory per component, as `git am`-style
patches, so they are reviewed and versioned with the tree rather than living
only in a checkout.

`scripts/build-cosmic.sh` applies them after checking out each component's pinned
commit (and skips a patch that is already applied). By hand:

```sh
cd "$PENGYOS_COSMIC_SRC/cosmic-notifications"
git apply <repo>/buildroot-external/board/desktop/cosmic-patches/cosmic-notifications/*.patch
```

| Component | Patch | Why |
|---|---|---|
| `cosmic-notifications` | `0001-applet-wait-longer-for-the-panel-connection.patch` | The daemon gave the panel 1 s to connect; a slow-starting panel lost its notifications applet. |
| `cosmic-applets` | `0001-battery-no-battery-icon-on-machines-without-a-system-battery.patch` | A desktop with a wireless mouse/keyboard showed a 0% battery icon: the applet took later UPower updates after "no battery". Backport of cosmic-settings a366fc7 (epoch-1.9). |

When the COSMIC recipes move into `package/`, these become that package's
patches.
