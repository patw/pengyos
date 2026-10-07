"""Machine-local policy for the act lane, and where the secrets come from.

`<state-dir>/nightly.toml` is policy for *this* machine (which build host, which
key, how much to do per night) so it is not in the repo. Every value has a
default; the file only says what differs.

The release key and the site target are never in the repo and never printed:
`PENGYOS_RELEASE_KEY` (env) or the key file it points at, and
`PENGYOS_SITE_REMOTE` (env, else ~/Personal/.secrets). If either is missing the
act lane refuses and the loop stays report-only — a missing key is a reported
condition, not a crash.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

HOME = Path.home()

DEFAULTS = {
    # The operator's decision (2026-10-05): the loop may sign and publish to
    # testing, and push it to the live feed. Stable is unreachable in code.
    "act_enabled": True,
    "push": True,
    "lanes": ["security", "routine"],
    "priorities": ["P0", "P1"],
    # Phase 3 (2026-10-06): the batch is the unit of work, the signed image is the
    # unit of risk. Measured on 2026-10-05: one full action end to end took 18m14s and
    # all five editions rebuilt incrementally took 9m42s on 32 cores, so capping actions
    # at 1 was never a hardware limit - it capped CVEs closed while paying the full
    # verification cost once per action. max_actions is how much the loop may prepare
    # and verify in a night; max_releases is how many signed images it may publish,
    # and stays 1 so the blast radius per night does not grow.
    "max_actions": 12,
    "max_releases": 1,
    "cooldown_days": 7,
    # The shape the build proved (2026-10-05). A stock package cannot be
    # version-bumped from a br2-external tree, so the security lane carries upstream
    # fix commits; see edits.py for the repro that settled it.
    "allow_patch_shape": True,
    "skip_cosmic": True,
    "key_file": str(HOME / "Personal/pengyos-keys/release-2026.key"),
    "secrets": str(HOME / "Personal/.secrets"),
}


def load(path: Path) -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(tomllib.loads(Path(path).read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def _secret_value(path: Path, name: str) -> str | None:
    """KEY=VALUE lines in ~/Personal/.secrets. Value never leaves this function."""
    try:
        for line in path.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name and v.strip():
                return v.strip()
    except OSError:
        return None
    return None


def release_key(cfg: dict) -> str | None:
    env = os.environ.get("PENGYOS_RELEASE_KEY", "").strip()
    if env and Path(env).expanduser().is_file():
        return env
    f = Path(cfg["key_file"]).expanduser()
    return str(f) if f.is_file() else None


def site_remote(cfg: dict) -> str | None:
    env = os.environ.get("PENGYOS_SITE_REMOTE", "").strip()
    if env:
        return env
    return _secret_value(Path(cfg["secrets"]), "PENGYOS_SITE_REMOTE")
