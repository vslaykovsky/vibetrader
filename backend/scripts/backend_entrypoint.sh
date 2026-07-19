#!/bin/sh
set -eu

engine="$(printf '%s' "${STRATEGY_ENGINE:-python}" | tr '[:upper:]' '[:lower:]')"
if [ "$engine" = "rust" ]; then
    cargo_bin="${STRATEGY_RUST_CARGO:-cargo}"
    if ! command -v "$cargo_bin" >/dev/null 2>&1; then
        echo "STRATEGY_ENGINE=rust but Cargo executable '$cargo_bin' is not available" >&2
        exit 127
    fi
    if ! command -v rustc >/dev/null 2>&1; then
        echo "STRATEGY_ENGINE=rust but rustc is not available" >&2
        exit 127
    fi

    target_dir="${STRATEGY_RUST_TARGET_DIR:-/app/.rust-target}"
    mkdir -p "$target_dir"
    if [ ! -w "$target_dir" ]; then
        echo "Rust target directory '$target_dir' is not writable" >&2
        exit 1
    fi

    echo "Rust strategy runtime ready: $($cargo_bin --version); $(rustc --version); target=$target_dir" >&2
fi

exec "$@"
