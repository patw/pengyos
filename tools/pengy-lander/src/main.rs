//! `pengy-lander` — Ice Lander. The whole program is `pengy_lander::cli`, so the shared
//! `pengy-games` binary can carry it too; this file only hands it the command
//! line.

fn main() {
    pengy_lander::cli::main(std::env::args().skip(1).collect());
}
