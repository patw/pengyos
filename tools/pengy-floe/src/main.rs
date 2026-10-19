//! `pengy-floe` — Flappy Pengy. The whole program is `pengy_floe::cli`, so the shared
//! `pengy-games` binary can carry it too; this file only hands it the command
//! line.

fn main() {
    pengy_floe::cli::main(std::env::args().skip(1).collect());
}
