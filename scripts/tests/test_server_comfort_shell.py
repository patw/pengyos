"""Tests for the Server comfort-shell wiring (US-SRV-1, the oh-my-zsh feel).

The shell comfort set has three halves that must not drift apart:

1. the recipes (`buildroot-external/package/{fzf,starship,zsh-autosuggestions,
   zsh-syntax-highlighting}`) — sourced in `Config.in`, enabled in the Server
   layer, present in the generated defconfigs;
2. the profile install (`board/server/post-build.sh` copies
   `tools/pengyos-server-toolset/shell/` to `/etc/pengyos/shell` + tmux.conf);
3. the system rc wiring (overlay `/etc/zshrc`, the `/etc/zsh/zshrc` symlink,
   `/etc/profile.d/pengyos-shell.sh`, and `/etc/skel/.zshrc` + `.bashrc`).

A Server/Desktop image that enables the packages but never sources the profile
— or sources a profile it never installs — is exactly the failure this caught
on 2026-10-12: the toolset had been authored (2026-09) with none of it wired.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "buildroot-external"
LAYER = EXT / "configs/layers/server.config"
CONFIG_IN = EXT / "Config.in"
SERVER_BOARD = EXT / "board/server"
OVERLAY = SERVER_BOARD / "rootfs-overlay"
SHELL_RECIPES = ("fzf", "starship", "zsh-autosuggestions", "zsh-syntax-highlighting")


def test_server_layer_enables_the_comfort_shell_packages():
    text = LAYER.read_text()
    for sym in ("BR2_PACKAGE_ZSH=y", "BR2_PACKAGE_FZF=y", "BR2_PACKAGE_STARSHIP=y",
                "BR2_PACKAGE_ZSH_AUTOSUGGESTIONS=y",
                "BR2_PACKAGE_ZSH_SYNTAX_HIGHLIGHTING=y"):
        assert sym in text, f"server layer must enable {sym}"


def test_every_comfort_recipe_is_wired_and_hashed():
    for name in SHELL_RECIPES:
        d = EXT / "package" / name
        assert (d / f"{name}.mk").is_file(), f"missing recipe {name}.mk"
        assert (d / "Config.in").is_file(), f"missing {name}/Config.in"
        assert (d / f"{name}.hash").is_file(), f"missing {name}.hash (pinned digest)"
        assert f'package/{name}/Config.in' in CONFIG_IN.read_text(), (
            f"Config.in must source {name} (the gate's symbol check needs it)"
        )


def test_generated_defconfigs_carry_the_comfort_shell():
    # The layer is the source; the defconfigs are generated. If these drift,
    # compose-defconfigs.py was not run (its own --check test covers staleness;
    # this one names the symbols so a silent drop is obvious). Core is excluded:
    # the comfort shell is a Server-layer set (Core ⊆ Server ⊆ Desktop).
    for cfg in ("pengyos_server_defconfig", "pengyos_desktop_defconfig",
                "pengyos_server_nvidia_defconfig", "pengyos_desktop_nvidia_defconfig"):
        text = (EXT / "configs" / cfg).read_text()
        for sym in ("BR2_PACKAGE_FZF", "BR2_PACKAGE_STARSHIP",
                    "BR2_PACKAGE_ZSH_AUTOSUGGESTIONS",
                    "BR2_PACKAGE_ZSH_SYNTAX_HIGHLIGHTING"):
            assert f"{sym}=y" in text, f"{cfg} must enable {sym}"


def test_server_post_build_installs_the_profile_files():
    text = (SERVER_BOARD / "post-build.sh").read_text()
    assert "pengyos-server-toolset/shell" in text
    assert "etc/pengyos/shell" in text
    assert "etc/tmux.conf" in text


def test_system_rc_files_source_the_profile():
    zshrc = (OVERLAY / "etc/zshrc").read_text()
    assert ". /etc/pengyos/shell/profile" in zshrc, "/etc/zshrc must source the profile"
    bash = (OVERLAY / "etc/profile.d/pengyos-shell.sh").read_text()
    assert ". /etc/pengyos/shell/profile" in bash
    link = OVERLAY / "etc/zsh/zshrc"
    assert link.is_symlink(), "overlay must carry /etc/zsh/zshrc (Debian-patched zsh)"


def test_skel_rcs_source_the_profile_and_survive_without_it():
    # New accounts get the comfort setup, and the starter files stand on their
    # own (history, completion, prompt) when the profile is absent (Core).
    for name in (".zshrc", ".bashrc"):
        text = (OVERLAY / "etc/skel" / name).read_text()
        assert "/etc/pengyos/shell/profile" in text, f"skel {name} must source the profile"
    zshrc = (OVERLAY / "etc/skel/.zshrc").read_text()
    assert "compinit" in zshrc and "HISTFILE" in zshrc
    assert "_pengyos_mark='%%'" in zshrc, "the '%' prompt fix (CAP_WAKE_ALARM) must stay"


def test_profile_is_posix_sh_parseable():
    # The profile is sourced by sh, bash and zsh; a syntax error in it breaks
    # every login shell. (The toolset's own suite parses the snippets too.)
    import subprocess
    prof = REPO / "tools/pengyos-server-toolset/shell/profile"
    p = subprocess.run(["sh", "-n", str(prof)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    p = subprocess.run(["zsh", "-n", str(prof)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
