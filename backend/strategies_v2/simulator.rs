mod optimizer_runtime;
mod portfolio;
mod strategy;
mod utils;

use portfolio::{Portfolio, Trade};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Instant;
use utils::{InputDataPoint, OutputDataPoint, StrategyHandler, StrategyInput};

#[derive(Deserialize)]
struct SimulationDataset {
    strategy_name: String,
    tickers: Vec<String>,
    base_scale: String,
    simulation_scale: String,
    primary_ticker: String,
    initial_deposit: f64,
    max_leverage: f64,
    multi_ticker: bool,
    total_units: usize,
    subscription_charts: Vec<Value>,
    events: Vec<SimulationEvent>,
}

#[derive(Deserialize)]
struct SimulationEvent {
    unixtime: i64,
    points: Vec<InputDataPoint>,
    fills: BTreeMap<String, f64>,
    marks: BTreeMap<String, f64>,
    invoke_strategy: bool,
    record_equity: bool,
    base_close: bool,
    chart_time: Value,
    benchmark_close: Option<f64>,
    base_row: Option<usize>,
    mark_before_input: bool,
    #[serde(default)]
    warmup: bool,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum OutputMode {
    MetricsOnly,
    Full,
}

fn write_pretty(path: &Path, value: &Value) -> Result<(), Box<dyn Error>> {
    let mut body = serde_json::to_string_pretty(value)?;
    body.push('\n');
    fs::write(path, body)?;
    Ok(())
}

fn load_dataset(
    workspace: &Path,
    startup: &[OutputDataPoint],
) -> Result<SimulationDataset, Box<dyn Error>> {
    let startup_path = workspace.join(".rust-simulation-startup.json");
    let dataset_path = workspace.join(".rust-simulation-data.msgpack");
    write_pretty(&startup_path, &serde_json::to_value(startup)?)?;
    let adapter = env::var("VIBETRADER_RUST_DATA_ADAPTER")
        .map(PathBuf::from)
        .map_err(|_| "VIBETRADER_RUST_DATA_ADAPTER is not configured")?;
    let python = env::var("STRATEGY_PYTHON_EXECUTABLE").unwrap_or_else(|_| "python3".to_owned());
    let status = Command::new(python)
        .arg(adapter)
        .arg("--workspace")
        .arg(workspace)
        .arg("--startup")
        .arg(&startup_path)
        .arg("--output")
        .arg(&dataset_path)
        .current_dir(workspace)
        .status()?;
    if !status.success() {
        return Err(format!("market-data adapter failed with {status}").into());
    }
    let bytes = fs::read(&dataset_path)?;
    let dataset = rmp_serde::from_slice(&bytes)?;
    let _ = fs::remove_file(startup_path);
    let _ = fs::remove_file(dataset_path);
    Ok(dataset)
}

fn trained_params(workspace: &Path) -> Option<InputDataPoint> {
    let path = workspace.join("trained_model_params.json");
    let value: Value = serde_json::from_slice(&fs::read(path).ok()?).ok()?;
    serde_json::from_value(value).ok()
}

fn marker_for_trade(trade: &Trade, time: &Value) -> Value {
    let invalid = !trade.valid;
    let is_buy = trade.action == "buy" || trade.action == "buy_to_cover";
    json!({
        "time": time,
        "position": if invalid { "inBar" } else if is_buy { "belowBar" } else { "aboveBar" },
        "color": if invalid { "#9e9e9e" } else if is_buy { "#26a69a" } else { "#ef5350" },
        "shape": if invalid { "circle" } else if is_buy { "arrowUp" } else { "arrowDown" },
        "text": if invalid { "ERROR" } else { trade.label() },
    })
}

fn order_row(trade: &Trade) -> Value {
    json!({
        "time": trade.unixtime,
        "ticker": trade.ticker,
        "direction": trade.direction,
        "price": round6(trade.price),
        "qty": round6(trade.qty),
        "deposit_ratio": round6(trade.deposit_ratio),
        "position_before_order": round6(trade.position_before_order),
        "position_after_order_filled": round6(trade.position_after_order_filled),
        "status": if trade.valid { "filled" } else { "invalid" },
        "comment": if trade.reason.is_empty() { "strategy signal" } else { trade.reason.as_str() },
    })
}

fn round6(value: f64) -> f64 {
    (value * 1_000_000.0).round() / 1_000_000.0
}

fn collect_outputs(
    outputs: &[OutputDataPoint],
    strategy_charts: &mut Vec<Value>,
    output_indicators: &mut BTreeMap<String, Vec<(i64, f64)>>,
    trained: &mut Option<Value>,
) -> Result<(), Box<dyn Error>> {
    for output in outputs {
        match output {
            OutputDataPoint::Chart { chart } => strategy_charts.push(chart.clone()),
            OutputDataPoint::Indicator {
                unixtime,
                name,
                value,
            } => {
                output_indicators
                    .entry(name.clone())
                    .or_default()
                    .push((*unixtime, *value));
            }
            OutputDataPoint::TrainedModelParams { .. } => {
                *trained = Some(serde_json::to_value(output)?);
            }
            OutputDataPoint::TimeAck { .. } => {
                return Err("on_step must not emit time_ack in the in-process simulator".into());
            }
            _ => {}
        }
    }
    Ok(())
}

fn is_price_overlay(name: &str) -> bool {
    let name = name.trim().to_lowercase();
    name == "sma" || name == "ema" || name.starts_with("bb_") || name.starts_with("fib_")
}

fn line_series(
    label: String,
    color: &str,
    data: Vec<Value>,
    markers: Option<&Vec<Value>>,
) -> Value {
    let mut value = json!({
        "type": "Line",
        "label": label,
        "options": {"color": color, "lineWidth": 2},
        "data": data,
    });
    if let Some(markers) = markers.filter(|items| !items.is_empty()) {
        value["markers"] = Value::Array(markers.clone());
    }
    value
}

fn inject_markers(charts: &mut [Value], markers: &BTreeMap<String, Vec<Value>>) {
    for chart in charts {
        let title = chart.get("title").and_then(Value::as_str).unwrap_or("");
        let ticker = markers
            .keys()
            .find(|ticker| title.starts_with(ticker.as_str()));
        let Some(items) = ticker.and_then(|ticker| markers.get(ticker)) else {
            continue;
        };
        let Some(series) = chart.get_mut("series").and_then(Value::as_array_mut) else {
            continue;
        };
        for line in series {
            if line.get("markers").is_none()
                || line.get("type").and_then(Value::as_str) == Some("Candlestick")
            {
                line["markers"] = Value::Array(items.clone());
            }
        }
    }
}

fn add_output_indicator_charts(
    charts: &mut Vec<Value>,
    primary_ticker: &str,
    base_scale: &str,
    points: &BTreeMap<String, Vec<(i64, f64)>>,
    event_times: &BTreeMap<i64, Value>,
    markers: Option<&Vec<Value>>,
) {
    const COLORS: [&str; 8] = [
        "#1e88e5", "#fb8c00", "#43a047", "#e53935", "#8e24aa", "#3949ab", "#00acc1", "#f4511e",
    ];
    for (index, (name, values)) in points.iter().enumerate() {
        let data: Vec<Value> = values
            .iter()
            .map(|(time, value)| json!({"time": event_times.get(time).cloned().unwrap_or(json!(time)), "value": value}))
            .collect();
        let line = line_series(
            format!("output:{name}"),
            COLORS[index % COLORS.len()],
            data,
            if is_price_overlay(name) {
                None
            } else {
                markers
            },
        );
        if is_price_overlay(name) {
            if let Some(price_chart) = charts.iter_mut().find(|chart| {
                chart.get("title").and_then(Value::as_str)
                    == Some(&format!("{primary_ticker} price ({base_scale})"))
            }) {
                if let Some(series) = price_chart.get_mut("series").and_then(Value::as_array_mut) {
                    series.push(line);
                    continue;
                }
            }
        }
        charts.push(json!({
            "type": "lightweight-charts",
            "title": format!("{primary_ticker} output:{name} ({base_scale})"),
            "description": "",
            "series": [line],
        }));
    }
}

fn max_drawdown(equity: &[f64]) -> f64 {
    let Some(first) = equity.first() else {
        return 0.0;
    };
    let mut peak = *first;
    let mut drawdown = 0.0_f64;
    for value in equity {
        peak = peak.max(*value);
        if peak > 0.0 {
            drawdown = drawdown.min(value / peak - 1.0);
        }
    }
    drawdown
}

fn periods_per_year(scale: &str) -> f64 {
    match scale {
        "1d" => 252.0,
        "1w" => 52.0,
        "1m" => 252.0 * 6.5 * 60.0,
        "15m" => 252.0 * 6.5 * 4.0,
        "1h" => 252.0 * 6.5,
        "4h" => 252.0 * 6.5 / 4.0,
        _ => 252.0,
    }
}

fn sharpe(equity: &[f64], scale: &str) -> Option<f64> {
    if equity.len() < 3 {
        return None;
    }
    let returns: Vec<f64> = equity
        .windows(2)
        .filter_map(|pair| {
            if pair[0] > 0.0 {
                Some(pair[1] / pair[0] - 1.0)
            } else {
                None
            }
        })
        .collect();
    if returns.len() < 2 {
        return None;
    }
    let mean = returns.iter().sum::<f64>() / returns.len() as f64;
    let variance = returns
        .iter()
        .map(|value| (value - mean).powi(2))
        .sum::<f64>()
        / (returns.len() - 1) as f64;
    (variance > 0.0).then(|| periods_per_year(scale).sqrt() * mean / variance.sqrt())
}

fn win_rate(trades: &[Trade]) -> Option<f64> {
    let mut longs: Vec<&Trade> = Vec::new();
    let mut shorts: Vec<&Trade> = Vec::new();
    let mut wins = 0_usize;
    let mut closed = 0_usize;
    for trade in trades {
        match trade.action.as_str() {
            "buy" => longs.push(trade),
            "sell" if !longs.is_empty() => {
                let entry = longs.remove(0);
                wins += usize::from(trade.price > entry.price);
                closed += 1;
            }
            "sell_short" => shorts.push(trade),
            "buy_to_cover" if !shorts.is_empty() => {
                let entry = shorts.remove(0);
                wins += usize::from(trade.price < entry.price);
                closed += 1;
            }
            _ => {}
        }
    }
    (closed > 0).then(|| wins as f64 / closed as f64)
}

fn progress_step() -> usize {
    env::var("SIMULATION_PROGRESS_STEP_PERCENT")
        .ok()
        .and_then(|raw| raw.parse().ok())
        .unwrap_or(10)
        .clamp(1, 25)
}

fn run_dataset<S: StrategyHandler>(
    workspace: &Path,
    handler: S,
    dataset: SimulationDataset,
    output_mode: OutputMode,
    emit_progress: bool,
) -> Result<Value, Box<dyn Error>> {
    let (metrics, _) = run_dataset_with_portfolio(
        workspace,
        handler,
        dataset,
        output_mode,
        emit_progress,
        None,
    )?;
    Ok(metrics)
}

fn run_dataset_with_portfolio<S: StrategyHandler>(
    workspace: &Path,
    mut handler: S,
    dataset: SimulationDataset,
    output_mode: OutputMode,
    emit_progress: bool,
    initial_portfolio: Option<Portfolio>,
) -> Result<(Value, Portfolio), Box<dyn Error>> {
    let startup = handler.startup();
    if emit_progress {
        eprintln!(
            "{}",
            json!({
                "simulation_ui": true,
                "event": "start",
                "workspace": workspace,
                "entry_script": "strategy.rs",
                "tickers": dataset.tickers,
                "base_scale": dataset.base_scale,
                "simulation_scale": dataset.simulation_scale,
                "total_units": dataset.total_units,
                "progress_step_percent": progress_step(),
            })
        );
    }

    let mut portfolio = initial_portfolio.unwrap_or_else(|| {
        Portfolio::new(
            dataset.initial_deposit,
            dataset.primary_ticker.clone(),
            dataset.max_leverage,
        )
    });
    let initial_equity = portfolio.equity(&BTreeMap::new());
    let mut warmup_portfolio = Portfolio::new(
        dataset.initial_deposit,
        dataset.primary_ticker.clone(),
        dataset.max_leverage,
    );
    let mut trained_input = trained_params(&workspace);
    let mut strategy_charts: Vec<Value> = Vec::new();
    let mut output_indicators: BTreeMap<String, Vec<(i64, f64)>> = BTreeMap::new();
    let mut trained_output: Option<Value> = None;
    if output_mode == OutputMode::Full {
        collect_outputs(
            &startup,
            &mut strategy_charts,
            &mut output_indicators,
            &mut trained_output,
        )?;
    }
    let catalog = startup
        .iter()
        .find_map(|output| {
            if let OutputDataPoint::IndicatorSeriesCatalog { series } = output {
                Some(serde_json::to_value(series).ok())
            } else {
                None
            }
        })
        .flatten();
    let mut recorded_inputs: Vec<Value> = Vec::new();
    let mut recorded_outputs: Vec<Value> = if output_mode == OutputMode::Full {
        vec![serde_json::to_value(&startup)?]
    } else {
        Vec::new()
    };
    let mut markers: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    let mut order_rows: Vec<Value> = Vec::new();
    let mut equity_data: Vec<Value> = Vec::new();
    let mut benchmark_data: Vec<Value> = Vec::new();
    let mut equity_values: Vec<f64> = Vec::new();
    let mut first_benchmark: Option<f64> = None;
    let mut event_times: BTreeMap<i64, Value> = BTreeMap::new();
    let mut position_values: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    let mut completed = 0_usize;
    let mut next_progress = 0_usize;
    let progress_increment = progress_step();

    for event in dataset.events {
        if output_mode == OutputMode::Full {
            event_times.insert(event.unixtime, event.chart_time.clone());
        }
        if event.mark_before_input && !event.warmup {
            portfolio.equity(&event.marks);
        }
        if event.invoke_strategy {
            let mut points = Vec::with_capacity(event.points.len() + 2);
            points.push(if event.warmup {
                warmup_portfolio.input_point()
            } else {
                portfolio.input_point()
            });
            if let Some(params) = trained_input.take() {
                points.push(params);
            }
            points.extend(event.points);
            let input = StrategyInput {
                unixtime: event.unixtime,
                points,
            };
            if output_mode == OutputMode::Full {
                recorded_inputs.push(serde_json::to_value(&input)?);
            }
            let outputs = handler.on_step(&input);
            if output_mode == OutputMode::Full {
                collect_outputs(
                    &outputs,
                    &mut strategy_charts,
                    &mut output_indicators,
                    &mut trained_output,
                )?;
            }
            let first_trade = portfolio.trades.len();
            if !event.warmup {
                portfolio.apply_outputs(
                    &outputs,
                    &event.fills,
                    event.unixtime,
                    !dataset.multi_ticker,
                );
            }
            if output_mode == OutputMode::Full {
                for trade in &portfolio.trades[first_trade..] {
                    markers
                        .entry(trade.ticker.clone())
                        .or_default()
                        .push(marker_for_trade(trade, &event.chart_time));
                    order_rows.push(order_row(trade));
                }
                recorded_outputs.push(serde_json::to_value(&outputs)?);
            }
        }
        if event.record_equity {
            portfolio.equity(&event.marks);
        }
        if event.base_close {
            completed += 1;
            let equity = portfolio.equity(&event.marks);
            equity_values.push(equity);
            if output_mode == OutputMode::Full {
                equity_data.push(json!({"time": event.chart_time, "value": equity}));
            }
            if let Some(close) = event.benchmark_close {
                let first = *first_benchmark.get_or_insert(close);
                if output_mode == OutputMode::Full {
                    benchmark_data.push(json!({
                        "time": event.chart_time,
                        "value": dataset.initial_deposit * close / first,
                    }));
                }
            }
            if output_mode == OutputMode::Full {
                let traded: BTreeSet<String> = portfolio.traded_tickers();
                for ticker in traded {
                    let value = portfolio.positions.get(&ticker).map_or(0.0, |position| {
                        position.qty
                            * event
                                .marks
                                .get(&ticker)
                                .or_else(|| portfolio.last_marks.get(&ticker))
                                .copied()
                                .unwrap_or(position.avg_entry_price)
                    });
                    position_values
                        .entry(ticker)
                        .or_default()
                        .push(json!({"time": event.chart_time, "value": value}));
                }
            }
            if emit_progress && dataset.total_units > 0 {
                let percent = completed.saturating_mul(100) / dataset.total_units;
                if percent >= next_progress {
                    eprintln!(
                        "{}",
                        json!({
                            "simulation_ui": true,
                            "event": "progress",
                            "percent": percent.min(100),
                            "completed_units": completed,
                            "total_units": dataset.total_units,
                            "unixtime": event.unixtime,
                            "base_row": event.base_row,
                        })
                    );
                    next_progress = percent.saturating_add(progress_increment).min(100);
                }
            }
        }
    }
    let final_outputs = handler.on_finish();
    if output_mode == OutputMode::Full {
        collect_outputs(
            &final_outputs,
            &mut strategy_charts,
            &mut output_indicators,
            &mut trained_output,
        )?;
        if !final_outputs.is_empty() {
            recorded_outputs.push(serde_json::to_value(&final_outputs)?);
        }
    }

    let final_equity = equity_values.last().copied().unwrap_or(initial_equity);
    let mut metrics = json!({
        "total_return": (final_equity / initial_equity - 1.0) * 100.0,
        "max_drawdown": max_drawdown(&equity_values) * 100.0,
        "num_trades": portfolio.trades.iter().filter(|trade| trade.valid).count(),
        "final_equity": final_equity,
    });
    if let Some(value) = sharpe(&equity_values, &dataset.base_scale) {
        metrics["sharpe_ratio"] = json!(value);
    }
    if let Some(value) = win_rate(&portfolio.trades) {
        metrics["win_rate"] = json!(value * 100.0);
    }

    if output_mode == OutputMode::Full {
        let mut charts = dataset.subscription_charts;
        inject_markers(&mut charts, &markers);
        add_output_indicator_charts(
            &mut charts,
            &dataset.primary_ticker,
            &dataset.base_scale,
            &output_indicators,
            &event_times,
            markers.get(&dataset.primary_ticker),
        );
        charts.extend(strategy_charts);
        let all_markers: Vec<Value> = markers.values().flatten().cloned().collect();
        charts.push(json!({
            "type": "lightweight-charts",
            "title": "Equity curve vs buy & hold",
            "description": "",
            "series": [
                line_series("Strategy equity".to_owned(), "#2962ff", equity_data, Some(&all_markers)),
                line_series(format!("Buy & hold {}", dataset.primary_ticker), "#9e9e9e", benchmark_data, None),
            ],
        }));
        if position_values.len() > 1 {
            let series: Vec<Value> = position_values
                .into_iter()
                .enumerate()
                .map(|(index, (ticker, data))| {
                    const COLORS: [&str; 8] = [
                        "#1e88e5", "#fb8c00", "#43a047", "#e53935", "#8e24aa", "#3949ab",
                        "#00acc1", "#f4511e",
                    ];
                    line_series(
                        format!("{ticker} position value"),
                        COLORS[index % COLORS.len()],
                        data,
                        None,
                    )
                })
                .collect();
            charts.push(json!({"type": "lightweight-charts", "title": "Current position value", "description": "", "series": series}));
        }
        charts.push(
            json!({"type": "table", "title": "Orders", "description": "", "rows": order_rows}),
        );
        let mut backtest = json!({"strategy_name": dataset.strategy_name, "charts": charts});
        if let Some(catalog) = catalog {
            if catalog.as_array().is_some_and(|items| !items.is_empty()) {
                backtest["indicator_series_catalog"] = catalog;
            }
        }
        write_pretty(&workspace.join("backtest.json"), &backtest)?;
        write_pretty(&workspace.join("metrics.json"), &metrics)?;
        write_pretty(
            &workspace.join("inputs.json"),
            &Value::Array(recorded_inputs),
        )?;
        write_pretty(
            &workspace.join("outputs.json"),
            &Value::Array(recorded_outputs),
        )?;
        if let Some(params) = trained_output {
            write_pretty(&workspace.join("trained_model_params.json"), &params)?;
        }
    }
    if emit_progress {
        eprintln!(
            "{}",
            json!({"simulation_ui": true, "event": "done", "percent": 100, "completed_units": completed, "total_units": dataset.total_units})
        );
    }
    Ok((metrics, portfolio))
}

fn run() -> Result<(), Box<dyn Error>> {
    let started = Instant::now();
    let workspace = env::current_dir()?;
    let handler = strategy::build_strategy()?;
    let startup = handler.startup();
    let dataset = load_dataset(&workspace, &startup)?;
    run_dataset(&workspace, handler, dataset, OutputMode::Full, true)?;
    eprintln!(
        "rust_simulation_seconds={:.3}",
        started.elapsed().as_secs_f64()
    );
    Ok(())
}

fn main() {
    if env!("CARGO_BIN_NAME").ends_with("_optimizer") {
        match optimizer_runtime::run() {
            Ok(()) => return,
            Err(optimizer_runtime::OptimizerError::Unsupported(message)) => {
                eprintln!("Rust optimizer unsupported: {message}");
                std::process::exit(78);
            }
            Err(error) => {
                eprintln!("Rust optimization failed: {error}");
                std::process::exit(1);
            }
        }
    }
    if let Err(error) = run() {
        eprintln!("Rust simulation failed: {error}");
        std::process::exit(1);
    }
}
