# /etc/profile.d/pengyos-readme.sh — where this system explains itself (Core overlay).
# The motd names /usr/share/pengyos.txt at an interactive login, but `ssh host cmd`
# and a terminal window never show a motd. This prints nothing (output here would
# break scp and rsync); it leaves the path in the environment, next to
# /etc/os-release's PENGYOS_README, for whoever looks there first.
export PENGYOS_README=/usr/share/pengyos.txt
