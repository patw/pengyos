# /etc/profile.d/pengyos-shell.sh — the comfort shell profile for bash login
# shells (Server layer overlay). zsh is wired through /etc/zshrc; this is the
# bash half (root's shell). The profile is guarded and idempotent, and makes
# no network call at shell start.

if [ -r /etc/pengyos/shell/profile ]; then
    . /etc/pengyos/shell/profile
fi
