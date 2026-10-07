# PengyOS install stick: root's autologin on tty1 opens the console installer.
# Only on a live boot (installed systems boot with pengyos.data= on the kernel
# line), only on tty1 (one installer at a time — over SSH or the serial console
# run `pengyos-install-tui install` yourself), only once per boot, and never
# where the Desktop's graphical installer takes over.
if [ "$(id -u)" = 0 ] && [ "$(tty 2>/dev/null)" = /dev/tty1 ] \
    && ! grep -q 'pengyos\.data=' /proc/cmdline \
    && [ ! -x /usr/bin/pengyos-install-gui ] \
    && [ ! -e /run/pengyos/console-installer.ran ]; then
    mkdir -p /run/pengyos && : > /run/pengyos/console-installer.ran
    /usr/libexec/pengyos/console-installer
fi
