# Stack-down replace — the replacement ladder

**Status:** decision recorded, 2026-10-06. Operator direction; written by Pengy
from the discussion of 2026-10-06 and the founding feasibility read of 2026-09-12.
**Scope:** which components PengyOS may replace with our own versions, in what
order, and what a replacement must satisfy before it ships.

This document is the durable home of the project's long-term direction. It was
previously recorded only as agent memory (BotTalk `a39bf630110358a93e010000`,
2026-09-12), which contradicts Principle 10 — *continuity lives in artifacts*.
That post is now marked superseded by this file.

---

## The direction

PengyOS starts from a stable, current, Buildroot-based baseline and **replaces
components working down the stack** as two things both allow it: agent capability,
and — the binding one — our ability to still *verify* the result.

The ladder is banded by **verifiability, not difficulty**. Difficulty is what an
agent can write; verifiability is what this project can still prove. Those are not
the same line, and the project's claims (reproducible, attested, witnessed) are
broken by crossing the second one even when the first is easy.

| Band | Examples | What makes it admissible today |
|---|---|---|
| **1 — Additive** | COSMIC applets, curated apps, theme, boot experience, OOB defaults | Proven. Already shipped (`pengy-panel`, `pengy-theme`, curation). No existing component is displaced |
| **2 — Fork upstream** | COSMIC Settings additions, libcosmic patches, `board/desktop/cosmic-patches/` | Boots, reaches a session, passes the smoke matrix and the Desktop screenshot check; patches written upstreamable; ledger entry (§ Divergence budget) |
| **3 — Replace a userland component** | coreutils → uutils, dbus → dbus-broker, tar/ps/top-class CLIs | Lane-1 recipe (not host injection), reverse dependencies resolved, **our own observe source**, repro still green, ledger entry |
| **4 — Replace a contract-bearing subsystem** | init/PID 1, C library, display server | Requires a **new verification layer designed before the replacement**, not after. Research track, not roadmap |
| **5 — Not on the list** | Linux kernel, NVIDIA driver blobs, Wayland protocol, a browser | Excluded by verifiability, not by capability. No upstream CVE mapping, no byte-identity, no smoke matrix that covers what we broke |

Band 5 is not a judgement about what an agent can write. It is a judgement about
what can be *checked* against anything outside ourselves.

**The governing rule, unchanged since 2026-09-12:** reimplement **behind** stable
interfaces. Filesystem hierarchy and POSIX, ELF and dynamic linking, systemd unit
semantics, the D-Bus wire, Wayland protocols, freedesktop specs. A component may be
replaced; its public contract may not (Principle 3). SPEC §5 states the same idea
as *replace interiors, not interfaces* — systemd is "kept for the contract, not
loved."

---

## Why the ladder is real, not aspirational

Buildroot supports component replacement from a `BR2_EXTERNAL` tree, and documents
it doing exactly that — including replacing the init system:

- `docs/manual/customize-outside-br.adoc` (Buildroot 2026.08) shows a br2-external
  tree providing `jpeg`, a custom **init** (`provides/init.in`, `BR2_INIT_MINE`) and
  a custom **skeleton** (`SKELETON_MINE_PROVIDES = skeleton`).
- Mechanism: `provides/<name>.in` declaring `config BR2_PACKAGE_PROVIDES_<NAME>`,
  plus `<PKG>_PROVIDES = <virtual>` in the `.mk`. `package/pkg-generic.mk:406-408`
  hard-errors if two providers are selected for one virtual.
- Tutorial: `docs/manual/adding-packages-virtual.adoc`. Live examples in 2026.08:
  `LIBOPENSSL_PROVIDES = openssl`, `LIBGLVND_PROVIDES += libgl`. 22 virtual
  provider symbols exist (`JPEG, LIBGL, LIBEGL, LIBGLES, LIBUDEV, UDEV, OPENSSL,
  ZLIB, NODEJS, LUAINTERPRETER, GETTEXT, SKELETON, TOOLCHAIN_EXTERNAL`, …).

**The constraint that is often overstated.** What was proven by a build on
2026-10-05 (`tools/pengyos-nightly/pengyos_nightly/edits.py`) is narrower than it
reads: you may not **shadow** a stock package under its own name (`"Package 'X'
defined a second time"`), and you may not **bump** a stock package's version from
`external.mk` (the stamp path is fixed when the stock recipe is parsed;
`include package/Makefile.in` at Makefile:526 runs before
`include $(BR2_EXTERNAL_FILE)` at Makefile:559). Neither forbids **replacing** it.
For a package that is *not* virtual — `coreutils` is not — replacement means:
disable the stock one in Kconfig, author ours under its own name, and resolve every
reverse dependency by hand. That is bookkeeping cost, not a Buildroot refusal.

---

## The admissibility test

A replacement is admissible only if **all five** hold. This is the gate, not advice.

1. **Lane 1.** It ships as a Buildroot recipe in `buildroot-external/package/`,
   built from pinned source by Buildroot. Host-built injection is not a
   replacement, it is a smuggling route (see *The lesson already paid for*).
2. **Provable.** `pengyos-qemu-verify` boots it, the smoke matrix passes, and for
   anything session-facing the Desktop screenshot check runs. A replacement we
   cannot boot-test is not a replacement, it is an unmeasured change.
3. **Observed.** It gets its own upstream-watch source in `pengyos-nightly`.
   pkg-stats only sees packages whose recipe it reads with a CPE it can match; a
   component we fork leaves that field of view immediately. Precedent: PengyR is an
   authored pin, so pkg-stats never saw it, and images shipped 1.10.0 while upstream
   was at 1.11.1 with nothing noticing (`sources.py:pengyr_findings` exists to fix
   exactly this). **No fork ships without an observe source.** That is part of done.
4. **Ledgered.** An entry in the divergence ledger (§ below) before it lands, not
   after.
5. **Repro survives.** Core and Server byte-identity across two hosts (26.10.3) is
   the project's strongest claim. A replacement that silently voids it voids the
   claim, and must say so in `SECURITY.md` and `SPEC.md` before it ships.

---

## Divergence budget and fork policy

Principle 6 has always allowed a maintained fork — *"conscious, documented, and
time-bounded, never accidental drift"* — but never defined the terms. It now has
them.

**The budget.** PengyOS carries at most **10 maintained forks** (a fork = a
component we build from our own tree or our own pinned source with our own changes,
outside a stock Buildroot recipe) at any time. Current count: **0**. Host-built
COSMIC is *not* a fork under this definition — it is an unbuilt dependency, and it
is the thing Band 2/3 work should convert into recipes.

Rules:

- The budget is a **hard gate**, not a target. At the cap, `pengyos-nightly` and
  the propose gate refuse new forks until one is paid down.
- **Pay down** means one of: the change is accepted upstream; the fork converges to
  stock (our changes become unnecessary because upstream moved); or the fork is
  retired and we return to the stock component.
- Every fork carries an **expiry condition** written at creation: the specific
  upstream event that ends it. A fork with no expiry condition is not approved.
- A fork crossing **12 months** without upstream progress or a convergence plan is
  escalated to the operator as a decision: fund it, retire it, or replace it.
- **Forks are Tier-2.** Creating one changes what the image is made of, so it is a
  human sign-off, not a nightly action. Patching a pinned version stays Tier-1
  under the existing nightly guards; *forking* does not.

**The ledger.** `divergence.toml` at the repository root, one entry per carried
divergence (fork or patch set), machine-readable so `pengyos-nightly`,
`pengyos-recipe-lint` and `pengyos-fleet-inventory` can all read the same fact:

```toml
[[divergence]]
id       = "libxml2-2.15.3-cve-2026"
kind     = "patch-set"            # patch-set | fork | host-built
component = "libxml2"
pinned_version = "2.15.3"
closes   = ["CVE-2026-86137", "CVE-2026-86138", "…"]
upstream_fixes = ["76fe08d97de8", "a4cba4b5b5a8", "…"]
carried_since  = "2026-10-05"
carried_in     = ["26.10.8"]
natural_fix    = "2.15.4"         # the version that would have closed it
expiry         = "Buildroot 2026.08.1 or newer pins libxml2 >= 2.15.4"
observe_source = "pkg-stats"      # stock: visible; fork: needs its own source
upstream_status = "not-offered"
```

The ledger is what turns *"the version number looks wrong"* into *"the version
number looks wrong, and here is the receipt."* It is also the artifact that makes
the budget enforceable rather than rhetorical.

---

## Patches are an emergency lane, not the default

A patch on a pinned stock version is the worst kind of divergence: it does not
change the version, so **every version-based tool in the chain misreads it** —
NVD ranges, Buildroot's `pkg-stats`, CPE matching, and any external scanner.

Measured on 2026-10-06, not inferred:

- `pkg-stats` counts patches only from a package's own directory
  (`set_patch_count()`, `support/scripts/pkg-stats:250`). Patches delivered through
  `BR2_GLOBAL_PATCH_DIR` — the mechanism PengyOS uses
  (`configs/layers/core.config:228`) — are **invisible to it**.
- In our own `pkgstats-pengyos-desktop.json`, libxml2 reads `ignored_cves: []`,
  `status.cve = error "affected by CVEs"`, `status.version = error "The newer
  version 2.15.4 is available upstream"`.
- `pengyos-sbom` emits no vulnerability data at all.
- Buildroot grades patch count itself: 0 = ok, <5 = warning, ≥5 = **error "lots of
  patches"**. Upstream treats a patch-heavy package as a defect.

Buildroot's own declaration channel is `<pkg>_IGNORE_CVES`
(`docs/manual/adding-packages-generic.adoc:509`): *"typically used when the CVE is
fixed by a patch in the package"*, with a Makefile comment naming the patch required
above every entry. `pkg-stats` reads it via `make printvars VARS=%_IGNORE_CVES`,
suppresses the CVE, and reports `stale_cve_ignores` when the version no longer
matches NVD's range — a built-in expiry signal. Verified 2026-10-06 that it works
for a **stock** package from a br2-external tree.

Therefore: **named forks over anonymous patches.** The patch lane stays for the
case where a fix exists and no bump is reachable, and every use of it must produce
an `IGNORE_CVES` entry, a ledger row, and an expiry condition in the same change.

---

## The lesson already paid for

COSMIC is a stack-down replace that was performed as **host-built injection**
(`scripts/build-cosmic.sh`, `board/desktop/post-build.sh`) rather than a Lane-1
recipe, and it cost the Desktop its reproducibility claim — stated plainly in
`SECURITY.md` ("The Desktop is not yet fully reproducible") and open as SPEC §6.3.

That is the tuition for this whole document, already paid. Every future replacement
is measured against it: **Lane 1 or it does not ship.**

---

## What this does not change

- **Principle 7 stays.** Latest stable, pinned, rebase discipline. Band-3
  replacements do not get to become old.
- **Principle 9 stays.** Creating a fork is a human sign-off.
- **Principle 8 stays.** Theme, boot experience, curation, naming remain
  human-led. Band 1 is where the agent implements taste, not where it supplies it.
- **The kernel and a browser remain non-goals** (README, *Failure modes*). Band 5
  is not a backlog.

## Triggers that would reopen a band

- A verification capability we do not have today (e.g. a kernel-level test harness
  with real coverage, or an independent witness that can check a forked subsystem).
- Upstream abandoning a component we depend on — the reason forks may come sooner
  than planned is upstream pullback on AI-assisted contributions, not our own
  ambition. See `docs/ai-contribution-stance.md`.
- A hardware or contract change that makes a stock component unusable for us.
