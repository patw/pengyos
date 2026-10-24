# PengyOS — running the repo (the operations layer)

> Added 2026-10-19, from an audit of what the repo does **not** contain.
>
> **Who this is for.** Someone taking this repository to build **their own** operating
> system, with a human and an agent (Pengy, Claude Code, or equivalent) working on it.
> If you only want to *run* PengyOS, download an image and subscribe to its feed — you do
> not need any of this.
>
> **The premise.** The upstream signing key is not in this repo, is not for sale, and is
> not reproducible. Nothing you build here will ever be trusted by a machine that carries
> the upstream key. That is the design, not an oversight
> ([`../SECURITY.md`](../SECURITY.md), [`principles.md`](principles.md)). Your job is to
> become your own upstream: generate your own key, run your own feed, and sign your own
> releases. This document is the list of everything that has to exist **around** the repo
> for the repo to run itself.

---

## 0. The shape of the whole thing

The repo is a complete **build** system. It is deliberately **not** a complete
**operations** system, because almost everything in the operations layer is
machine-local by design: keys, mail setup, schedules, and site targets are not
portable, and putting them in the repo would put secrets in the repo.

```
   THE REPO (portable)                 OUTSIDE THE REPO (yours, per machine)
   ───────────────────────            ─────────────────────────────────────
   recipes, configs, overlays         a signing key            (generate)
   88 bounded tools                   a policy file            (copy the example)
   the integration gate               a schedule               (copy the example)
   build / release / sign / SBOM      a state directory        (create)
   specs, tests, this doc             a mailer                 (copy the example)
                                    a model gateway          (your agent's)
                                    a site host              (yours)
                                    secrets by name          (yours)
```

Every item on the right has a **contract** below: the interface the repo expects, not a
recipe for implementing it. Satisfy the contract and the tooling works.

**Placeholder conventions used throughout.** No real host, domain, path, or secret value
appears in this document.

| Placeholder | Means |
|---|---|
| `build-host.home.lab` | the machine that builds (SPEC §7) |
| `site.home.lab:/srv/os-site/` | your site host and document root |
| `https://os.example.net/` | your feed/download base URL |
| `~/keys/<your-keyid>` | where **your** release key lives |

---

## 1. The trust boundary — generate these, they are not in the repo

| You must create | Contract | Notes |
|---|---|---|
| **A release signing key pair** | `pengyos-sign` format; ed25519, keyid = first 4 bytes hex | Generate it. Never commit it. `chmod 600`. The public half is what your images trust. |
| **A key directory in your images** | `/usr/share/pengyos/keys/` | `pengyos-update` verifies a manifest against these **before** touching a slot. Ship *your* public key here; a machine only ever accepts releases signed by a key it carries. |
| **A feed URL** | `IMAGE_ID`-matched Atom feed | Set your own in `board/common/os-release.sh` (`HOME_URL`) and in the update config the image installs. The repo's defaults point at the upstream feed; change them or your machines will look for someone else's releases. |
| **A site host** | rsync target + a static file server | §8. |

**The rule that makes all of this safe:** a human signs the security boundary. The agent
may draft, build, verify, and — if you choose — sign into a *testing* channel. Promotion
to stable, key changes, and privilege changes are yours. `pengyos-release` has **no code
path to stable**; that is enforced in the tool, not in a policy file, so you cannot
accidentally automate past it.

---

## 2. Build host

Already handled; do not re-derive it. See **SPEC §7** and
`scripts/provision-build-host.sh` (`--desktop` for COSMIC's build dependencies). It installs
the apt packages, the rustup toolchain, checks `/dev/kvm`, and prints versions. The
verified host is Ubuntu 26.04; a Desktop build is only as reproducible as its host, and
SPEC §7 says so openly.

---

## 3. The schedule

Nothing in the repo schedules anything. The loops run from **your** crontab, and the
repo cannot tell you the schedule is missing — which is the single most dangerous gap in
this design (see §9).

The shape that works, and the reason for each half:

```
# Plan: observe upstream, triage CVEs, email a report, write plan.json.
# Read-only against the tree; safe to run while you are working.
0 19 * * * cd <repo>/tools/pengyos-nightly && python3 -m pengyos_nightly \
             --repo <repo> --build-root <build-root> --state-dir <state> \
             --site <site-dir> run            >> $HOME/logs/cron/pengyos-nightly.log 2>&1

# Act: apply the night's batch into the TESTING channel.
# Put this in a window where NOTHING ELSE touches the checkout.
0 2 * * *  cd <repo>/tools/pengyos-nightly && python3 -m pengyos_nightly \
             --repo <repo> --build-root <build-root> --state-dir <state> \
             --site <site-dir> act --apply    >> $HOME/logs/cron/pengyos-nightly-act.log 2>&1
```

**The lesson, learned expensively.** The act lane's first guard is `clean_tree`: it
refuses to run on a checkout with uncommitted changes, because on 2026-10-15 a refusal
ran `checkout -f main` and destroyed an agent job's uncommitted work. So **the act lane
is scheduled against your own working hours.** If you schedule it at 21:00, you will be
working at 21:00, the tree will be dirty, the guard will refuse, and the night is lost.
Pick a window where no agent job, no build, and no interactive session touches the
checkout. Verify the box does not suspend at that hour.

Two consequences worth knowing:
- The act run acts on a plan written hours earlier, on the **next calendar day**. That is
  fine: `release_cap` counts releases published *today*, so the previous evening's release
  does not block it.
- Approvals made after the plan is written still count: `act` re-plans when
  `decisions.json` is newer than `plan.json`. A wider plan→act gap gives you *more* time
  to answer, not less.

Copy `tools/pengyos-nightly/crontab.example` and edit.

---

## 4. The policy file

`<state-dir>/nightly.toml` is **machine-local policy** and is gitignored on purpose.
Every key has a default in `pengyos_nightly/config.py`; the file only states what differs.
Copy `tools/pengyos-nightly/nightly.toml.example`.

What actually matters:

| Key | Why you should think about it |
|---|---|
| `act_enabled` | **The kill switch.** `false` = the loop plans and reports, never acts. Set it false until you have watched a full dry run. |
| `max_actions` / `max_releases` | The batch is the unit of work; **one signed image is the unit of risk**. Keep `max_releases = 1`. Raising `max_actions` costs build time, not blast radius. |
| `cooldown_days` | Per component, consumed only by a *published* action. Too small and the loop re-attacks a component it just changed. |
| `lanes`, `priorities` | Which findings may trigger an action. `["security","routine"]` + `["P0","P1"]` is the conservative default. |
| `bump_buildroot` | Empty = the release-bump lane is inert. Naming a target is **you scheduling** a Buildroot series move; the loop never decides that from a tag. |
| `act_time` | Must match your crontab. The decision page prints it so you know how long an approval has to land. |
| `key_file`, `secrets` | The repo's compiled defaults are the **upstream author's** paths. Override both. |

---

## 5. The state directory — know which files are precious

`--state-dir` is where the loop lives between nights. It is not in the repo and must not
be. Split it by what it costs to lose:

| File | Regenerable? | If you lose it |
|---|---|---|
| `observations.json`, `pkgstats-*.json` | **Yes** — next run rebuilds | One slow run |
| `plan.json` | **Yes** — `plan` regenerates | Nothing |
| `reports/` | Yes | Lost history |
| `nightly.toml` | **No** — back it up | Your policy is gone |
| `decisions.json` | **No** | Your approvals/denials are gone; the loop re-asks |
| `actions.json` | **No** | Cooldowns and "what shipped" are gone |
| `state.json` → `patch_failures` | **No — the expensive one** | The accumulated record of *which upstream fixes do not apply to which package*. The loop rediscovers them one pre-flight per night, and a known-bad candidate can eat a whole night |
| `state.json` → `cves`, `findings` | Semi | The loop re-reports everything as new |
| `attest.jsonl` | **No** — append-only | Your evidence chain has a hole |
| `nvd/git` | Yes, but large | Multi-GB re-download |

Treat `nightly.toml`, `decisions.json`, `actions.json`, `state.json` and `attest.jsonl`
as data you back up. The rest is cache.

---

## 6. The mailer contract

`pengyos-nightly` sends mail through a script it finds at `$PENGYOS_EMAIL_SCRIPT`,
defaulting to `~/.local/bin/pengyos-email`. **No mail setup is in the repo, by design** —
the distro tooling carries nobody's mail configuration.

The contract, exactly:

```
<mailer> --quiet --html --subject "<subject line>" --body-file <file.html> [--to <addr>]
```

- Invoked via its **shebang**. If it is executable, the kernel reads the shebang; the
  tool only falls back to `python3 <mailer>` when it is not. A shell wrapper invoked as
  `python3` becomes a SyntaxError and takes the run down with it.
- Exit non-zero to signal failure. The tool treats a failed mailer as a **delivery
  failure, not a reason to abandon the run** — the plan step still happens.
- With no mailer configured, nothing goes out and the queue is **not** marked reported.
  That is correct: a report nobody received has not been reported.

Copy `tools/pengyos-nightly/pengyos-email.example` to `~/.local/bin/pengyos-email`,
`chmod +x`, and point it at your mail.

---

## 7. The model gateway contract

CVE triage asks a local model to judge reachability and priority. It reads your **agent's
own** settings file at `$PENGY_SETTINGS`, defaulting to `~/.config/pengy/settings.json`,
and needs two keys:

```json
{ "base_url": "http://your-gateway.home.lab:8080/v1", "api_key": "<yours>" }
```

- Used as an OpenAI-compatible `POST /chat/completions`, `temperature: 0`, **no tools**.
- `--no-model` runs triage with rules only. You get a working loop, with a coarser
  priority call and no reachability judgement.
- The rules that "cannot be argued with" run **before** the model and cap what it may
  assign. A model is an optimisation here, not a dependency.

---

## 8. The site host contract

The site is a **static tree**; there is no server-side code and no build step — a write
is public.

```
<site>/update/<IMAGE_ID>/<VERSION>/
    manifest.json  manifest.json.sig  rootfs.ext4.zst  bzImage  release.json
<site>/download/<VERSION>/
    pengyos-<edition>-<version>-x86_64.img.xz   SHA256SUMS  SHA256SUMS.sig  downloads.json
<site>/update/feed.xml   <site>/update/index.html   <site>/download/index.html
```

Rules that will bite you:
- **`update/feed.xml` and the two `index.html` files are GENERATED** by
  `pengyos-release update-feed`. Never hand-edit them. To retire a version: delete the
  version directory, then regenerate.
- **"Unlisted" is not "unavailable."** Removing a version from the feed does not remove
  its files. If you need it gone, delete the directory.
- The feed matches on **`IMAGE_ID`**. If you fork and rename your editions, your machines
  stop matching the feed silently.
- `pengyos-release`'s own feed format is a signed `index.json`, **not** the Atom
  `feed.xml` the site serves. `pengyos-release verify --feed` cannot verify a published
  Atom site. Verify a release with `pengyos-sign verify` per manifest, re-hash each
  artifact against its manifest, and `sha256sum -c download/<version>/SHA256SUMS`.
- Publishing is a two-step: write on the build host, then `rsync` to
  `$PENGYOS_SITE_REMOTE` (`host:path/`). The site host does not need the repo installed.

---

## 9. Monitoring — silence is a design feature, so probe it

Two of the loop's healthy states are **silent by design**: "nothing new to report" and
"nothing allowed in the plan." That is correct behaviour, and it is indistinguishable
from a loop that is broken, has been removed from the crontab, or is running on a machine
that is off.

So:

1. **Run the probe, and put its exit code somewhere.**
   ```
   python3 -m pengyos_nightly ... status    # exit 1 when the loop is stuck
   ```
   It reports observation age, open CVE counts, the act lane's state, the next batch, and
   whether the last batch published nothing. It exits non-zero when something is actually
   wrong, so a watchdog can use it without reading prose.
2. **Assert the crontab's *content*, not just the log's age.** A cadence check that ages a
   log file goes red only after the cadence window, and it cannot say *"the entry was
   removed"* — because a removed entry has no log to age. On 2026-10-17 a `crontab`
   REPLACE silently dropped the two nightly lines; both loops stopped for a day and the
   staleness alarm fired ~15 hours late, naming the wrong cause. Check that the expected
   lines **exist in the crontab**, hourly.
3. **Verify a crontab edit by diffing the whole file**, never by grepping for the lines
   you care about. `crontab -l > before`, edit, `crontab file`, `diff`.
4. **Make refusals loud.** `act --apply` mails the maintainer when the batch is refused
   (0.14.0+). Silence for "nothing to do" is right; silence for "refused" is how a night
   vanishes.
5. **Watch the shared checkout.** Anything that mutates the repo — an agent job, a build,
   a human session — can block the act lane via `clean_tree`. Know what touches it and
   when.

---

## 10. Secrets inventory — names, never values

The repo reads these. It ships none of them. Put them in your environment or in the file
`secrets` points at (`KEY=VALUE` lines).

| Name | Needed by | Consequence if absent |
|---|---|---|
| `PENGYOS_RELEASE_KEY` | `release.sh --publish`, the act lane | Refuses to publish; loop stays report-only. **A reported condition, not a crash.** |
| `PENGYOS_SITE_REMOTE` | `release.sh --push`, act lane `push` | Refuses to push |
| `PENGYOS_BUILD_ROOT` | every build | Outputs land in a default you did not choose |
| `PENGYOS_SITE` | release tooling | Staging site defaults to `<build-root>/site` |
| `PENGYOS_COSMIC_SRC`, `PENGYOS_COSMIC_BUILD` | Desktop builds | Desktop targets fail |
| `PENGYOS_EMAIL_SCRIPT` | the report + refusal mail | No mail; queue stays unreported |
| `PENGY_SETTINGS` | CVE triage | Use `--no-model` |
| `PENGYOS_AUTHORIZED_KEYS_FILE` | — | **Must be unset.** `release.sh --publish` refuses if it is set: it is the test-key guard |

Two rules:
- **Never print a secret, and never put one in a commit, a log, an email, or a doc.**
- The repo's compiled defaults for `key_file` and `secrets` are the upstream author's
  paths. Override them in `nightly.toml`.

---

## 11. What must stay human

The tier model (SPEC §1.5) is the contract for your agent:

| Tier | Surface | Who changes it |
|---|---|---|
| **0 — Base** | kernel, libraries, the image | Nobody at runtime. Signed image → the other A/B slot → boots once on trial → kept only if it reaches `multi-user.target`. |
| **1 — Machine** | `/etc`, services, driver config, user data | The agent, freely. Lives on the `data` partition; updates never touch it. |
| **2 — Critical** | signing keys, the update trust root, privilege changes | **A human signs.** Small, enumerated list. |

Concretely, do not let an agent: write a signing key, promote to stable, change the
authorized keys, approve a firmware write, reboot a machine, or install a binary that runs
with system access. Sandboxed apps (Flatpak) and guests (podman) are how untrusted
binaries get accepted **without** being trusted.

---

## 12. What you cannot reproduce

Be clear-eyed about this, because it is where a reproduction attempt quietly fails:

1. **The upstream signing key.** Nothing you build is trusted by machines carrying it.
2. **The published release history.** Every upstream image is a signed artifact of a
   specific build host, key, and time. You can rebuild from source and compare; you
   cannot reproduce the signature.
3. **The fleet evidence.** Hardware conclusions in the CHANGELOG and config comments were
   measured on specific machines. Re-derive them on your own hardware; do not assume them.
4. **The accumulated `patch_failures` knowledge.** You start with an empty known-bad set
   and pay for it in pre-flights.

What you **can** reproduce: the build (SPEC §7, and the reproducibility check has been
done for Core and Server), the gate, the SBOM, the boot verification, the release
mechanics, and the whole loop — under your own key.

---

## 13. Getting started, in order

```bash
git clone <this repo> && cd <repo>
scripts/provision-build-host.sh --desktop          # SPEC §7

# 1. your key  (private key is written 0600; the public key lands alongside it)
mkdir -p ~/keys && chmod 700 ~/keys
tools/pengyos-sign/target/release/pengyos-sign keygen \
        --out ~/keys/myrelease.key --pub ~/keys/myrelease.pub

# 2. your build root + site staging
export PENGYOS_BUILD_ROOT=/data/build/os PENGYOS_SITE=/data/build/os/site

# 3. your policy + state
mkdir -p /data/os-state
cp tools/pengyos-nightly/nightly.toml.example /data/os-state/nightly.toml   # edit it

# 4. your mailer
cp tools/pengyos-nightly/pengyos-email.example ~/.local/bin/os-email
chmod +x ~/.local/bin/os-email && export PENGYOS_EMAIL_SCRIPT=~/.local/bin/os-email

# 5. prove the pipeline before arming it
python3 -m pytest -q                                   # one tool's suite
python3 scripts/pengyos_gate.py run --repo . --mode full
python3 -m pengyos_nightly --repo . --build-root $PENGYOS_BUILD_ROOT \
        --state-dir /data/os-state --site $PENGYOS_SITE run
python3 -m pengyos_nightly ... plan
python3 -m pengyos_nightly ... act                      # DRY RUN: changes nothing
python3 -m pengyos_nightly ... status                   # exit 0 = healthy

# 6. only now: arm it. act_enabled = true, and install the crontab.
crontab -l > crontab.before
crontab tools/pengyos-nightly/crontab.example
diff crontab.before <(crontab -l)                       # verify the whole file
```

> `pengyos-sign` is a **Rust binary** at `tools/pengyos-sign/target/release/pengyos-sign`
> (cargo-built), not a Python module. `python3 -m pengyos_sign` does not exist. Run
> `python3 tools/pengyos/pengyos.py contract` to print the run form of every tool dir,
> derived from the tree.
