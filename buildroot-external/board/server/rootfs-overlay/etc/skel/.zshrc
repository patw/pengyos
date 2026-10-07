# PengyOS — a starting ~/.zshrc (copied from /etc/skel when your account was
# made; it is yours to change). Its existence also skips zsh's first-run wizard.

# History: shared between terminals, no duplicates.
HISTFILE=~/.zsh_history
HISTSIZE=50000
SAVEHIST=50000
setopt share_history hist_ignore_all_dups hist_reduce_blanks extended_history

# Completion (menu-select, case-insensitive), emacs-style keys.
[[ -d ~/.cache ]] || mkdir -p ~/.cache
autoload -Uz compinit && compinit -d ~/.cache/zcompdump
zstyle ':completion:*' menu select
zstyle ':completion:*' matcher-list 'm:{a-z}={A-Z}'
bindkey -e
setopt auto_cd interactive_comments no_beep

# pip --user, uv, cargo and friends install to ~/.local/bin.
typeset -U path
path=(~/.local/bin $path)

# user@host dir %  — '#' only for root. (Not zsh's %#: logind gives every
# session the CAP_WAKE_ALARM capability, which %# counts as "privileged".)
if (( EUID == 0 )); then _pengyos_mark='#'; else _pengyos_mark='%%'; fi
PROMPT="%F{green}%n@%m%f %F{blue}%~%f %(?..%F{red}[%?]%f )${_pengyos_mark} "
unset _pengyos_mark

alias ls='ls --color=auto' ll='ls -lh' la='ls -lAh' grep='grep --color=auto'
