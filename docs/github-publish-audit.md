# Publishing audit — what has to change before this repo is public

Audit run 2026-10-05 on `main` at `25aacd0c`. Read-only: nothing here was
rewritten. The point is to know the cost before the first `git push`, because
the one expensive item (commit author metadata) is only cheap **before** the
repo exists anywhere else.

## What was checked, and how

```sh
git rev-list --count HEAD                                   # commit count
git log --format='%an <%ae>' | sort | uniq -c | sort -rn   # author identities
git log --reverse --format='%h %ad' --date=short | head -1 # first commit
git rev-list --objects --all | git cat-file --batch-check=... # blobs > 5 MB
git ls-files | grep -iE '\.env$|\.pem$|\.key$|id_rsa|credentials'
grep -rcE '10\.0\.23\.[0-9]+|<machine names>|dungeons@gmail\.com|/home/beholder' ...
```

## The shape of the history

| Fact | Value |
|---|---|
| Commits | 1131, 2026-09-12 → 2026-10-05 |
| Tracked files | 2600 |
| Authors | `Ralph Loop <ralph@local>` 780, `Pat Wendorf <dungeons@gmail.com>` 310, `Pengy <pengy@catbee.ca>` 10, plus 8 near-duplicate ralph spellings (`ralph@pengyos.local`, `ralph@pengyos`, `ralph@localhost`, `ralph@catbee.local`, `ralph@catbee.ca`, `ralph@local`), one `beholder <beholder@catbee.ca>`, one `Pengy <pengy@catbee.cc>` (typo) |
| Blobs over 5 MB in any commit | none — no images, no tarballs of build output |
| Secrets | none found. The only `.key` files are `tools/pengyos-secureboot-enroll/tests/fixtures/keys/{PK,KEK,DB}.key`, whose contents are literal placeholders (`PRIVATE-KEY-PK-BYTES-0123456789abcdef`) |
| Remote | **none configured** — this repo has never been pushed |

The author list is the honest record of how the project was built (a ralph loop
did most of it). Keeping it is a defensible choice; the question is whether the
personal email and the near-duplicate bot identities are worth flattening.

## Private references in the tracked tree

Counts are occurrences across `*.md *.toml *.py *.rs *.sh`:

| Kind | Count | Where it concentrates |
|---|---:|---|
| Home-network machine names (`behoserver`, `behoweather`, `behopc`, `miniserv`, `behocraptop`, `behobasement`, `beholaptop`, `behotv`, `behorouter`) | 97 | `CHANGELOG.md` 29, `TODO.md` 26, `buildroot-external/board/desktop/post-build.sh` 5, `USE-CASES.md` 4, `README.md` 4 |
| LAN IP (`10.0.23.4`) | 1 | `CHANGELOG.md:1049` |
| Personal email (`dungeons@gmail.com`) | 1 | `tools/pengyos-release/pyproject.toml` (author field) |
| Absolute home paths (`/home/beholder/…`) | 2 | both in CHANGELOG prose, describing a fix |
| Private hosts/domains (`mem.catbee.ca`, `img.catbee.ca`) | 0 | — |

`pengyos.catbee.ca` appears throughout and is **not** private: it is the product
site and the OTA feed. It stays.

Two sub-kinds matter differently:

- **Load-bearing mentions.** `buildroot-external/board/*/post-build.sh` and
  several `external.mk` comments name behoserver/distrobox because they explain
  *why* a build-host leak was pinned (`-Dpolkit_agent_helper_1=…`,
  `-Ddnsmasq=…`). Rewriting those to "the build host" loses the reason the pin
  exists, which is the thing a future maintainer needs. Recommend: keep, with a
  one-line note that these are examples of build hosts.
- **History prose.** `CHANGELOG.md` and `TODO.md` name machines because they
  record where a check was run ("verified on behoweather"). That is honest
  provenance, and it is also the bulk of the count. Recommend: keep the
  provenance, and delete `TODO.md` from the public tree entirely once its items
  become GitHub issues (the file is already marked "temporary — delete once
  these land").

## The scrub plan, in the order that works

1. **Decide the author question first.** `git filter-repo` rewrites every commit
   hash. After the first push, a rewrite means force-pushing over a public
   history and invalidating every clone, tag and issue reference. So this is a
   *before* decision, not a cleanup task.
   - Option A — leave it. `dungeons@gmail.com` appears in 310 commit author
     fields. It is already on other public repos; the cost is spam, not exposure.
   - Option B — rewrite to a project identity, e.g.
     `git filter-repo --mailmap mailmap.txt` mapping every ralph spelling to
     `Ralph Loop <ralph@pengyos.local>` and Pat to a noreply address
     (`Pat Wendorf <patw@users.noreply.github.com>`). This also fixes the
     `pengy@catbee.cc` typo and the 8 ralph spellings, which is worth doing for
     `git log` readability even if the email stays.
   - Option C — squash the history into a few authored commits. Cleanest tin,
     worst for anyone tracing why a pin exists. Not recommended: this project's
     value is in its commit messages.
2. **Fix the one tracked personal email** (`tools/pengyos-release/pyproject.toml`
   author field) — a normal commit, no rewrite needed. Do this regardless.
3. **Drop `TODO.md`** once its open items exist as GitHub issues, and keep
   `SECURITY.md` (it already states the vulnerability-reporting path).
4. **Then create the repo and push.** `gh` is on miniserv, not on behoserver;
   either install it here or push from miniserv. Add the remote, push `main` and
   the tags, and confirm CI (the full gate on every push) actually runs.
5. **After** the push: nothing above can be redone cheaply. That is the whole
   reason this file exists.

## What is already publish-ready

`LICENSE` (MIT, "Copyright (c) 2026 Pat Wendorf") in every tool directory,
`SECURITY.md`, `CONTRIBUTING.md` (including how to release), `README.md`,
`.gitignore`, the repo-level gate (`scripts/pengyos_gate.py`) running on every
push, and a green suite in each tool. The generated tool-README opener was
already trimmed and the GitHub links already point at `patw/pengyos`.

## Open decisions for the operator

1. Public or private first.
2. Author metadata: leave, mailmap, or squash (item 1 above).
3. Whether `TODO.md` is deleted at the same moment its items become issues, or
   published as-is and removed later.
4. Whether the machine names in `CHANGELOG.md` stay (recommended: yes).
