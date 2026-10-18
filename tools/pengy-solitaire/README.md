# pengy-solitaire — Frostbite Solitaire

Klondike, draw three, unlimited redeals: the game every OS used to ship and
PengyOS did not. Windows 3.0 had Solitaire and Minesweeper in the box, the Atari
2600 had its pack-in cart, BSD had `greedy` and `phantasia`. PengyOS ships ten
small complete games; this is solitaire.

Rust 2021, **`std` only, zero external crates**, no network, no root, no daemon.
It runs in any terminal, and it runs with no terminal at all.

## Play it

```sh
cd tools/pengy-solitaire
cargo run --release            # play (needs a terminal)
cargo run -- --help            # every flag and key
cargo run -- --headless --seed 21 --no-save   # prove it works with no pty
```

| Key | What it does |
|---|---|
| `h` `j` `k` `l` | Move the cursor. The arrow keys work too (`a`/`d`/`w`/`s` style left/right/up/down via the arrows). |
| `space` | Take the run the cursor is on, then drop it on the pile the cursor has moved to. Space on the source puts it back. |
| `d` | Draw three from the stock. An empty stock recycles the waste back (that costs 100, and passes are unlimited). |
| `f` | Auto-collect: send every card that is **safe** to its foundation, repeatedly, until nothing is safe. |
| `u` | Undo one level (50 deep). |
| `n` | New deal. |
| `q` | Quit, restoring the terminal. `Ctrl-C` quits too. |

Anything else is ignored. `Escape` is deliberately not a quit key: terminals send
the arrow keys as `ESC [ A`, so a bare `ESC` has to stay harmless.

## The rules, exactly

- A standard 52-card deck. **Seven tableau columns** of 1..7 cards, the top card
  of each face up; **four foundations**; a **24-card stock** and a **waste**.
- **Draw three** from the stock to the waste, preserving their order. Unlimited
  passes: an empty stock turns the whole waste back over into the stock, face
  down, for a 100-point penalty. Recycling needs a non-empty waste.
- **Tableau** builds down by rank with the colours alternating, and a run of
  cards moves as a unit. Only a **King** opens an empty column.
- **Foundations** build up by suit from the Ace, one card at a time.
- A tableau card **flips the moment it is uncovered** (+5).
- `f` only takes cards that are **safe**: an Ace always; a card of rank `r` only
  when both foundations of the *opposite* colour already hold rank `r-1`; and a
  card off the tableau only when the card beneath it is a legal build. Applied
  repeatedly until stable, so a second `f` does nothing.
- **Win** when all 52 cards are on the foundations. Klondike has no lose state:
  a game you cannot win is a game you press `n` on.

## Scoring

Standard Klondike, floored at zero:

| Event | Points |
|---|---|
| waste → tableau | +5 |
| waste → foundation | +10 |
| tableau → foundation | +10 |
| a tableau card flips | +5 |
| foundation → tableau | −15 |
| recycling the waste | −100 |

Moves and the elapsed time are counted alongside the score. A flip is not a
move; a recycle is.

## Headless: how the game is proven to work

The repo gate and `pengyos-qemu-verify` have no pty, so the game must work
without one. It does: with no TTY, with `--headless`, or with `--replay`, it
never touches the terminal, never reads keys, prints plain text and exits 0.

```sh
pengy-solitaire --headless --seed 21 --no-save                       # the demo player plays
pengy-solitaire --state fixtures/near-win.state --replay fixtures/win.replay --seed 1
pengy-solitaire --headless --seed 21 --ticks 900 --record /tmp/you.replay --no-save
```

A replay file is **one line of input per tick**: a line with characters is those
keypresses, a blank line is no input, a `#` line is a comment and is not a tick.
`_` is the space key (a trailing space is too easy for an editor to eat). The
harness stops at the win tick, at a quit key, or at EOF, prints the final state
as `key=value` lines, and exits 0. `hash=` is FNV-1a over the whole position:
same seed, same script, same hash. The elapsed timer is deliberately *not* in
the hash — it is the wall clock, not the position.

A state file (`--state`, written by `--save`) is the same format the fixtures
use: cards as ids `0..51` (`suit*13 + rank`), a leading `-` for face down, piles
bottom-to-top separated by `|`. It is validated on load — 52 distinct cards,
foundations ascending by suit, no face-down top card — so a broken fixture is a
clean exit 66, never a panic.

**The committed fixtures:**

| File | What it proves |
|---|---|
| `fixtures/near-win.state` | A legal position **one move from winning**: 51 cards on the foundations, the King of Diamonds on top of the waste. |
| `fixtures/win.replay` | The seven-tick script that wins it: `state=win`, `foundations=52`, `score=700`, `moves=121`. |
| `fixtures/demo.replay` | A 900-tick session from seed 21, played by the built-in demo player: 69 moves, 9 flips, 4 foundations, hash `a6e8695c240ee34f`. |

## Flags

`--seed N` (default 1), `--headless`, `--replay FILE`, `--state FILE`,
`--save FILE`, `--record FILE`, `--ticks N`, `--autopilot`, `--mono`,
`--no-color`, `--print-frame`, `--width N`, `--height N`, `--data-dir DIR`,
`--no-save`, `--help`, `--version`. `NO_COLOR` is honoured. Exit codes: **0**
success, **2** usage error, **66** unreadable replay or state file.

Designed for **80x24**. In a smaller window it degrades in a fixed order: the
border goes first (below 78 columns or 24 rows), then the score panel (below 66
columns), then the title (below 22 rows), then the key legend (below 20 rows).
The cards are never dropped: a pile that outgrows the grid compresses its
cascade and counts the cards that do not fit with a `+n` marker. `--mono` draws
in plain ASCII when the locale cannot render the box drawing and the suits.

## Where the record lives

`$XDG_DATA_HOME/pengyos/games/pengy-solitaire/best.txt` (default
`~/.local/share/pengyos/games/pengy-solitaire/best.txt`): best score, the moves
that scored it, the fastest win, games played, wins, and the seed the best score
happened on. Tier-1 mutable, under `$HOME`, never `/usr`, never the repo. A
failure to write is ignored — a read-only `$HOME` must not cost you a game.

## Layout

| Path | What is in it |
|---|---|
| `src/lib.rs` | **Every rule and the renderer, pure**: no files, no keyboard, no screen, no clock, no threads. The seeded xorshift64* is the only randomness, so a run is a function of `(seed, script)`. |
| `src/main.rs` | The terminal front-end: `stty raw -echo`, a stdin reader thread feeding the loop over `mpsc`, the alternate screen with one home+repaint per frame, a fixed 60 ms timestep that sleeps instead of spinning, the CLI, the record file. |
| `tests/rules.rs` | The rules: the 52-card deck, seeded shuffle determinism, every legality rule (accept and reject), the multi-card run move, stock draw order and recycle, auto-flip, auto-collect safety and idempotence, the score table, undo to an identical hash and its 50-level cap, the cursor, the tick, the win, rendering and the degrade rules, the scripted-session determinism, the fixtures. |
| `tests/cli.rs` | The real binary with no pty: `--help`, `--version`, `--headless`, `--replay`, `--state`, `--save`, `--record`, `--mono`, `NO_COLOR`, exit codes 0/2/66, frame geometry, the record file in the XDG path and nowhere else. |
| `tests/github_ready.rs` | The tool-directory contract (docs, license, verifier, empty dependencies, pure library, launcher, recipe, fixtures). |
| `fixtures/` | The near-win deal, the winning replay, a 900-tick demo session. |
| `launcher/pengy-solitaire.desktop` | The Desktop menu entry (`Terminal=true`, `Categories=Game;`). |
| `../../buildroot-external/package/pengy-solitaire/` | The Buildroot recipe skeleton that builds this crate from the repo into `/usr/bin`. |

## Buildroot

`buildroot-external/package/pengy-solitaire/` carries `Config.in` +
`pengy-solitaire.mk`: a `local`-source recipe that runs `cargo build --offline
--release` on this directory and installs the binary to
`/usr/bin/pengy-solitaire` plus the `.desktop` file. It needs no vendored crates
and no download digest, because the crate has **no dependencies** — that is the
point of the zero-dep rule.

Image wiring (the `source` line in `buildroot-external/Config.in`, the layer
`.config`, the menu) is deliberately **not** in this change: it is one shared
file touched by ten games, so it is a single integration pass, not ten.

## Tests

```sh
cd tools/pengy-solitaire && cargo test     # 40 rule tests + 19 CLI tests + 9 contract tests, <1s
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See [LICENSE](LICENSE).
