//! `pengy-merge` — Icefloe 2048. The whole program is `pengy_merge::cli`, so the shared
//! `pengy-games` binary can carry it too; this file only hands it the command
//! line.

fn main() {
    pengy_merge::cli::main(std::env::args().skip(1).collect());
}
