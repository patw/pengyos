# PengyOS — a starting ~/.zshrc (copied from /etc/skel when your account was
# made; it is yours to change). Its existence also skips zsh's first-run wizard.
#
# The comfort setup (starship prompt, fzf Ctrl-R/Ctrl-T, zoxide, the modern
# aliases, autosuggestions + syntax highlighting) is system-wide:
# /etc/zshrc sources /etc/pengyos/shell/profile before this file runs, and
# the line below sources it again for the shells that skip /etc/zshrc. The
# profile is idempotent, so sourcing it twice is a no-op. Everything below
# is yours: delete any of it and the system comfort setup still stands.

if [ -r ~/.config/pengyos/shell/profile ]; then
    . ~/.config/pengyos/shell/profile
elif [ -r /etc/pengyos/shell/profile ]; then
    . /etc/pengyos/shell/profile
fi

# History: shared between terminals, no duplicates.
HISTFILE=~/.zsh_history
HISTSIZE=50000
SAVEHIST=50000
setopt share_history hist_ignore_all_dups hist_reduce_blanks extended_history

# Completion (menu-select, case-insensitive), emacs-style keys.
[[ -d ~/.cache ]] || mkdir -p ~/.cache
# -i: skip compinit's compaudit security check and its interactive prompt. The prompt
# needs a terminal; with none, `read -q` prints "not interactive and can't open terminal"
# and compinit aborts, so any non-tty zsh that sources this file leaks that to stderr and
# loses completion entirely. Measured on the CI gate 2026-10-14 (run 38580797351): the
# runner's fpath has directories compaudit calls insecure, so the check fires there and
# test_skel_zshrc_starts_silently_and_keeps_the_profile failed. Reproduced locally with a
# world-writable fpath entry: without -i, rc=1 and that exact pair of lines on stderr;
# with -i, rc=0 and silent. The security prompt is a developer convenience - it has no
# business running on every shell start of a managed image.
autoload -Uz compinit && compinit -i -d ~/.cache/zcompdump
zstyle ':completion:*' menu select
zstyle ':completion:*' matcher-list 'm:{a-z}={A-Z}'
bindkey -e
setopt auto_cd interactive_comments no_beep

# The oh-my-zsh-feel bits that need no plugin:
# (Ctrl-R is already an incremental substring search in emacs mode, and fzf's
# key bindings upgrade it to a fuzzy one when the profile loaded them.)
setopt complete_in_word           # complete from the cursor, not just the end
setopt always_to_end              # ... and land after a completed word
zstyle ':completion:*' use-cache yes
zstyle ':completion:*' cache-path ~/.cache/zsh
zstyle ':completion:*' group-name ''
zstyle ':completion:*:descriptions' format '%F{yellow}-- %d --%f'
zstyle ':completion:*:options' description 'yes'
zstyle ':completion:*' use-color auto

# pip --user, uv, cargo and friends install to ~/.local/bin.
typeset -U path
path=(~/.local/bin $path)

# user@host dir %  — '#' only for root. (Not zsh's %#: logind gives every
# session the CAP_WAKE_ALARM capability, which %# counts as "privileged".)
# This is the fallback for a shell with no starship (Core, a root console):
# when the profile above already loaded starship, its prompt is kept.
if [[ $PROMPT != *starship* ]]; then
    if (( EUID == 0 )); then _pengyos_mark='#'; else _pengyos_mark='%%'; fi
    PROMPT="%F{green}%n@%m%f %F{blue}%~%f %(?..%F{red}[%?]%f )${_pengyos_mark} "
    unset _pengyos_mark
fi

# Plain fallbacks, defined only where the profile did not already alias the
# name (it aliases ls/ll/la to eza on Server and Desktop).
(( ${+aliases[ls]} )) || alias ls='ls --color=auto'
(( ${+aliases[ll]} )) || alias ll='ls -lh'
(( ${+aliases[la]} )) || alias la='ls -lAh'
alias grep='grep --color=auto'
# Git, the way people who live in a shell type it.
alias g='git' gst='git status' gss='git status -s' gd='git diff' gco='git checkout'
alias glog='git log --oneline --graph --decorate -20' gp='git pull --ff-only'
