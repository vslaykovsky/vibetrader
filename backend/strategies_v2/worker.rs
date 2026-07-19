mod strategy;
mod utils;

use utils::run_strategy;

fn main() {
    let result = strategy::build_strategy().and_then(run_strategy);
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
