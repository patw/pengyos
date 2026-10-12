"""No-telemetry assertion over the config set.

Covers: *"the configs declare no telemetry"* / *"no-telemetry scan flags a
planted phone-home URL (test has teeth)"*.

The comfort configs (``tmux.conf``, the ``profile`` / ``zoxide.sh`` snippets)
must never phone home: a fresh offline login must work, and none of them may
carry an analytics/telemetry endpoint, an analytics SDK, or an explicit
telemetry *enablement*.

:func:`conftest.scan_config_for_telemetry` (the shared scanner, also used by any
future config) strips comments first — so a comment that merely *mentions*
"telemetry" does not trip the detector (that is also starship's config key, set
to ``false`` — starship.toml was dropped at item 7, but the bare word is still
an opt-out, not a phone-home signal) — then flags every line with a phone-home
token. This module:

* asserts the whole config set is free of phone-home tokens;
* asserts telemetry is not *enabled* (checked **positively**: ``telemetry =
  true`` absent);
* has **teeth** — a planted phone-home URL, a planted analytics SDK, and a
  planted ``telemetry = true`` are each asserted to be flagged, while the
  desired ``telemetry = false`` opt-out and mere mentions in comments are not.
"""

from __future__ import annotations

from pathlib import Path

from conftest import (
    NO_TELEMETRY_TOKENS,
    scan_config_for_telemetry,
    scan_config_path_for_telemetry,
    strip_config_comments,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SHELL_DIR = REPO_ROOT / "shell"

# The config set this item asserts over: the non-shell configs (tmux.conf, the
# TOML prompt) and the POSIX-sh snippets (profile, fzf.sh, zoxide.sh) — everything
# that ships in ``shell/`` (item 20 re-added fzf.sh + starship.toml).
TMUX_CONF = SHELL_DIR / "tmux.conf"
PROFILE = SHELL_DIR / "profile"
ZOXIDE_SH = SHELL_DIR / "zoxide.sh"
FZF_SH = SHELL_DIR / "fzf.sh"
STARSHIP_TOML = SHELL_DIR / "starship.toml"

# (path, is_shell) for every config in the set, so the scan iterates the whole
# set without a hard-coded list that could rot. `is_shell` picks the comment
# stripping rule (shell drops inline ``#``; tmux/TOML keep inline content).
CONFIG_SET: tuple[tuple[Path, bool], ...] = (
    (TMUX_CONF, False),
    (PROFILE, True),
    (ZOXIDE_SH, True),
    (FZF_SH, True),
    (STARSHIP_TOML, False),
)


# --- the whole config set is free of phone-home tokens ----------------------


def test_config_set_has_no_phone_home_tokens():
    """Every config in the set must be clean under the shared no-telemetry scan."""
    for path, is_shell in CONFIG_SET:
        problems = scan_config_path_for_telemetry(path)
        assert problems == [], (
            f"{path.name} phones home:\n  - "
            + "\n  - ".join(problems)
        )


def test_every_config_in_the_set_is_scanned():
    """The scan must actually cover each shipped config (no silent skip)."""
    scanned = {path for path, _ in CONFIG_SET}
    shipped = {p for p in SHELL_DIR.iterdir() if p.is_file()}
    # shipped minus the .gitkeep placeholder (if ever re-added)
    shipped.discard(SHELL_DIR / ".gitkeep")
    assert shipped, "shell/ should contain configs to scan"
    assert shipped <= scanned, f"config set does not cover shipped configs: {shipped - scanned}"


# --- the scan has teeth: a planted phone-home URL is flagged -----------------


def test_scan_flags_planted_phone_home_url():
    """Teeth: a planted https:// URL (a phone-home endpoint) must be flagged."""
    planted = (
        "# a comment\n"
        'set -g status-right "#[fg=default] https://analytics.evil.example/collect"\n'
    )
    problems = scan_config_for_telemetry(planted, is_shell=False)
    assert problems, "the scan must flag a planted phone-home URL"
    assert any("https://" in p for p in problems), problems


def test_scan_flags_planted_http_url():
    """Teeth: a planted http:// URL must be flagged too."""
    planted = 'format = "x http://telemetry.example/ping"\n'
    assert scan_config_for_telemetry(planted, is_shell=False)


def test_scan_flags_planted_analytics_sdk():
    """Teeth: a planted analytics SDK reference must be flagged."""
    planted = "# no analytics here\n[telemetry]\nendpoint = \"https://mixpanel.example\"\n"
    problems = scan_config_for_telemetry(planted, is_shell=False)
    assert any("mixpanel.example" in p for p in problems), problems


def test_scan_flags_planted_telemetry_enablement():
    """Teeth: an explicit ``telemetry = true`` must be flagged."""
    planted = "telemetry = true\nformat = \"x\"\n"
    problems = scan_config_for_telemetry(planted, is_shell=False)
    assert problems, "telemetry = true must be flagged"
    assert any("telemetry = true" in p for p in problems), problems


def test_scan_flags_planted_shell_curl():
    """Teeth: a planted curl/URL in a shell snippet must be flagged."""
    planted = 'export X="ok"\ncurl https://beacon.example/x >/dev/null 2>&1\n'
    problems = scan_config_for_telemetry(planted, is_shell=True)
    assert any("beacon.example" in p for p in problems), problems


# --- the scan does NOT false-positive on the desired state -------------------


def test_scan_allows_telemetry_opt_out():
    """The desired ``telemetry = false`` opt-out must NOT be flagged."""
    assert scan_config_for_telemetry("telemetry = false\nformat = \"x\"\n", is_shell=False) == []


def test_scan_allows_telemetry_mention_in_comment():
    """A comment that merely says 'no telemetry' must NOT be flagged."""
    comment = "# no telemetry / no analytics / no phone-home\nformat = \"x\"\n"
    assert scan_config_for_telemetry(comment, is_shell=False) == []


def test_scan_allows_plain_text_without_tokens():
    """A config with no phone-home tokens must be clean (sanity)."""
    clean = 'format = "[ $path ]($style)"\n[shell]\nsymbol = "❯"\n'
    assert scan_config_for_telemetry(clean, is_shell=False) == []


def test_scan_is_case_insensitive():
    """The scan matches tokens case-insensitively (HTTPS, Analytics, ...)."""
    planted = 'set -g x "HTTPS://ANALYTICS.EXAMPLE/ping"\n'
    assert scan_config_for_telemetry(planted, is_shell=False)


# --- telemetry is disabled positively (spec) -------------------------------


def test_no_config_enables_telemetry():
    """No config may *enable* telemetry: ``telemetry = true`` must be absent."""
    for path, is_shell in CONFIG_SET:
        code = strip_config_comments(path.read_text(encoding="utf-8"), is_shell)
        assert "telemetry = true" not in code.lower(), (
            f"{path.name} enables telemetry (telemetry = true)"
        )


def test_starship_theme_runs_no_commands_of_its_own():
    """Starship has no telemetry and no `telemetry` key (setting one only made
    it warn on every prompt). What could reach the network from a theme is a
    `custom` module or an `env_var`-driven command: the theme defines none, so
    every prompt segment is computed by starship from local state."""
    import tomllib
    theme = tomllib.loads(STARSHIP_TOML.read_text(encoding="utf-8"))
    assert "telemetry" not in theme, "starship has no telemetry key; it warns on it"
    assert "custom" not in theme, "the theme must not run commands at each prompt"
    assert "$custom" not in theme.get("format", ""), "no custom modules in the format"


# --- the scanner covers the documented token set ---------------------------


def test_no_telemetry_token_list_is_nonempty():
    """The shared token list must be populated (a scan with no tokens is vacuous)."""
    assert NO_TELEMETRY_TOKENS, "NO_TELEMETRY_TOKENS must be non-empty"
    # network egress + at least one analytics SDK must be covered.
    assert "https://" in NO_TELEMETRY_TOKENS
    assert any(tok.startswith(("sentry", "mixpanel", "amplitude", "posthog"))
               for tok in NO_TELEMETRY_TOKENS)


def test_strip_comments_drops_full_line_but_keeps_inline_shell_code():
    """strip_config_comments: full-line comments gone; inline shell code kept."""
    text = "# header\nexport A=1  # trailing note\nset -g x y\n"
    stripped = strip_config_comments(text, is_shell=True)
    assert "# header" not in stripped
    assert "# trailing note" not in stripped
    assert "export A=1" in stripped
    assert "set -g x y" in stripped
