# AI-assisted contribution — our stance

**Status:** decision recorded, 2026-10-06. Operator direction; written by Pengy
from the discussion of 2026-10-06 and the external survey of that date.
**Scope:** how PengyOS attributes AI-assisted work, and how we contribute into
upstream projects in a climate that has turned hostile to it.

---

## The position

PengyOS is a **human-led, AI-assisted** project. A person sets direction and signs
the security boundary; agents draft, build, test, document and triage. We do not
present this as a philosophy of AI capability, and we do not present it as a
selling point. It is how a one-person team keeps a from-source distro current,
which is otherwise a decade of unpaid drudgery.

We also contribute **outward**. Principle 6 says improvements go home as upstreamable
patches. That is the part that now has friction, and it needs an explicit stance
rather than optimism.

---

## The world as it is in October 2026

Recorded because a future maintainer should not have to rediscover it:

- **Linux kernel, policy codified April 2026** (Torvalds and the maintainers). AI
  agents **must not** add `Signed-off-by` — the DCO is a human legal certification.
  An **`Assisted-by:` tag is mandatory**, naming the model, agent and tools, e.g.
  `Assisted-by: Claude:claude-3-opus coccinelle sparse`. Full human liability
  remains with the submitter. Torvalds rejected a blanket ban and wanted the policy
  to read as "just a tool", not a stance from either camp. The trigger was a fully
  AI-generated patch — code, changelog and tests — submitted to 6.15 without
  disclosure. His framing of the real problem:
  > "There is zero point in talking about AI slop. Because the AI slop people
  > aren't going to document their patches as such."
  The danger is not obvious junk; it is credible-looking patches that compile,
  match local style, meet the spec, and still carry a subtle bug or a maintenance
  tax.
- **Godot, 30 June 2026.** Bans autonomous agent use and vibe coding (auto-ban);
  *"No use of AI to generate substantial pieces of code. We require all code to be
  human authored"*; bans AI-generated text in human-to-human communication; blocks
  new features and significant refactoring from contributors with three or fewer
  merged PRs without maintainer permission. Their stated reason is not code quality
  and it deserves a real answer:
  > "AI contributions have the added pain of being demoralizing… If your feedback
  > on PRs is just being absorbed by a machine and not going towards mentoring a
  > potential future maintainer, it becomes much harder to justify spending your
  > free time on PR review."
- Elsewhere in 2026: curl ended its bug bounty, Jazzband shut down, GitHub has been
  reported to be considering letting projects disable pull requests entirely, and a
  project banned a vendor's GitHub account after an AI agent submitted sloppy work.
- **The other direction also exists.** Omarchy — an Arch-based, AI-forward desktop
  with its own shell — incorporated a nonprofit foundation in August 2026 with
  roughly $15M committed. It is evidence that an AI-forward Linux desktop project is
  viable and wanted. Note *what earned it*: taste, opinion, a keyboard-driven point
  of view. Not the AI framing, which is what the press leads with and what gets a
  project pre-judged.
- An uncomfortable dependency worth naming: System76 markets hardware partly on
  having no AI assistants, while PengyOS's Desktop is built on COSMIC, System76's
  stack. We are contributing into a community that may not want our authorship
  model.

---

## What we do, concretely

**1. Attribute honestly, upstream's way.** Where a project has an attribution
convention, we use it exactly as that project defines it — kernel-style
`Assisted-by:` naming model, agent and auxiliary tools. Where it has none, we
disclose in the patch description anyway. We do not submit agent-authored work as
human-authored. This is not deference; it is the only posture compatible with
Principle 1 and with being read again next year.

**2. A human signs, in the upstream sense too.** Principle 9 is about our trust
root, but the kernel's rule is the same rule: a person who understands the
architecture stands behind the change and takes the liability. Every PengyOS patch
sent outward is sent by Pat, not by an agent account. Agent GitHub accounts do not
open PRs into other people's projects.

**3. We answer the demoralization objection with format, not argument.** Godot's
real complaint is that review effort is spent on a machine that cannot learn. Our
outward contributions are therefore not drive-by PRs: they are a patch series with
rationale, a named upstream commit lineage, a test we ran, and a reproducible build
recipe — the kind of submission a maintainer can check in minutes rather than
review from scratch. If a project says no, **we respect the no** and carry the
divergence in the ledger openly (`docs/stack-down-replace.md`, *Divergence budget*).
We do not route around a project's contribution policy.

**4. We do not lead with the AI framing.** The claim PengyOS makes is checkable and
technical: reproducible, attested, signed, self-triaging, human-ratified. Lead with
what can be verified; let the process be described when asked. "First AI-native
distro" is a sentence that gets an artifact dismissed; the same facts stated as
engineering get read.

**5. We keep our own position explicit.** `CONTRIBUTING.md` welcomes contributions
from people and from agents under identical rules. That is a deliberate minority
position in 2026 and we hold it on purpose: our reviewer is the same person who
signs the boundary, our gate is automated and external, and our acceptance criterion
is a reproducible build and boot — not a diff that looks plausible. We are not
asking upstream to accept our posture; we are stating ours where we control the
repository.

---

## Why forks may come sooner than the ladder implies

The stated reason to expect more divergence is **not** that we want to fork. It is
that upstreams are raising the cost of contributing to them, including for
AI-assisted work that is disclosed, tested and well-argued. When a fix we need
cannot be offered upstream under a project's policy, the honest options are: carry
it as a documented divergence with an expiry, or fork with a ledger entry and a
pay-down plan. What is not on the table is hiding the authorship, or routing around
a project's rules.

This is the practical reason the divergence budget exists and exists *before* the
first fork rather than after it.

---

## Non-goals

- No agent-authored contribution submitted without disclosure.
- No agent GitHub account opening pull requests into upstream projects.
- No public framing of the project as an AI achievement.
- No assumption that upstream will accept our patches. Carrying a fix ourselves is
  a normal outcome, not a failure — provided it is declared, ledgered and expiring.
