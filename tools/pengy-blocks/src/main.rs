//! `pengy-blocks` — Glacier Blocks. The whole program is `pengy_blocks::cli`, so the shared
//! `pengy-games` binary can carry it too; this file only hands it the command
//! line.

fn main() {
    pengy_blocks::cli::main(std::env::args().skip(1).collect());
}
