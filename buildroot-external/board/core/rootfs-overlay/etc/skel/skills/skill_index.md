# Skills Index

Pengy's skills live in `~/skills/`. Each one is a directory with a
`skillname/skillname_skill.md` instruction file, optionally backed by a small
bash or Python helper. Read the `_skill.md` before running the helper — the
instruction file says what to run and what the output means.

> **Read this index before you start a task.** If a skill covers the ground,
> read that skill and follow it instead of improvising: it already knows the
> paths, the flags and the gotchas of the thing you are about to touch.

skill structure: `skillname/skillname_skill.md`

| Dir | What | Script? |
|-----|------|:-------:|
| pengyos/ | **How this machine works** — the PengyOS layout (read-only system slots, the `/etc` overlay, the `data` partition), where everything lives, the update path, and the four lanes for installing software. Read this before touching system files, installing anything, or answering "what am I running". | ❌ (no script) |
| pengy/ | **What Pengy is and how it works** — the agent you are: the three front ends, `~/.config/pengy/`, the built-in tools, tool confirmation, and how skills extend it. | ❌ (no script) |

## Adding a skill

Ask Pengy to write one: it creates `~/skills/<name>/<name>_skill.md`, adds the
helper script next to it, and appends a row to the table above. Keep a skill to
one job, put the *how* in the markdown and the *doing* in the script, and make
any script that reports state accept `--json` so other agents can parse it.

Skills are deliberately plain files, not a protocol: readable, hackable,
versionable, and yours. Nothing here is required — Pengy runs fine with an
empty `~/skills/`.
