//! `pengy-invaders` — Thaw Invaders. The whole program is `pengy_invaders::cli`, so the shared
//! `pengy-games` binary can carry it too; this file only hands it the command
//! line.

fn main() {
    pengy_invaders::cli::main(std::env::args().skip(1).collect());
}
