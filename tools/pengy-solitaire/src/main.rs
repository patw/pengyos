//! `pengy-solitaire` — Frostbite Solitaire. The whole program is `pengy_solitaire::cli`, so the shared
//! `pengy-games` binary can carry it too; this file only hands it the command
//! line.

fn main() {
    pengy_solitaire::cli::main(std::env::args().skip(1).collect());
}
