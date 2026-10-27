# Contributing to PengyOS

Thanks for looking. PengyOS is a **human-led, AI-assisted** project: a person
sets the direction and signs the boundary; agents draft, build, test and
document. Contributions from people *and* from agents are welcome — the rules
below are the same for both. That is a deliberate position, and the reasoning for
it — and how we behave when contributing into someone else's project — is written
down in [`docs/ai-contribution-stance.md`](docs/ai-contribution-stance.md).

## Attribution

Disclose how the work was made. It costs nothing, it is what the wider ecosystem
now expects, and it is the only posture consistent with being read again next year.

- **In this repository:** an agent-authored change is recorded as such — in the
  commit message, and in the tool's `CHANGELOG.md` where the change is notable.
  Where a person reviewed and takes responsibility, say who.
- **Outward, into upstream projects:** use *that project's* convention exactly as
  it defines it. The Linux kernel requires an `Assisted-by:` tag naming the model,
  agent and auxiliary tools (`Assisted-by: Claude:claude-3-opus coccinelle sparse`)
  and forbids AI agents adding `Signed-off-by` — the DCO is a human certification.
  Where a project has no convention, disclose in the patch description anyway.
- **A person signs every outward submission.** Agent accounts do not open pull
  requests into other people's projects. The human who submits carries the
  liability, upstream's way as well as ours.
- **If a project's policy says no, we respect it** and carry the fix as a declared
  divergence in [`divergence.toml`](divergence.toml) with an expiry condition. We do
  not hide authorship and we do not route around a project's rules.

Before anything else, read:

- [`docs/principles.md`](docs/principles.md) — why the project exists, and its
  principles.
- [`README.md`](README.md) — the front page: what the editions are, how to try them,
  and what isn't ready yet.
- [`SPEC.md`](SPEC.md) — what is being built (the component stack).
- [`USE-CASES.md`](USE-CASES.md) — for whom; the user stories whose acceptance
  criteria become our tests.
- [`docs/qemu-testing.md`](docs/qemu-testing.md) — how to build, boot and
  inspect an image.

## The three rules that matter

1. **Stories before code.** If you cannot point at a user story and an acceptance
   criterion your change serves, you probably shouldn't build it yet
   (`USE-CASES.md` governs when it disagrees with the spec).
2. **Verifiable by construction.** A change is "done" when it is proven by a
   build/test/boot you can reproduce — not when it looks right. Every tool ships
   a pinned verifier; every image ships an SBOM and attestation.
3. **The human signs the boundary.** Nothing that crosses the security boundary —
   release signing, update acceptance, privilege change — is automated without a
   person's sign-off.

## Repository conventions

This repository is a **monorepo**. There are two kinds of subdirectory:

- **`tools/<id>/`** — one bounded tool or authored component per directory. Each
  follows the *tool contract*: a `README.md`, `LICENSE`, `CHANGELOG.md`,
  `spec.md`, a pinned `verify.toml`, and a real test suite. See
  [`tools/README.md`](tools/README.md).
- **`prototypes/<id>/`** — measured evaluations that are not shipped, kept in the
  tool shape but outside the gate (`check_toolkit_suite` walks `tools/` only).
- **Structural directories** — `buildroot-external/` (our Buildroot tree),
  `docs/`, `scripts/`. Each has its own `README.md`.

For a new tool, **write the `spec.md` first**, at the quality of a strong model
(prescriptive, with acceptance criteria and one small subtask per context
window). The implementer only executes one subtask at a time — a vague spec gets
vague code.

## Versioning

PengyOS images use calendar versions, **`YY.MM.N`**: `26.09` is the release
line (year and month), `N` counts the images published that month, from 0.
The single source is [`VERSION`](VERSION) at the repository root; bump it
when publishing an image (a new month resets `N` to 0). From it, each
edition's post-build writes os-release (`board/common/os-release.sh`):

| Field | Example | Meaning |
|---|---|---|
| `VERSION_ID` | `26.09` | the release line |
| `IMAGE_VERSION` | `26.09.3` | this image |
| `IMAGE_ID` | `pengyos-desktop-nvidia` | which image: update feeds match on it |
| `BUILD_ID` | `09af82d6df` | the git commit (`-dirty`: uncommitted changes) |
| `PRETTY_NAME` | `PengyOS Server 26.09` | shown by the console banner, `hostnamectl` |

**A patch build ships only the editions it changes; a new month ships all of
them.** A Desktop-only fix (COSMIC, audio) is published for the Desktops alone,
so Server machines don't reboot into a new slot that changes nothing. The first
release of each month (`YY.MM.0`) is published for every edition, so all five
always share a release line. People compare the line: the download page,
`PRETTY_NAME` and the console banner lead with `26.10`, and the build
(`26.10.7`) is small print.

The installer stick's payload and `pengyos-release` use the same `YY.MM.N`
(it sorts as dotted integers). Tools under `tools/` keep their own semantic
versions.

## Releasing

Signing a release and putting it in front of machines is a human decision
(see `SECURITY.md`). The signing key never lives in this repository or on a
test machine. Images are published to the **testing** channel first, then
promoted to **stable** once they have been upgraded on real hardware.

1. **Bump and commit.** Set `VERSION`, retitle the `[Unreleased]` headings in
   `CHANGELOG.md` with it (`### Fixed (26.10.7)`), and commit. Images take
   `IMAGE_VERSION` from `VERSION` and `BUILD_ID` from the commit; don't
   publish a `-dirty` build (`release.sh --publish` refuses one).

2. **Refresh what Buildroot won't notice.** A build tree that already exists
   doesn't notice some changes, and will quietly ship the old result. After
   `pengyos-build configure` and before `build`, in
   `~/build/pengyos/output/<target>`:

   | Changed since that tree last built | Run (with `env -u LD_LIBRARY_PATH make …`) |
   |---|---|
   | a `linux.fragment` | `linux-reconfigure`, then `nvidia-open-rebuild` on the NVIDIA targets |
   | `BR2_TARGET_GRUB2_BUILTIN_MODULES_EFI` | `grub2-rebuild` |
   | a file in `package/<name>/` other than the `.mk` (a config file, a theme) | `<name>-reinstall` |
   | a recipe's **version** (`<PKG>_VERSION`) | `<name>-dirclean`, then build — see below |
   | a host-copied library replaced by a recipe | delete the copy from `target/usr/lib`, then build |

   > **A version bump leaves the previous version installed.** Buildroot installs the new
   > version's files and never removes the old one's, so `target/` ends up holding both —
   > measured 2026-10-08 bumping `nvidia-open` 595.104.02 → 610.57.04: **543 MB** of the
   > old driver still in the Desktop NVIDIA target, and an SBOM naming two versions.
   > **Deleting those files by hand makes it worse:** `.stamp_installed` still says the
   > package is done, so the next build re-links the image from a target that no longer
   > has them — 25 dangling SONAME symlinks and an image with no driver in it at all,
   > while every boot check stayed green. `<name>-dirclean` is what clears the stamps.
   > A green boot does not catch this: QEMU has no GPU to load, so a missing driver is
   > invisible. Check `target/` for the version you actually asked for.

   Rebuilding a tree from scratch avoids all of this.

3. **Build and check.** `scripts/release.sh [TARGET …]` (all five images by
   default) acquires the pinned Buildroot tree, configures, builds, writes the
   SBOM, and boots each image under QEMU: the smoke matrix for Core and Server,
   and a login plus `pengyos-qemu-verify desktop` for the Desktops. It
   acquires, configures and builds in one go, so when step 2 applies, run
   `pengyos-build acquire`, then `pengyos-build configure`, then step 2, then
   `release.sh`. Boot the image on the hardware it is for, too (see
   `docs/qemu-testing.md` §6).

4. **Sign and publish to testing**, from `tools/pengyos-release`:

   ```sh
   # once per A/B edition: Desktop, Desktop NVIDIA, Server, Server NVIDIA
   python3 -m pengyos_release update-publish --build ~/build/pengyos/output/<target> \
     --site ~/build/pengyos/site --key "$PENGYOS_RELEASE_KEY" \
     --channel testing --notes @notes.md
   # once, with a --build for every image (all the same VERSION)
   python3 -m pengyos_release download-publish --build … --build … \
     --site ~/build/pengyos/site --key "$PENGYOS_RELEASE_KEY"
   rsync -a ~/build/pengyos/site/ "$PENGYOS_SITE_REMOTE"
   ```

   `scripts/release.sh --publish` (sign into the site dir) or `--push` (and
   rsync it) does the same after its checks. The download images are staged,
   not offered: `download/index.html` lists only releases on stable. Images built with
   `PENGYOS_AUTHORIZED_KEYS_FILE` set must not be published.

5. **Promote** once a real machine on testing has upgraded and confirmed the
   new slot:

   ```sh
   python3 -m pengyos_release update-promote --site ~/build/pengyos/site \
     --image <IMAGE_ID> --release <YY.MM.N> --channel stable
   ```

   This also regenerates the download page, so the release's install images
   become the ones new users get. Then rsync again, and prune releases you no longer want public from both
   the site and the remote (`rsync` without `--delete` brings pruned
   releases back).

## Setting up a development machine

The verified build host is Ubuntu 26.04 on x86_64 with KVM and ~16 GB RAM:

```sh
scripts/provision-build-host.sh [--desktop]   # idempotent: installs host prerequisites
scripts/build-cosmic.sh                       # Desktop only: COSMIC + our libcosmic apps

cd tools/pengyos-build                        # then, per target, in this order:
python3 -m pengyos_build acquire   <target> --targets ../../targets.toml   # clone + pin Buildroot
python3 -m pengyos_build configure <target> --targets ../../targets.toml
python3 -m pengyos_build build     <target> --targets ../../targets.toml
```

`acquire` is the only step that touches the network, and it is idempotent; the
tree lives in `$PENGYOS_BUILD_ROOT` (default `~/build/pengyos`), never in the
repo. `configure` and `build` never auto-acquire — they stop and tell you to.

Then boot what you built, headless, and assert it reaches a login prompt:

```sh
cd tools/pengyos-qemu-verify
python3 -m pengyos_qemu_verify boot \
    ~/build/pengyos/output/pengyos-core/images/pengyos-core-x86_64.img --uefi
python3 -m pengyos_qemu_verify smoke          # Core by default; --matrix smoke/server.toml for Server
```

To run it on real hardware, write the image to a stick. `scripts/flash-usb.sh` does
the post-`dd` GPT repair (`sgdisk -e`) that a plain `dd` leaves broken, so prefer it
over typing the `dd` yourself:

```sh
sudo scripts/flash-usb.sh ~/build/pengyos/output/pengyos-core/images/pengyos-core-x86_64.img /dev/sdX
```

`docs/qemu-testing.md` is the fuller runbook: SSH into a booted guest, the serial
console, testing the installer, and screenshotting the Desktop in a VM.

Per-tool development needs only the tool's own toolchain (Python ≥ 3.11, or a
recent Rust, or cmake for the C++ tools). Python tools use PEP 723 / `uv` or a
plain `pytest`; see each tool's README.

## Before you open a pull request

1. **Run the tool's own suite.**
   ```sh
   cd tools/<id> && python3 -m pytest -q      # or: cargo test
   ```
2. **Run the repo-level integration gate.** A tool's own suite is
   self-consistent and can be green while the *integration* is broken, so the
   gate runs an external check over the tree:
   ```sh
   python3 scripts/pengyos_gate.py run --repo . --mode full
   ```
   If your change touches an **image** or a **board**, also build it and boot it —
   the runbook (headless boot, SSH, the serial console, and screenshotting the
   Desktop) is [`docs/qemu-testing.md`](docs/qemu-testing.md).
3. **Keep it small and atomic.** One logical change per commit; revert is a
   feature. Write commit messages that say *why*, not just *what*.
4. **Update the docs you invalidate.** If you change a directory's purpose, its
   `README.md` changes too. If you make a decision a future maintainer would
   otherwise re-litigate, record it (an ADR under that tool's `docs/`, or the
   relevant `docs/` file).

## Licensing

By contributing you agree that your contribution is licensed under the
project's [MIT License](LICENSE). We prefer permissive licenses for components
(MIT / Apache-2.0); where a component must be GPL/LGPL or a functional-necessity
blob, the exception is **documented explicitly** in `SPEC.md` — never hidden.
