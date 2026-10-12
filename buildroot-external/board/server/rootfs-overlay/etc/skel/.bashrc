# PengyOS — a starting ~/.bashrc (copied from /etc/skel when your account was
# made; it is yours to change).
#
# The comfort setup (starship prompt, fzf Ctrl-R/Ctrl-T, zoxide, the modern
# aliases) lives system-wide at /etc/pengyos/shell/profile: bash login shells
# source it through /etc/profile.d, and the line below covers non-login
# interactive shells. The profile is guarded and idempotent. zsh users get it
# through /etc/zshrc; this file is bash's half.

case $- in
    *i*) ;;
      *) return 0 ;;
esac

if [ -r ~/.config/pengyos/shell/profile ]; then
    . ~/.config/pengyos/shell/profile
elif [ -r /etc/pengyos/shell/profile ]; then
    . /etc/pengyos/shell/profile
fi

# History: shared between terminals, no duplicates.
export HISTFILE=~/.bash_history
export HISTSIZE=50000
export HISTFILESIZE=50000
shopt -s histappend
export PROMPT_COMMAND="history -a${PROMPT_COMMAND:+;$PROMPT_COMMAND}"

# pip --user, uv, cargo and friends install to ~/.local/bin.
case ":$PATH:" in
    *":$HOME/.local/bin:"*) : ;;
    *) PATH="$HOME/.local/bin:$PATH" ;;
esac
export PATH
