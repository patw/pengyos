# pengyos-remote-desktop — "Remote Desktop"

A COSMIC window over `pengyos-remote`: an on/off switch, where another
computer connects (this machine's name and addresses) and as whom, the
certificate fingerprint to check, the RDP password, and "Ask again" for the
on-screen permission. Every change is a `pengyos-remote` command run as you;
no administrator is needed. It refreshes every 5 seconds, so a change made in a
terminal shows up.

`pengyos-remote-desktop --status` prints the same as text.

Built with the other tools/ apps (`scripts/build-cosmic.sh`, feature `cosmic`);
`cargo test` covers the model without a display.
