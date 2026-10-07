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

# Idempotency guard: a marker env var, so a double source is a no-op.
if [ -n "${PENGYOS_TOOLSET_FZF_INIT:-}" ]; then
    return 0 2>/dev/null || true
fi
PENGYOS_TOOLSET_FZF_INIT=1
export PENGYOS_TOOLSET_FZF_INIT

# Only useful in an interactive shell.
case "$-" in
    *i*) : ;;
    *) return 0 2>/dev/null || true ;;
esac

# Prefer ripgrep for the file finder when it is installed; fall back to find.
if command -v rg >/dev/null 2>&1; then
    export FZF_DEFAULT_COMMAND='rg --hidden --color=never --files'
else
    export FZF_DEFAULT_COMMAND='find . -type f -not -path "*/.git/*"'
fi

# Defaults: half-height, list mode, border, live preview when a viewer exists.
export FZF_DEFAULT_OPTS="${FZF_DEFAULT_OPTS:--height 40% --layout=reverse --border}"

# fzf ships key-bindings + completion scripts under a few common roots; probe
# for them so this works across distros/package managers without hard-coding one
# path. Only source a file that is actually readable (offline-safe).
if command -v fzf >/dev/null 2>&1; then
    for base in /usr/share/fzf /usr/local/share/fzf /nix/store/*/share/fzf /opt/fzf/share/fzf; do
        if [ -r "$base/key-bindings.sh" ]; then
            . "$base/key-bindings.sh"
        fi
        if [ -r "$base/completion.sh" ]; then
            . "$base/completion.sh"
        fi
    done
fi
