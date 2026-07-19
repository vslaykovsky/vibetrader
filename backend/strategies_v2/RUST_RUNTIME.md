# Rust strategy engine

Set one backend-wide option before the process starts:

```bash
STRATEGY_ENGINE=rust
```

`python` remains the default. The setting controls the generated entrypoint, workspace instructions, source persistence, historical backtests, hyperopt trials, interactive simulation, and DB-materialized live runners. Rust workspaces contain generated `strategy.rs`; platform-managed `worker.rs`, `simulator.rs`, `optimizer.rs`, `optimizer_runtime.rs`, `portfolio.rs`, and `utils.rs`; a per-thread Cargo package; and the existing JSON params/output files.

Rust binaries are built in release mode and cached until a Rust source, manifest, or lockfile changes. The balanced release profile uses `opt-level=2`, no LTO, and 8 codegen units; this keeps generated-strategy rebuilds substantially quicker than maximum optimization while retaining nearly all runtime performance for this workload. Cargo dependencies and build artifacts share `STRATEGY_RUST_TARGET_DIR` (default: `backend/.rust-target`), so later strategies reuse compiled dependencies. The backend Docker image includes pinned Rust 1.85.1/Cargo and warms the targets during image construction.

## Historical simulation

The historical execution hot path is Rust end to end:

1. `simulator.rs` constructs the generated strategy and obtains its subscriptions.
2. The existing Python market-data provider/cache prepares OHLC, subscription indicator, partial-bar, and Renko inputs once, then writes one named-field MessagePack dataset.
3. The Rust simulator reads that dataset once and owns the event loop, direct in-process strategy calls, order fills, portfolio state, metrics, progress, and output chart assembly.

There is no strategy subprocess and no stdin/stdout round trip per bar. Python is retained only as a bulk data/indicator adapter so the Rust engine uses exactly the current provider, session, cache, and indicator semantics. Moving those preparation routines to Rust later does not change the strategy API.

## Hyperparameter optimization

For a single-ticker strategy with closed, same-scale SMA subscriptions, `python hyperopt.py` dispatches one Rust optimizer process. It samples all candidates, asks the Python adapter for one maximum-warmup OHLC frame, and then runs every candidate through fresh in-memory strategy and portfolio state. Trial runs retain only metrics; the winner is rendered once with the normal full output contract.

Walk-forward mode is also owned by that process: Rust constructs folds, preserves a continuous seeded sampler across folds, and runs fold-local training trials with fresh state. For OOS execution it creates the winning strategy, warms its signals on pre-OOS bars without executing orders, then supplies the cash and open positions left by the preceding OOS fold on the first executable bar. The first fold starts with the configured deposit and no position. `test_window_days` controls both the OOS fold length and retraining interval, keeping execution contiguous. Rust stitches the resulting continuous equity and orders, calculates aggregate metrics, and writes auditable starting/ending portfolio snapshots for every fold to `walkforward.json`. The Python adapter is invoked once for the complete study data frame.

Parameters that define the market-data frame, including ticker, scale, dates, provider, and initial deposit, cannot be optimized on this path. Other subscription topologies automatically use the compatibility optimizer, preserving their existing behavior.

## Interactive transport

Rust defaults to:

```bash
STRATEGY_IPC_TRANSPORT=jsonl
```

`STRATEGY_IPC_TRANSPORT=msgpack` enables a four-byte big-endian length followed by named-field MessagePack. Python strategies retain their existing JSON-lines protocol.

This setting only applies to the paced interactive worker. MessagePack is smaller than newline JSON, but it is not automatically faster for small request/ack messages. A local no-op microbenchmark measured Rust JSON-lines at roughly 23k round trips/second and MessagePack at roughly 20k because Pydantic can validate JSON directly. Changing pipes to Unix-domain sockets would not remove the main costs: stdin/stdout are already anonymous OS pipes, and per-event serialization, validation, process wakeups, and portfolio/order feedback dominate.

For the interactive path, a shared-memory SPSC ring plus eventfd can beat pipes at very high event rates, but it adds substantially more complexity. The historical runner avoids the boundary entirely, which is faster than any IPC transport.
