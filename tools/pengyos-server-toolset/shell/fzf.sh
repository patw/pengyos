# fzf.sh — fzf shell init for the PengyOS server comfort set (US-SRV-1)
#
# POSIX-sh compatible; source it from your shell rc (the profile sources it for
# you). Safe to source more than once. Makes NO network call at shell start:
# it only reads local files and sets local options, so a fresh login works
# fully offline.
#
#   . shell/fzf.sh
#
# If fzf ships completion/key-binding scripts on your system they are sourced
# (a few common locations are probed, so this works on Debian/Ubuntu, Alpine,
# Nix, a Buildroot image, etc.); otherwise sensible local defaults are set so
# `Ctrl-R`, `Ctrl-T` and `Alt-A` still work.

# Only useful in an interactive shell.
case "$-" in
    *i*) : ;;
    *) return 0 2>/dev/null || true ;;
esac

# Idempotency guard: a shell variable (not exported, so a child shell runs
# its own init), set only once we know the shell is interactive.
if [ -n "${PENGYOS_TOOLSET_FZF_INIT:-}" ]; then
    return 0 2>/dev/null || true
fi
PENGYOS_TOOLSET_FZF_INIT=1

# Prefer ripgrep for the file finder when it is installed; fall back to find.
if command -v rg >/dev/null 2>&1; then
    export FZF_DEFAULT_COMMAND='rg --hidden --color=never --files'
else
    export FZF_DEFAULT_COMMAND='find . -type f -not -path "*/.git/*"'
fi

# Defaults: half-height, list mode, border, live preview when a viewer exists.
export FZF_DEFAULT_OPTS="${FZF_DEFAULT_OPTS:---height 40% --layout=reverse --border}"

# fzf ships key-bindings + completion scripts under a few common roots; probe
# for them so this works across distros/package managers without hard-coding one
# path. Only source a file that is actually readable (offline-safe).
#
# The scripts are per shell (key-bindings.bash / key-bindings.zsh; there is no
# `.sh` flavour — probing for one meant Ctrl-R/Ctrl-T never loaded). fzf 0.48+
# prints its own integration for the running shell (`fzf --zsh`, `fzf --bash`),
# which is version-matched to the binary, so that is tried first.
if command -v fzf >/dev/null 2>&1; then
    if [ -n "${ZSH_VERSION:-}" ]; then
        _fzf_sh=zsh
    elif [ -n "${BASH_VERSION:-}" ]; then
        _fzf_sh=bash
    else
        _fzf_sh=
    fi
    if [ -n "$_fzf_sh" ]; then
        _fzf_init=$(fzf --"$_fzf_sh" 2>/dev/null) || _fzf_init=
        if [ -n "$_fzf_init" ]; then
            eval "$_fzf_init"
        else
            for base in /usr/share/fzf /usr/local/share/fzf /opt/fzf/share/fzf; do
                if [ -r "$base/key-bindings.$_fzf_sh" ]; then
                    . "$base/key-bindings.$_fzf_sh"
                    [ -r "$base/completion.$_fzf_sh" ] && . "$base/completion.$_fzf_sh"
                    break
                fi
            done
        fi
        unset _fzf_init
    fi
    unset _fzf_sh
fi
