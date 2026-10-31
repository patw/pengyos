# pengyos-sharing — spec

**Goal:** a person at the Desktop shares a folder with the house without a
terminal, and knows how other computers open it.

**Must:**
- Show whether sharing is on, every share (folder, mode, users) and its
  `\\host\name` and `smb://host/name` addresses.
- Share a folder (picker plus typed path), suggest a valid name, offer
  read-only; change a share's folder or mode; stop sharing one after a
  confirmation that says the files stay.
- Set the person's share password (twice, 8–128 characters), sent on stdin.
- Explain refusals before running anything (the CLI's name rules, system
  folders, a name another share uses), and show `pengyos-shares`' own message
  when it refuses.
- Never hold root: every change is `pkexec /usr/bin/pengyos-shares`; the polkit
  rule allows exactly that program for local, active wheel sessions.

**Won't:** pick other users for a share (the CLI does), guest access, or
anything beyond the LAN (pengyos-shares keeps 445 to the private ranges).
