use super::portfolio::Portfolio;
use super::utils::{IndicatorSubscription, InputDataPoint, Ohlc, OutputDataPoint, StrategyHandler};
use super::{
    run_dataset, run_dataset_with_portfolio, strategy, OutputMode, SimulationDataset,
    SimulationEvent,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::env;
use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

const UNSUPPORTED_EXIT_CODE: i32 = 78;
const FIXED_STUDY_PARAMETERS: [&str; 12] = [
    "ticker",
    "scale",
    "simulation_scale",
    "start_date",
    "end_date",
    "initial_deposit",
    "provider",
    "max_leverage",
    "strategy_name",
    "description",
    "run_mode",
    "trained_model_params",
];
const TEMP_FILES: [&str; 4] = [
    ".rust-optimization-startups.json",
    ".rust-optimization-data.msgpack",
    ".rust-optimization-best.msgpack",
    ".rust-optimization-best-startup.json",
];

#[derive(Debug)]
pub enum OptimizerError {
    Unsupported(String),
    Failed(String),
}

impl fmt::Display for OptimizerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unsupported(message) | Self::Failed(message) => formatter.write_str(message),
        }
    }
}

#[derive(Clone, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
enum SearchSpec {
    Int { low: i64, high: i64 },
    Float { low: f64, high: f64 },
    Categorical { choices: Vec<Value> },
}

fn default_trials() -> usize {
    30
}

fn default_timeout() -> f64 {
    21_600.0
}

fn default_direction() -> String {
    "maximize".to_owned()
}

fn default_objective() -> String {
    "total_return".to_owned()
}

fn default_mode() -> String {
    "single".to_owned()
}

#[derive(Clone, Debug, Serialize)]
struct WalkForwardConfig {
    train_window_days: i64,
    test_window_days: i64,
    oos_total_days: i64,
}

#[derive(Deserialize)]
struct WalkForwardConfigInput {
    train_window_days: i64,
    test_window_days: i64,
    #[serde(default)]
    step_days: Option<i64>,
    oos_total_days: i64,
}

impl<'de> Deserialize<'de> for WalkForwardConfig {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let input = WalkForwardConfigInput::deserialize(deserializer)?;
        if let Some(step_days) = input.step_days {
            if step_days != input.test_window_days {
                return Err(serde::de::Error::custom(
                    "legacy step_days must equal test_window_days",
                ));
            }
        }
        Ok(Self {
            train_window_days: input.train_window_days,
            test_window_days: input.test_window_days,
            oos_total_days: input.oos_total_days,
        })
    }
}

#[derive(Deserialize)]
struct HyperoptConfig {
    search_space: BTreeMap<String, SearchSpec>,
    included_parameters: Option<Vec<String>>,
    excluded_parameters: Option<Vec<String>>,
    #[serde(default = "default_trials")]
    n_trials: usize,
    #[serde(default = "default_timeout")]
    timeout_seconds: f64,
    #[serde(default = "default_direction")]
    direction: String,
    #[serde(default = "default_objective")]
    objective_metric: String,
    seed: Option<i64>,
    #[serde(default = "default_mode")]
    mode: String,
    walk_forward: Option<WalkForwardConfig>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct CivilDate {
    year: i32,
    month: u32,
    day: u32,
}

impl CivilDate {
    fn parse(value: &str) -> Result<Self, OptimizerError> {
        let mut parts = value.split('-');
        let year = parts
            .next()
            .and_then(|part| part.parse().ok())
            .ok_or_else(|| failed(format!("invalid date {value:?}")))?;
        let month = parts
            .next()
            .and_then(|part| part.parse().ok())
            .ok_or_else(|| failed(format!("invalid date {value:?}")))?;
        let day = parts
            .next()
            .and_then(|part| part.parse().ok())
            .ok_or_else(|| failed(format!("invalid date {value:?}")))?;
        if parts.next().is_some() || !(1..=12).contains(&month) || !(1..=31).contains(&day) {
            return Err(failed(format!("invalid date {value:?}")));
        }
        let parsed = Self { year, month, day };
        if Self::from_days(parsed.days()) != parsed {
            return Err(failed(format!("invalid date {value:?}")));
        }
        Ok(parsed)
    }

    fn days(self) -> i64 {
        let mut year = self.year as i64;
        let month = self.month as i64;
        let day = self.day as i64;
        year -= i64::from(month <= 2);
        let era = if year >= 0 { year } else { year - 399 } / 400;
        let year_of_era = year - era * 400;
        let month_prime = month + if month > 2 { -3 } else { 9 };
        let day_of_year = (153 * month_prime + 2) / 5 + day - 1;
        let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
        era * 146_097 + day_of_era - 719_468
    }

    fn from_days(days: i64) -> Self {
        let days = days + 719_468;
        let era = if days >= 0 { days } else { days - 146_096 } / 146_097;
        let day_of_era = days - era * 146_097;
        let year_of_era =
            (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
        let mut year = year_of_era + era * 400;
        let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
        let month_prime = (5 * day_of_year + 2) / 153;
        let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
        let month = month_prime + if month_prime < 10 { 3 } else { -9 };
        year += i64::from(month <= 2);
        Self {
            year: year as i32,
            month: month as u32,
            day: day as u32,
        }
    }

    fn add_days(self, days: i64) -> Self {
        Self::from_days(self.days() + days)
    }

    fn unix_start(self) -> i64 {
        self.days() * 86_400
    }

    fn iso(self) -> String {
        format!("{:04}-{:02}-{:02}", self.year, self.month, self.day)
    }
}

#[derive(Clone)]
struct WalkForwardFold {
    number: usize,
    train_start: CivilDate,
    train_end: CivilDate,
    test_start: CivilDate,
    test_end: CivilDate,
}

#[derive(Deserialize)]
struct StudyBar {
    unixtime: i64,
    chart_time: Value,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
}

#[derive(Deserialize)]
struct TrialWindow {
    warmup_start: usize,
    simulation_start: usize,
    simulation_end: usize,
}

#[derive(Deserialize)]
struct StudyDataset {
    strategy_name: String,
    primary_ticker: String,
    base_scale: String,
    simulation_scale: String,
    initial_deposit: f64,
    max_leverage: f64,
    bars: Vec<StudyBar>,
    trial_windows: Vec<TrialWindow>,
}

struct SmaState {
    id: String,
    period: usize,
    values: VecDeque<f64>,
    sum: f64,
}

impl SmaState {
    fn push(&mut self, value: f64) -> Option<f64> {
        self.values.push_back(value);
        self.sum += value;
        if self.values.len() > self.period {
            if let Some(removed) = self.values.pop_front() {
                self.sum -= removed;
            }
        }
        (self.values.len() == self.period).then(|| self.sum / self.period as f64)
    }
}

struct StudyRng {
    state: u64,
}

impl StudyRng {
    fn new(seed: Option<i64>) -> Self {
        let fallback = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos() as u64)
            .unwrap_or(0x9e3779b97f4a7c15);
        Self {
            state: seed.map(|value| value as u64).unwrap_or(fallback),
        }
    }

    fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(0x9e3779b97f4a7c15);
        let mut value = self.state;
        value = (value ^ (value >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
        value = (value ^ (value >> 27)).wrapping_mul(0x94d049bb133111eb);
        value ^ (value >> 31)
    }

    fn unit(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / ((1_u64 << 53) as f64)
    }
}

fn failed(error: impl fmt::Display) -> OptimizerError {
    OptimizerError::Failed(error.to_string())
}

fn write_json(path: &Path, value: &Value) -> Result<(), OptimizerError> {
    let mut body = serde_json::to_string_pretty(value).map_err(failed)?;
    body.push('\n');
    fs::write(path, body).map_err(failed)
}

fn emit_ui(value: Value) {
    let mut payload = value.as_object().cloned().unwrap_or_default();
    payload.insert("hyperopt_ui".to_owned(), Value::Bool(true));
    eprintln!("{}", Value::Object(payload));
}

fn timing_payload(
    started: Instant,
    finished: usize,
    total: usize,
    timeout: f64,
) -> Map<String, Value> {
    let elapsed = started.elapsed().as_secs_f64();
    let mut payload = Map::from_iter([
        ("elapsed_seconds".to_owned(), json!(elapsed)),
        ("timeout_seconds".to_owned(), json!(timeout)),
    ]);
    if finished > 0 {
        let seconds_per_step = elapsed / finished as f64;
        payload.insert("seconds_per_step".to_owned(), json!(seconds_per_step));
        payload.insert(
            "eta_seconds".to_owned(),
            json!((seconds_per_step * total as f64).min(timeout).max(elapsed) - elapsed),
        );
    }
    payload
}

fn with_timing(
    mut payload: Value,
    started: Instant,
    finished: usize,
    total: usize,
    timeout: f64,
) -> Value {
    if let Some(object) = payload.as_object_mut() {
        object.extend(timing_payload(started, finished, total, timeout));
    }
    payload
}

fn active_space(config: &HyperoptConfig) -> Result<BTreeMap<String, SearchSpec>, OptimizerError> {
    if config.search_space.is_empty() {
        return Err(failed(
            "params-hyperopt.json needs a non-empty search_space object",
        ));
    }
    let known: BTreeSet<&str> = config.search_space.keys().map(String::as_str).collect();
    let mut active = if let Some(included) = &config.included_parameters {
        for key in included {
            if !known.contains(key.as_str()) {
                return Err(failed(format!(
                    "included_parameters contains unknown key {key:?}"
                )));
            }
        }
        included
            .iter()
            .map(|key| (key.clone(), config.search_space[key].clone()))
            .collect()
    } else {
        config.search_space.clone()
    };
    if let Some(excluded) = &config.excluded_parameters {
        for key in excluded {
            if !known.contains(key.as_str()) {
                return Err(failed(format!(
                    "excluded_parameters contains unknown key {key:?}"
                )));
            }
            active.remove(key);
        }
    }
    if active.is_empty() {
        return Err(failed("active hyperparameter search space is empty"));
    }
    Ok(active)
}

fn sample_value(rng: &mut StudyRng, spec: &SearchSpec) -> Result<Value, OptimizerError> {
    match spec {
        SearchSpec::Int { low, high } => {
            if high < low {
                return Err(failed("integer search-space high must be >= low"));
            }
            let width = (*high as i128 - *low as i128 + 1) as u128;
            let offset = (rng.next_u64() as u128 % width) as i128;
            Ok(json!((*low as i128 + offset) as i64))
        }
        SearchSpec::Float { low, high } => {
            if !low.is_finite() || !high.is_finite() || high < low {
                return Err(failed(
                    "float search-space bounds must be finite and ordered",
                ));
            }
            Ok(json!(low + (high - low) * rng.unit()))
        }
        SearchSpec::Categorical { choices } => {
            if choices.is_empty() {
                return Err(failed("categorical search-space choices must not be empty"));
            }
            Ok(choices[(rng.next_u64() as usize) % choices.len()].clone())
        }
    }
}

fn sample_candidates(
    base: &Map<String, Value>,
    space: &BTreeMap<String, SearchSpec>,
    count: usize,
    seed: Option<i64>,
) -> Result<Vec<Value>, OptimizerError> {
    let mut rng = StudyRng::new(seed);
    sample_candidates_with_rng(base, space, count, &mut rng)
}

fn sample_candidates_with_rng(
    base: &Map<String, Value>,
    space: &BTreeMap<String, SearchSpec>,
    count: usize,
    rng: &mut StudyRng,
) -> Result<Vec<Value>, OptimizerError> {
    let mut candidates = Vec::with_capacity(count);
    for _ in 0..count {
        let mut params = base.clone();
        for (key, spec) in space {
            params.insert(key.clone(), sample_value(rng, spec)?);
        }
        candidates.push(Value::Object(params));
    }
    Ok(candidates)
}

fn walk_forward_folds(
    base: &Map<String, Value>,
    config: &WalkForwardConfig,
) -> Result<Vec<WalkForwardFold>, OptimizerError> {
    if config.train_window_days <= 0 || config.test_window_days <= 0 || config.oos_total_days <= 0 {
        return Err(failed("walk-forward window sizes must be positive"));
    }
    let end = CivilDate::parse(
        base.get("end_date")
            .and_then(Value::as_str)
            .ok_or_else(|| failed("params.json is missing end_date"))?,
    )?;
    let oos_start = end.add_days(-(config.oos_total_days - 1));
    let mut test_start = oos_start;
    let mut folds = Vec::new();
    while test_start.days() <= end.days() {
        let proposed_end = test_start.add_days(config.test_window_days - 1);
        let test_end = if proposed_end.days() < end.days() {
            proposed_end
        } else {
            end
        };
        let train_end = test_start.add_days(-1);
        let train_start = train_end.add_days(-(config.train_window_days - 1));
        folds.push(WalkForwardFold {
            number: folds.len() + 1,
            train_start,
            train_end,
            test_start,
            test_end,
        });
        test_start = test_start.add_days(config.test_window_days);
    }
    if folds.is_empty() {
        return Err(failed("walk-forward produced no folds"));
    }
    Ok(folds)
}

fn adapter_command() -> Result<Command, OptimizerError> {
    let python = env::var("STRATEGY_PYTHON_EXECUTABLE").unwrap_or_else(|_| "python3".to_owned());
    let adapter = env::var("VIBETRADER_RUST_DATA_ADAPTER")
        .map_err(|_| failed("VIBETRADER_RUST_DATA_ADAPTER is not configured"))?;
    let mut command = Command::new(python);
    command.arg(adapter);
    Ok(command)
}

fn run_adapter(command: &mut Command) -> Result<(), OptimizerError> {
    let status = command.status().map_err(failed)?;
    if status.success() {
        return Ok(());
    }
    if status.code() == Some(UNSUPPORTED_EXIT_CODE) {
        return Err(OptimizerError::Unsupported(
            "strategy subscriptions require the compatibility optimizer".to_owned(),
        ));
    }
    Err(failed(format!("market-data adapter failed with {status}")))
}

fn indicator_padding_days(startup: &[OutputDataPoint]) -> i64 {
    let max_bars = startup.iter().fold(5_i64, |current, output| match output {
        OutputDataPoint::IndicatorSubscription {
            indicator: IndicatorSubscription::Sma { period, .. },
        } => current.max(i64::from(*period) * 3),
        _ => current,
    });
    max_bars.clamp(30, 500)
}

fn trial_window_for_dates(
    study: &StudyDataset,
    startup: &[OutputDataPoint],
    start: CivilDate,
    end: CivilDate,
) -> Result<TrialWindow, OptimizerError> {
    let start_unix = start.unix_start();
    let end_exclusive = end.add_days(1).unix_start();
    let warmup_unix = start
        .add_days(-indicator_padding_days(startup))
        .unix_start();
    let warmup_start = study
        .bars
        .iter()
        .position(|bar| bar.unixtime >= warmup_unix)
        .ok_or_else(|| failed("no bars in walk-forward warmup range"))?;
    let simulation_start = study
        .bars
        .iter()
        .position(|bar| bar.unixtime >= start_unix)
        .ok_or_else(|| failed("no bars in walk-forward simulation range"))?;
    let simulation_end = study
        .bars
        .iter()
        .rposition(|bar| bar.unixtime < end_exclusive)
        .ok_or_else(|| failed("no bars in walk-forward simulation range"))?;
    if simulation_start > simulation_end {
        return Err(failed("no bars in walk-forward simulation range"));
    }
    Ok(TrialWindow {
        warmup_start,
        simulation_start,
        simulation_end,
    })
}

fn build_trial_dataset(
    study: &StudyDataset,
    window: &TrialWindow,
    startup: &[OutputDataPoint],
    include_subscription_charts: bool,
    include_strategy_warmup: bool,
) -> Result<SimulationDataset, OptimizerError> {
    if window.warmup_start > window.simulation_start
        || window.simulation_start > window.simulation_end
        || window.simulation_end >= study.bars.len()
    {
        return Err(failed("invalid Rust optimization trial window"));
    }
    let mut ticker_ids = Vec::new();
    let mut sma_states = Vec::new();
    for output in startup {
        match output {
            OutputDataPoint::TickerSubscription {
                id,
                ticker,
                scale,
                partial,
                ..
            } if ticker == &study.primary_ticker && scale == &study.base_scale && !partial => {
                ticker_ids.push(id.clone());
            }
            OutputDataPoint::IndicatorSubscription {
                indicator:
                    IndicatorSubscription::Sma {
                        id,
                        ticker,
                        scale,
                        period,
                        partial,
                        ..
                    },
            } if ticker == &study.primary_ticker
                && scale == &study.base_scale
                && !partial
                && *period > 0 =>
            {
                sma_states.push(SmaState {
                    id: id.clone(),
                    period: *period as usize,
                    values: VecDeque::new(),
                    sum: 0.0,
                });
            }
            OutputDataPoint::IndicatorSubscription { .. }
            | OutputDataPoint::TickerSubscription { .. } => {
                return Err(OptimizerError::Unsupported(
                    "only closed, same-scale, single-ticker SMA subscriptions are supported"
                        .to_owned(),
                ));
            }
            _ => {}
        }
    }
    let event_start = if include_strategy_warmup {
        window.warmup_start
    } else {
        window.simulation_start
    };
    let mut events = Vec::with_capacity(window.simulation_end - event_start + 1);
    let mut candles = Vec::new();
    let mut indicator_data: Vec<Vec<Value>> = vec![Vec::new(); sma_states.len()];
    for index in window.warmup_start..=window.simulation_end {
        let bar = &study.bars[index];
        let sma_values: Vec<Option<f64>> = sma_states
            .iter_mut()
            .map(|state| state.push(bar.close))
            .collect();
        let warmup = index < window.simulation_start;
        if warmup && !include_strategy_warmup {
            continue;
        }
        if include_subscription_charts && !warmup {
            candles.push(json!({
                "time": bar.chart_time.clone(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
            }));
            for (series, value) in indicator_data.iter_mut().zip(&sma_values) {
                if let Some(value) = value {
                    series.push(json!({"time": bar.chart_time.clone(), "value": value}));
                }
            }
        }
        let mut points = Vec::with_capacity(ticker_ids.len() + sma_states.len());
        for id in &ticker_ids {
            points.push(InputDataPoint::Ohlc {
                id: id.clone(),
                ticker: study.primary_ticker.clone(),
                ohlc: Ohlc {
                    open: bar.open,
                    high: bar.high,
                    low: bar.low,
                    close: bar.close,
                    volume: bar.volume,
                },
                closed: true,
            });
        }
        for (state, value) in sma_states.iter().zip(sma_values) {
            if let Some(value) = value {
                points.push(InputDataPoint::Indicator {
                    id: state.id.clone(),
                    name: "sma".to_owned(),
                    value,
                    closed: true,
                });
            }
        }
        let prices = BTreeMap::from([(study.primary_ticker.clone(), bar.close)]);
        events.push(SimulationEvent {
            unixtime: bar.unixtime,
            points,
            fills: prices.clone(),
            marks: prices,
            invoke_strategy: true,
            record_equity: !warmup,
            base_close: !warmup,
            chart_time: bar.chart_time.clone(),
            benchmark_close: (!warmup).then_some(bar.close),
            base_row: (!warmup).then_some(index - window.simulation_start),
            mark_before_input: false,
            warmup,
        });
    }
    let subscription_charts = if include_subscription_charts {
        const COLORS: [&str; 8] = [
            "#1e88e5", "#fb8c00", "#43a047", "#e53935", "#8e24aa", "#3949ab", "#00acc1", "#f4511e",
        ];
        let mut series = vec![json!({
            "type": "Candlestick",
            "label": study.primary_ticker,
            "options": {"upColor": "#26a69a", "downColor": "#ef5350"},
            "data": candles,
        })];
        for (index, (state, data)) in sma_states.iter().zip(indicator_data).enumerate() {
            series.push(json!({
                "type": "Line",
                "label": format!("sma {}", state.period),
                "options": {"color": COLORS[index % COLORS.len()], "lineWidth": 2},
                "data": data,
            }));
        }
        vec![json!({
            "type": "lightweight-charts",
            "title": format!("{} price ({})", study.primary_ticker, study.base_scale),
            "description": "",
            "series": series,
        })]
    } else {
        Vec::new()
    };
    Ok(SimulationDataset {
        strategy_name: study.strategy_name.clone(),
        tickers: vec![study.primary_ticker.clone()],
        base_scale: study.base_scale.clone(),
        simulation_scale: study.simulation_scale.clone(),
        primary_ticker: study.primary_ticker.clone(),
        initial_deposit: study.initial_deposit,
        max_leverage: study.max_leverage,
        multi_ticker: false,
        total_units: window.simulation_end - window.simulation_start + 1,
        subscription_charts,
        events,
    })
}

fn metric_value(metrics: &Value, dotted: &str) -> Option<f64> {
    let mut current = metrics;
    for part in dotted.split('.') {
        current = current.get(part)?;
    }
    current.as_f64()
}

fn chart_time_to_unix(value: &Value) -> Option<i64> {
    if let Some(number) = value.as_f64() {
        if number.is_finite() {
            return Some(if number > 1e12 {
                (number / 1000.0) as i64
            } else {
                number as i64
            });
        }
    }
    let raw = value.as_str()?;
    if raw.len() < 10 {
        return None;
    }
    CivilDate::parse(&raw[..10]).ok().map(CivilDate::unix_start)
}

fn in_date_range(value: &Value, start: CivilDate, end: CivilDate) -> bool {
    chart_time_to_unix(value)
        .is_some_and(|unix| start.unix_start() <= unix && unix < end.add_days(1).unix_start())
}

fn crop_lwc_chart(chart: &Value, start: CivilDate, end: CivilDate, fold: usize) -> Option<Value> {
    let mut out = chart.clone();
    let object = out.as_object_mut()?;
    let is_equity =
        object.get("title").and_then(Value::as_str) == Some("Equity curve vs buy & hold");
    let source = object.get("series")?.as_array()?.clone();
    let mut cropped = Vec::new();
    for mut series in source {
        let Some(series_object) = series.as_object_mut() else {
            continue;
        };
        let data: Vec<Value> = series_object
            .get("data")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter(|point| {
                point
                    .get("time")
                    .is_some_and(|time| in_date_range(time, start, end))
            })
            .cloned()
            .collect();
        let markers: Vec<Value> = series_object
            .get("markers")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter(|marker| {
                marker
                    .get("time")
                    .is_some_and(|time| in_date_range(time, start, end))
            })
            .cloned()
            .collect();
        if data.is_empty() && markers.is_empty() {
            continue;
        }
        series_object.insert("data".to_owned(), Value::Array(data));
        if markers.is_empty() {
            series_object.remove("markers");
        } else {
            series_object.insert("markers".to_owned(), Value::Array(markers));
        }
        if series_object.get("type").and_then(Value::as_str) != Some("Candlestick") && !is_equity {
            let label = series_object
                .get("label")
                .and_then(Value::as_str)
                .unwrap_or_default();
            series_object.insert(
                "label".to_owned(),
                Value::String(if label.is_empty() {
                    format!("fold {fold}")
                } else {
                    format!("{label} fold {fold}")
                }),
            );
        }
        cropped.push(series);
    }
    if cropped.is_empty() {
        return None;
    }
    object.insert("series".to_owned(), Value::Array(cropped));
    object.remove("verticalMarkers");
    Some(out)
}

fn crop_table_chart(chart: &Value, start: CivilDate, end: CivilDate) -> Option<Value> {
    let mut out = chart.clone();
    let object = out.as_object_mut()?;
    let rows: Vec<Value> = object
        .get("rows")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|row| {
            ["time", "date", "datetime", "timestamp"]
                .iter()
                .find_map(|key| row.get(*key))
                .is_some_and(|time| in_date_range(time, start, end))
        })
        .cloned()
        .collect();
    if rows.is_empty() && object.get("title").and_then(Value::as_str) != Some("Orders") {
        return None;
    }
    object.insert("rows".to_owned(), Value::Array(rows));
    Some(out)
}

fn crop_backtest_doc(document: &Value, start: CivilDate, end: CivilDate, fold: usize) -> Value {
    let mut output = json!({
        "strategy_name": document
            .get("strategy_name")
            .and_then(Value::as_str)
            .unwrap_or("Walk-forward OOS"),
        "charts": [],
    });
    if let Some(catalog) = document
        .get("indicator_series_catalog")
        .and_then(Value::as_array)
    {
        output["indicator_series_catalog"] = Value::Array(catalog.clone());
    }
    let charts = output["charts"].as_array_mut().expect("charts is an array");
    for chart in document
        .get("charts")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let cropped = match chart.get("type").and_then(Value::as_str) {
            Some("lightweight-charts") => crop_lwc_chart(chart, start, end, fold),
            Some("table") => crop_table_chart(chart, start, end),
            _ => None,
        };
        if let Some(chart) = cropped {
            charts.push(chart);
        }
    }
    output
}

fn append_series(destination: &mut Value, source: &Value) {
    let source_series = source
        .get("series")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let Some(destination_series) = destination.get_mut("series").and_then(Value::as_array_mut)
    else {
        return;
    };
    for source_item in source_series {
        let key = (
            source_item
                .get("type")
                .and_then(Value::as_str)
                .unwrap_or_default(),
            source_item
                .get("label")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        );
        let existing = destination_series.iter_mut().find(|item| {
            item.get("type").and_then(Value::as_str).unwrap_or_default() == key.0
                && item
                    .get("label")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    == key.1
        });
        let Some(existing) = existing else {
            destination_series.push(source_item);
            continue;
        };
        if let Some(points) = source_item.get("data").and_then(Value::as_array) {
            if existing.get("data").and_then(Value::as_array).is_none() {
                existing["data"] = json!([]);
            }
            existing["data"]
                .as_array_mut()
                .expect("data is an array")
                .extend(points.clone());
        }
        if let Some(markers) = source_item.get("markers").and_then(Value::as_array) {
            if !markers.is_empty() {
                if existing.get("markers").and_then(Value::as_array).is_none() {
                    existing["markers"] = json!([]);
                }
                existing["markers"]
                    .as_array_mut()
                    .expect("markers is an array")
                    .extend(markers.clone());
            }
        }
    }
}

fn first_chart_time_in_range(chart: &Value, start: CivilDate, end: CivilDate) -> Option<Value> {
    chart
        .get("series")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .flat_map(|series| {
            series
                .get("data")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .filter_map(|point| point.get("time"))
        .filter(|time| in_date_range(time, start, end))
        .filter_map(|time| chart_time_to_unix(time).map(|unix| (unix, time.clone())))
        .min_by_key(|(unix, _)| *unix)
        .map(|(_, time)| time)
}

fn stitch_docs(fold_docs: &[Value], fold_infos: &[Value]) -> Value {
    let name = fold_docs
        .first()
        .and_then(|doc| doc.get("strategy_name"))
        .and_then(Value::as_str)
        .unwrap_or("Strategy");
    let mut output = json!({
        "strategy_name": format!("{name} walk-forward OOS"),
        "charts": [],
    });
    if let Some(catalog) = fold_docs
        .first()
        .and_then(|doc| doc.get("indicator_series_catalog"))
        .and_then(Value::as_array)
    {
        output["indicator_series_catalog"] = Value::Array(catalog.clone());
    }
    let mut by_title: BTreeMap<String, usize> = BTreeMap::new();
    for document in fold_docs {
        for chart in document
            .get("charts")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let title = chart
                .get("title")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned();
            let chart_to_add = chart.clone();
            let charts = output["charts"].as_array_mut().expect("charts is an array");
            let Some(index) = by_title.get(&title).copied() else {
                by_title.insert(title, charts.len());
                charts.push(chart_to_add);
                continue;
            };
            match chart.get("type").and_then(Value::as_str) {
                Some("lightweight-charts") => append_series(&mut charts[index], &chart_to_add),
                Some("table") => {
                    let rows = chart_to_add
                        .get("rows")
                        .and_then(Value::as_array)
                        .cloned()
                        .unwrap_or_default();
                    if charts[index]
                        .get("rows")
                        .and_then(Value::as_array)
                        .is_none()
                    {
                        charts[index]["rows"] = json!([]);
                    }
                    charts[index]["rows"]
                        .as_array_mut()
                        .expect("rows is an array")
                        .extend(rows);
                }
                _ => {}
            }
        }
    }
    for chart in output["charts"].as_array_mut().expect("charts is an array") {
        if chart.get("type").and_then(Value::as_str) != Some("lightweight-charts") {
            continue;
        }
        let mut markers = Vec::new();
        for (index, info) in fold_infos.iter().enumerate() {
            let Some(start) = info
                .get("test_start")
                .and_then(Value::as_str)
                .and_then(|raw| CivilDate::parse(raw).ok())
            else {
                continue;
            };
            let Some(end) = info
                .get("test_end")
                .and_then(Value::as_str)
                .and_then(|raw| CivilDate::parse(raw).ok())
            else {
                continue;
            };
            if let Some(time) = first_chart_time_in_range(chart, start, end) {
                markers.push(json!({
                    "time": time,
                    "label": format!("OOS {}", index + 1),
                    "color": "#f59e0b",
                }));
            }
        }
        if !markers.is_empty() {
            chart["verticalMarkers"] = Value::Array(markers);
        }
    }
    output
}

fn replace_continuous_benchmark(
    document: &mut Value,
    study: &StudyDataset,
    start: CivilDate,
    end: CivilDate,
    initial_deposit: f64,
) {
    let start_unix = start.unix_start();
    let end_exclusive = end.add_days(1).unix_start();
    let bars: Vec<&StudyBar> = study
        .bars
        .iter()
        .filter(|bar| bar.unixtime >= start_unix && bar.unixtime < end_exclusive)
        .collect();
    let Some(first_close) = bars
        .first()
        .map(|bar| bar.close)
        .filter(|close| *close > 0.0)
    else {
        return;
    };
    let data: Vec<Value> = bars
        .into_iter()
        .map(|bar| {
            json!({
                "time": bar.chart_time.clone(),
                "value": initial_deposit * bar.close / first_close,
            })
        })
        .collect();
    let Some(chart) = document
        .get_mut("charts")
        .and_then(Value::as_array_mut)
        .into_iter()
        .flatten()
        .find(|chart| {
            chart.get("title").and_then(Value::as_str) == Some("Equity curve vs buy & hold")
        })
    else {
        return;
    };
    let Some(series) = chart
        .get_mut("series")
        .and_then(Value::as_array_mut)
        .into_iter()
        .flatten()
        .find(|series| {
            series
                .get("label")
                .and_then(Value::as_str)
                .is_some_and(|label| label.starts_with("Buy & hold "))
        })
    else {
        return;
    };
    series["data"] = Value::Array(data);
}

fn portfolio_equity(portfolio: &Portfolio) -> f64 {
    portfolio.cash
        + portfolio
            .positions
            .iter()
            .map(|(ticker, position)| {
                position.qty
                    * portfolio
                        .last_marks
                        .get(ticker)
                        .copied()
                        .unwrap_or(position.avg_entry_price)
            })
            .sum::<f64>()
}

fn portfolio_positions(portfolio: Option<&Portfolio>) -> Value {
    let Some(portfolio) = portfolio else {
        return Value::Array(Vec::new());
    };
    Value::Array(
        portfolio
            .positions
            .iter()
            .map(|(ticker, position)| {
                let mark = portfolio
                    .last_marks
                    .get(ticker)
                    .copied()
                    .unwrap_or(position.avg_entry_price);
                json!({
                    "ticker": ticker,
                    "qty": position.qty,
                    "avg_entry_price": position.avg_entry_price,
                    "mark": mark,
                    "market_value": position.qty * mark,
                })
            })
            .collect(),
    )
}

fn equity_values(document: &Value) -> Vec<f64> {
    document
        .get("charts")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .find(|chart| {
            chart.get("title").and_then(Value::as_str) == Some("Equity curve vs buy & hold")
        })
        .and_then(|chart| chart.get("series"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .find(|series| series.get("label").and_then(Value::as_str) == Some("Strategy equity"))
        .and_then(|series| series.get("data"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|point| point.get("value").and_then(Value::as_f64))
        .collect()
}

fn order_rows(document: &Value) -> Vec<Value> {
    document
        .get("charts")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .find(|chart| {
            chart.get("type").and_then(Value::as_str) == Some("table")
                && chart.get("title").and_then(Value::as_str) == Some("Orders")
        })
        .and_then(|chart| chart.get("rows"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
}

fn order_win_rate(rows: &[Value]) -> Option<f64> {
    let mut longs: BTreeMap<String, VecDeque<f64>> = BTreeMap::new();
    let mut shorts: BTreeMap<String, VecDeque<f64>> = BTreeMap::new();
    let mut wins = 0_usize;
    let mut closed = 0_usize;
    for row in rows {
        if row.get("status").and_then(Value::as_str) == Some("invalid") {
            continue;
        }
        let ticker = row
            .get("ticker")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let direction = row
            .get("direction")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_lowercase();
        let Some(price) = row.get("price").and_then(Value::as_f64) else {
            continue;
        };
        let before = row
            .get("position_before_order")
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
        let after = row
            .get("position_after_order_filled")
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
        match direction.as_str() {
            "buy" => {
                if before < 0.0 {
                    if let Some(entry) = shorts.entry(ticker.clone()).or_default().pop_front() {
                        wins += usize::from(price < entry);
                        closed += 1;
                    }
                }
                if after > before && before >= 0.0 {
                    longs.entry(ticker).or_default().push_back(price);
                }
            }
            "sell" => {
                if before > 0.0 {
                    if let Some(entry) = longs.entry(ticker.clone()).or_default().pop_front() {
                        wins += usize::from(price > entry);
                        closed += 1;
                    }
                }
                if after < before && before <= 0.0 {
                    shorts.entry(ticker).or_default().push_back(price);
                }
            }
            _ => {}
        }
    }
    (closed > 0).then(|| wins as f64 / closed as f64 * 100.0)
}

fn stitched_metrics(
    document: &Value,
    base: &Map<String, Value>,
    scale: &str,
    initial_equity: Option<f64>,
) -> Value {
    let equity = equity_values(document);
    let initial = initial_equity
        .or_else(|| base.get("initial_deposit").and_then(Value::as_f64))
        .or_else(|| equity.first().copied())
        .unwrap_or(0.0);
    let final_equity = equity.last().copied().unwrap_or(initial);
    let rows = order_rows(document);
    json!({
        "total_return": (initial > 0.0).then(|| (final_equity / initial - 1.0) * 100.0),
        "sharpe_ratio": super::sharpe(&equity, scale),
        "max_drawdown": super::max_drawdown(&equity) * 100.0,
        "win_rate": order_win_rate(&rows),
        "num_trades": rows.iter().filter(|row| row.get("status").and_then(Value::as_str) != Some("invalid")).count(),
        "final_equity": final_equity,
    })
}

fn prepare_study_dataset(
    workspace: &Path,
    startups: Vec<Value>,
    data_params: &Value,
) -> Result<StudyDataset, OptimizerError> {
    let startups_path = workspace.join(".rust-optimization-startups.json");
    let study_path = workspace.join(".rust-optimization-data.msgpack");
    write_json(&workspace.join("params.json"), data_params)?;
    write_json(&startups_path, &Value::Array(startups))?;
    let mut command = adapter_command()?;
    command
        .arg("--workspace")
        .arg(workspace)
        .arg("--study-startups")
        .arg(&startups_path)
        .arg("--output")
        .arg(&study_path)
        .current_dir(workspace);
    run_adapter(&mut command)?;
    rmp_serde::from_slice(&fs::read(study_path).map_err(failed)?).map_err(failed)
}

struct FoldStudyResult {
    best_params: Value,
    best_value: f64,
    completed: usize,
}

fn params_with_dates(base: &Map<String, Value>, start: CivilDate, end: CivilDate) -> Value {
    let mut params = base.clone();
    params.insert("start_date".to_owned(), Value::String(start.iso()));
    params.insert("end_date".to_owned(), Value::String(end.iso()));
    Value::Object(params)
}

fn optimize_fold(
    workspace: &Path,
    study: &StudyDataset,
    candidates: &[Value],
    fold: &WalkForwardFold,
    n_folds: usize,
    config: &HyperoptConfig,
) -> Result<FoldStudyResult, OptimizerError> {
    let started = Instant::now();
    let maximize = config.direction != "minimize";
    let mut best_value = if maximize {
        f64::NEG_INFINITY
    } else {
        f64::INFINITY
    };
    let mut best_params: Option<Value> = None;
    let mut completed = 0_usize;
    for (index, candidate) in candidates.iter().enumerate() {
        if started.elapsed().as_secs_f64() >= config.timeout_seconds {
            emit_ui(with_timing(
                json!({
                    "event": "stopped",
                    "reason": "wall_timeout",
                    "trial": index,
                    "n_trials": config.n_trials,
                    "objective_metric": config.objective_metric,
                    "best_value": best_params.as_ref().map(|_| best_value),
                    "completed_trials": completed,
                    "fold": fold.number,
                    "n_folds": n_folds,
                }),
                started,
                index,
                config.n_trials,
                config.timeout_seconds,
            ));
            break;
        }
        write_json(&workspace.join("params.json"), candidate)?;
        let handler = strategy::build_strategy().map_err(failed)?;
        let startup = handler.startup();
        let window = trial_window_for_dates(study, &startup, fold.train_start, fold.train_end)?;
        let dataset = build_trial_dataset(study, &window, &startup, false, false)?;
        let metrics = match run_dataset(workspace, handler, dataset, OutputMode::MetricsOnly, false)
        {
            Ok(metrics) => metrics,
            Err(error) => {
                eprintln!(
                    "Rust walk-forward fold {} trial {} failed: {error}",
                    fold.number,
                    index + 1
                );
                emit_ui(with_timing(
                    json!({
                        "event": "trial",
                        "trial": index + 1,
                        "n_trials": config.n_trials,
                        "objective_metric": config.objective_metric,
                        "outcome": "sim_failed",
                        "best_value": best_params.as_ref().map(|_| best_value),
                        "completed_trials": completed,
                        "fold": fold.number,
                        "n_folds": n_folds,
                    }),
                    started,
                    index + 1,
                    config.n_trials,
                    config.timeout_seconds,
                ));
                continue;
            }
        };
        let Some(value) = metric_value(&metrics, &config.objective_metric) else {
            emit_ui(with_timing(
                json!({
                    "event": "trial",
                    "trial": index + 1,
                    "n_trials": config.n_trials,
                    "objective_metric": config.objective_metric,
                    "outcome": "missing_objective",
                    "best_value": best_params.as_ref().map(|_| best_value),
                    "completed_trials": completed,
                    "fold": fold.number,
                    "n_folds": n_folds,
                }),
                started,
                index + 1,
                config.n_trials,
                config.timeout_seconds,
            ));
            continue;
        };
        completed += 1;
        let better = best_params.is_none()
            || if maximize {
                value > best_value
            } else {
                value < best_value
            };
        if better {
            best_value = value;
            best_params = Some(candidate.clone());
        }
        emit_ui(with_timing(
            json!({
                "event": "trial",
                "trial": index + 1,
                "n_trials": config.n_trials,
                "objective_metric": config.objective_metric,
                "outcome": "completed",
                "trial_value": value,
                "new_best": better,
                "best_value": best_value,
                "completed_trials": completed,
                "fold": fold.number,
                "n_folds": n_folds,
            }),
            started,
            index + 1,
            config.n_trials,
            config.timeout_seconds,
        ));
    }
    Ok(FoldStudyResult {
        best_params: best_params.ok_or_else(|| {
            failed(format!(
                "walk-forward fold {} produced no successful trials",
                fold.number
            ))
        })?,
        best_value,
        completed,
    })
}

fn changed_params(best: &Value, base: &Map<String, Value>) -> Value {
    let mut changed = Map::new();
    if let Some(best) = best.as_object() {
        for (key, value) in best {
            if key != "start_date" && key != "end_date" && base.get(key) != Some(value) {
                changed.insert(key.clone(), value.clone());
            }
        }
    }
    Value::Object(changed)
}

fn run_walk_forward(
    workspace: &Path,
    base: &Map<String, Value>,
    config: &HyperoptConfig,
    space: &BTreeMap<String, SearchSpec>,
) -> Result<(), OptimizerError> {
    let walk_config = config
        .walk_forward
        .as_ref()
        .ok_or_else(|| failed("walk_forward mode requires walk_forward settings"))?;
    let folds = walk_forward_folds(base, walk_config)?;
    let overall_started = Instant::now();
    emit_ui(json!({
        "event": "walk_forward_start",
        "n_folds": folds.len(),
        "n_trials": config.n_trials,
        "objective_metric": config.objective_metric,
    }));

    let mut rng = StudyRng::new(config.seed);
    let mut candidates_by_fold = Vec::with_capacity(folds.len());
    let mut all_startups = Vec::with_capacity(folds.len() * config.n_trials);
    for fold in &folds {
        let train_base = params_with_dates(base, fold.train_start, fold.train_end);
        let candidates = sample_candidates_with_rng(
            train_base
                .as_object()
                .expect("params_with_dates returns an object"),
            space,
            config.n_trials,
            &mut rng,
        )?;
        for candidate in &candidates {
            write_json(&workspace.join("params.json"), candidate)?;
            let handler = strategy::build_strategy().map_err(failed)?;
            all_startups.push(serde_json::to_value(handler.startup()).map_err(failed)?);
        }
        candidates_by_fold.push(candidates);
    }
    let wide_params = params_with_dates(
        base,
        folds.first().expect("folds is non-empty").train_start,
        folds.last().expect("folds is non-empty").test_end,
    );
    let study = prepare_study_dataset(workspace, all_startups, &wide_params)?;
    let scale = base
        .get("scale")
        .or_else(|| base.get("simulation_scale"))
        .and_then(Value::as_str)
        .unwrap_or("1d");
    let initial_deposit = base
        .get("initial_deposit")
        .and_then(Value::as_f64)
        .ok_or_else(|| failed("params.json is missing initial_deposit"))?;
    let mut fold_docs = Vec::with_capacity(folds.len());
    let mut fold_infos = Vec::with_capacity(folds.len());
    let mut oos_portfolio: Option<Portfolio> = None;

    for (fold_index, fold) in folds.iter().enumerate() {
        emit_ui(json!({
            "event": "walk_forward_fold",
            "fold": fold.number,
            "n_folds": folds.len(),
            "phase": "train",
            "train_start": fold.train_start.iso(),
            "train_end": fold.train_end.iso(),
            "test_start": fold.test_start.iso(),
            "test_end": fold.test_end.iso(),
            "objective_metric": config.objective_metric,
        }));
        let result = optimize_fold(
            workspace,
            &study,
            &candidates_by_fold[fold_index],
            fold,
            folds.len(),
            config,
        )?;
        emit_ui(json!({
            "event": "walk_forward_fold",
            "fold": fold.number,
            "n_folds": folds.len(),
            "phase": "test",
            "objective_metric": config.objective_metric,
            "best_value": result.best_value,
            "completed_trials": result.completed,
        }));

        let mut oos_params = result
            .best_params
            .as_object()
            .cloned()
            .ok_or_else(|| failed("best walk-forward params must be an object"))?;
        oos_params.insert(
            "start_date".to_owned(),
            Value::String(fold.test_start.iso()),
        );
        oos_params.insert("end_date".to_owned(), Value::String(fold.test_end.iso()));
        let oos_params = Value::Object(oos_params);
        write_json(&workspace.join("params.json"), &oos_params)?;
        let handler = strategy::build_strategy().map_err(failed)?;
        let startup = handler.startup();
        let window = trial_window_for_dates(&study, &startup, fold.test_start, fold.test_end)?;
        let dataset = build_trial_dataset(&study, &window, &startup, true, true)?;
        let fold_start_equity = oos_portfolio
            .as_ref()
            .map(portfolio_equity)
            .unwrap_or(initial_deposit);
        let fold_start_cash = oos_portfolio
            .as_ref()
            .map(|portfolio| portfolio.cash)
            .unwrap_or(initial_deposit);
        let fold_start_positions = portfolio_positions(oos_portfolio.as_ref());
        let (_, next_portfolio) = run_dataset_with_portfolio(
            workspace,
            handler,
            dataset,
            OutputMode::Full,
            false,
            oos_portfolio.take(),
        )
        .map_err(failed)?;
        let fold_end_equity = portfolio_equity(&next_portfolio);
        let fold_end_cash = next_portfolio.cash;
        let fold_end_positions = portfolio_positions(Some(&next_portfolio));
        oos_portfolio = Some(next_portfolio);
        let raw_doc: Value =
            serde_json::from_slice(&fs::read(workspace.join("backtest.json")).map_err(failed)?)
                .map_err(failed)?;
        let cropped = crop_backtest_doc(&raw_doc, fold.test_start, fold.test_end, fold.number);
        let marker_info = json!({
            "test_start": fold.test_start.iso(),
            "test_end": fold.test_end.iso(),
        });
        let single_stitched = stitch_docs(
            std::slice::from_ref(&cropped),
            std::slice::from_ref(&marker_info),
        );
        let fold_metrics = stitched_metrics(&single_stitched, base, scale, Some(fold_start_equity));
        fold_infos.push(json!({
            "fold": fold.number,
            "train_start": fold.train_start.iso(),
            "train_end": fold.train_end.iso(),
            "test_start": fold.test_start.iso(),
            "test_end": fold.test_end.iso(),
            "train_objective_metric": config.objective_metric,
            "train_best_value": result.best_value,
            "completed_trials": result.completed,
            "best_params": changed_params(&result.best_params, base),
            "starting_cash": fold_start_cash,
            "starting_equity": fold_start_equity,
            "starting_positions": fold_start_positions,
            "ending_cash": fold_end_cash,
            "ending_equity": fold_end_equity,
            "ending_positions": fold_end_positions,
            "oos_metrics": fold_metrics,
        }));
        fold_docs.push(cropped);
    }

    let mut stitched = stitch_docs(&fold_docs, &fold_infos);
    replace_continuous_benchmark(
        &mut stitched,
        &study,
        folds.first().expect("folds is non-empty").test_start,
        folds.last().expect("folds is non-empty").test_end,
        initial_deposit,
    );
    let metrics = stitched_metrics(&stitched, base, scale, Some(initial_deposit));
    write_json(&workspace.join("backtest.json"), &stitched)?;
    write_json(&workspace.join("metrics.json"), &metrics)?;
    write_json(
        &workspace.join("walkforward.json"),
        &json!({
            "mode": "walk_forward",
            "objective_metric": config.objective_metric,
            "direction": config.direction,
            "n_trials": config.n_trials,
            "execution_model": "continuous_oos_portfolio",
            "walk_forward": walk_config,
            "folds": fold_infos,
            "metrics": metrics,
        }),
    )?;
    println!(
        "walk-forward OOS {}={} over {} folds",
        config.objective_metric,
        metrics
            .get(&config.objective_metric)
            .cloned()
            .unwrap_or(Value::Null),
        folds.len()
    );
    emit_ui(with_timing(
        json!({
            "event": "walk_forward_done",
            "objective_metric": config.objective_metric,
            "n_folds": folds.len(),
            "metrics": metrics,
        }),
        overall_started,
        folds.len(),
        folds.len(),
        config.timeout_seconds,
    ));
    for name in TEMP_FILES {
        let _ = fs::remove_file(workspace.join(name));
    }
    Ok(())
}

fn run_inner(workspace: &Path, original: &Value) -> Result<bool, OptimizerError> {
    let config: HyperoptConfig =
        serde_json::from_slice(&fs::read(workspace.join("params-hyperopt.json")).map_err(failed)?)
            .map_err(failed)?;
    if config.mode != "single" && config.mode != "walk_forward" {
        return Err(OptimizerError::Unsupported(format!(
            "unsupported optimization mode {:?}",
            config.mode
        )));
    }
    if config.n_trials == 0 {
        return Err(failed("n_trials must be positive"));
    }
    let base = original
        .as_object()
        .ok_or_else(|| failed("params.json must contain an object"))?;
    let space = active_space(&config)?;
    if let Some(key) = FIXED_STUDY_PARAMETERS
        .iter()
        .find(|key| space.contains_key(**key))
    {
        return Err(OptimizerError::Unsupported(format!(
            "{key} must remain fixed while reusing one market-data frame"
        )));
    }
    if config.mode == "walk_forward" {
        run_walk_forward(workspace, base, &config, &space)?;
        return Ok(true);
    }
    let candidates = sample_candidates(base, &space, config.n_trials, config.seed)?;
    let started = Instant::now();
    eprintln!(
        "Rust hyperopt start: mode=single objective={} direction={} trials={} wall={:.3}s seed={:?}",
        config.objective_metric,
        config.direction,
        config.n_trials,
        config.timeout_seconds,
        config.seed
    );
    emit_ui(json!({
        "event": "start",
        "objective_metric": config.objective_metric,
        "maximize": config.direction != "minimize",
        "n_trials": config.n_trials,
    }));

    let mut startups = Vec::with_capacity(candidates.len());
    for candidate in &candidates {
        write_json(&workspace.join("params.json"), candidate)?;
        let handler = strategy::build_strategy().map_err(failed)?;
        startups.push(serde_json::to_value(handler.startup()).map_err(failed)?);
    }
    let study_path = workspace.join(".rust-optimization-data.msgpack");
    let prepared_path = workspace.join(".rust-optimization-best.msgpack");
    let startup_path = workspace.join(".rust-optimization-best-startup.json");
    let study = prepare_study_dataset(
        workspace,
        startups,
        candidates
            .last()
            .ok_or_else(|| failed("Rust optimization has no candidates"))?,
    )?;
    if study.trial_windows.len() != candidates.len() {
        return Err(failed(
            "Rust study data has the wrong number of trial windows",
        ));
    }

    let maximize = config.direction != "minimize";
    let mut best_value = if maximize {
        f64::NEG_INFINITY
    } else {
        f64::INFINITY
    };
    let mut best_params: Option<Value> = None;
    let mut completed = 0_usize;
    let mut attempted = 0_usize;
    for (index, candidate) in candidates.iter().enumerate() {
        if started.elapsed().as_secs_f64() >= config.timeout_seconds {
            emit_ui(with_timing(
                json!({
                    "event": "stopped",
                    "reason": "wall_timeout",
                    "trial": index,
                    "n_trials": config.n_trials,
                    "objective_metric": config.objective_metric,
                    "best_value": best_params.as_ref().map(|_| best_value),
                    "completed_trials": completed,
                }),
                started,
                index,
                config.n_trials,
                config.timeout_seconds,
            ));
            break;
        }
        attempted = index + 1;
        write_json(&workspace.join("params.json"), candidate)?;
        let handler = strategy::build_strategy().map_err(failed)?;
        let startup = handler.startup();
        let dataset = match build_trial_dataset(
            &study,
            &study.trial_windows[index],
            &startup,
            false,
            false,
        ) {
            Ok(dataset) => dataset,
            Err(OptimizerError::Unsupported(message)) => {
                return Err(OptimizerError::Unsupported(message))
            }
            Err(error) => {
                eprintln!("Rust trial {} failed: {error}", index + 1);
                continue;
            }
        };
        let metrics = match run_dataset(workspace, handler, dataset, OutputMode::MetricsOnly, false)
        {
            Ok(metrics) => metrics,
            Err(error) => {
                eprintln!("Rust trial {} failed: {error}", index + 1);
                emit_ui(with_timing(
                    json!({
                        "event": "trial",
                        "trial": index + 1,
                        "n_trials": config.n_trials,
                        "objective_metric": config.objective_metric,
                        "outcome": "sim_failed",
                        "best_value": best_params.as_ref().map(|_| best_value),
                        "completed_trials": completed,
                    }),
                    started,
                    attempted,
                    config.n_trials,
                    config.timeout_seconds,
                ));
                continue;
            }
        };
        let Some(value) = metric_value(&metrics, &config.objective_metric) else {
            continue;
        };
        completed += 1;
        let better = best_params.is_none()
            || if maximize {
                value > best_value
            } else {
                value < best_value
            };
        if better {
            best_value = value;
            best_params = Some(candidate.clone());
        }
        emit_ui(with_timing(
            json!({
                "event": "trial",
                "trial": index + 1,
                "n_trials": config.n_trials,
                "objective_metric": config.objective_metric,
                "outcome": "completed",
                "trial_value": value,
                "new_best": better,
                "best_value": best_value,
                "completed_trials": completed,
            }),
            started,
            attempted,
            config.n_trials,
            config.timeout_seconds,
        ));
    }
    let best_params =
        best_params.ok_or_else(|| failed("no successful Rust optimization trials"))?;
    write_json(&workspace.join("params.json"), &best_params)?;
    let final_handler = strategy::build_strategy().map_err(failed)?;
    let final_startup = final_handler.startup();
    write_json(
        &startup_path,
        &serde_json::to_value(&final_startup).map_err(failed)?,
    )?;
    let mut prepare_best = adapter_command()?;
    prepare_best
        .arg("--workspace")
        .arg(workspace)
        .arg("--startup")
        .arg(&startup_path)
        .arg("--raw-data")
        .arg(&study_path)
        .arg("--output")
        .arg(&prepared_path)
        .current_dir(workspace);
    run_adapter(&mut prepare_best)?;
    let dataset: SimulationDataset =
        rmp_serde::from_slice(&fs::read(&prepared_path).map_err(failed)?).map_err(failed)?;
    run_dataset(workspace, final_handler, dataset, OutputMode::Full, false).map_err(failed)?;
    println!(
        "best {}={} over {} successful trials",
        config.objective_metric, best_value, completed
    );
    emit_ui(with_timing(
        json!({
            "event": "done",
            "objective_metric": config.objective_metric,
            "best_value": best_value,
            "completed_trials": completed,
            "n_trials": config.n_trials,
        }),
        started,
        attempted,
        config.n_trials,
        config.timeout_seconds,
    ));
    for name in TEMP_FILES {
        let _ = fs::remove_file(workspace.join(name));
    }
    Ok(false)
}

pub fn run() -> Result<(), OptimizerError> {
    let workspace: PathBuf = env::current_dir().map_err(failed)?;
    let params_path = workspace.join("params.json");
    let original_text = fs::read_to_string(&params_path).map_err(failed)?;
    let original: Value = serde_json::from_str(&original_text).map_err(failed)?;
    match run_inner(&workspace, &original) {
        Ok(restore_params) => {
            if restore_params {
                fs::write(&params_path, &original_text).map_err(failed)?;
            }
            Ok(())
        }
        Err(error) => {
            for name in TEMP_FILES {
                let _ = fs::remove_file(workspace.join(name));
            }
            if let Err(restore_error) = fs::write(&params_path, &original_text) {
                return Err(failed(format!(
                    "{error}; additionally failed to restore params.json: {restore_error}"
                )));
            }
            Err(error)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn equal_legacy_step_days_is_accepted_but_not_serialized() {
        let config: WalkForwardConfig = serde_json::from_value(json!({
            "train_window_days": 30,
            "test_window_days": 5,
            "step_days": 5,
            "oos_total_days": 60,
        }))
        .expect("equal legacy step_days should be accepted");

        assert_eq!(config.test_window_days, 5);
        let serialized = serde_json::to_value(config).expect("config should serialize");
        assert_eq!(
            serialized,
            json!({
                "train_window_days": 30,
                "test_window_days": 5,
                "oos_total_days": 60,
            })
        );
    }

    #[test]
    fn unequal_legacy_step_days_is_rejected() {
        let error = serde_json::from_value::<WalkForwardConfig>(json!({
            "train_window_days": 30,
            "test_window_days": 5,
            "step_days": 2,
            "oos_total_days": 60,
        }))
        .expect_err("unequal legacy step_days must be rejected");

        assert!(error
            .to_string()
            .contains("legacy step_days must equal test_window_days"));
    }
}
