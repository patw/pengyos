# profiles/

Minimal, strict-by-default AppArmor profiles for PengyOS core components
(including the `pengy` agent).

Naming convention: the profile file is named after the confined process
(e.g. `pengy-agent` for the pengy agent binary). Files here are plain
AppArmor profile text — no `.aa` extension is required, but the validator
treats every file in this directory as a profile.

These profiles are validated by the `pengyos-apparmor` tool; real loading
(`aa-load`) happens on an actual PengyOS system and is a non-goal of the
test suite.
