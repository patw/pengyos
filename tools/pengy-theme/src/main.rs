//! `pengy-theme` CLI binary — thin argv shim over [`pengy_theme::cli::run`].

fn main() {
    // argv[0] is the program name; the dispatcher wants the remainder.
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::exit(pengy_theme::cli::run(&args));
}
