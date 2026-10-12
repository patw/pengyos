# zoxide.sh — zoxide shell init for the PengyOS server comfort set (US-SRV-1)
#
# POSIX-sh compatible; source it from your shell rc (the profile sources it for
# you). Safe to source more than once. Makes NO network call at shell start:
# zoxide is a local, file-system-only fast-directory jumper.
#
#   . shell/zoxide.sh
#
# When the `zoxide` binary is installed we eval its own `init sh` output (which
# is POSIX-sh). When it is absent we install a tiny no-op `z`/`__zsh` shim so
# anything that calls `z`/`zd` still succeeds instead of erroring — the profile
# and tests never see a broken command.

# Only useful in an interactive shell.
case "$-" in
    *i*) : ;;
    *) return 0 2>/dev/null || true ;;
esac

# Idempotency guard: a shell variable (not exported, so a child shell runs
# its own init), set only once we know the shell is interactive.
if [ -n "${PENGYOS_TOOLSET_ZOXIDE_INIT:-}" ]; then
    return 0 2>/dev/null || true
fi
PENGYOS_TOOLSET_ZOXIDE_INIT=1

if command -v zoxide >/dev/null 2>&1; then
    # zoxide's own POSIX-sh emitter.
    eval "$(zoxide init sh 2>/dev/null)"
else
    # No-op shim: define z/zd so callers do not fail when zoxide is missing.
    __zoxide() { return 0; }
    z() { __zoxide "$@"; }
    zd() { __zoxide "$@"; }
fi
