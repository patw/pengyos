//! `pengy-pong` — Penguin Pong. The whole program is `pengy_pong::cli`, so the shared
//! `pengy-games` binary can carry it too; this file only hands it the command
//! line.

fn main() {
    pengy_pong::cli::main(std::env::args().skip(1).collect());
}
