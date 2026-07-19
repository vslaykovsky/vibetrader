#![allow(dead_code)]

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::error::Error;
use std::fs;
use std::io::{self, BufRead, BufReader, BufWriter, Read, Write};

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Ohlc {
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct PortfolioPosition {
    pub ticker: String,
    pub order_type: String,
    pub deposit_ratio: f64,
    pub volume_weighted_avg_entry_price: f64,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(tag = "kind")]
pub enum InputDataPoint {
    #[serde(rename = "ohlc")]
    Ohlc {
        #[serde(default)]
        id: String,
        ticker: String,
        ohlc: Ohlc,
        #[serde(default = "default_true")]
        closed: bool,
    },
    #[serde(rename = "indicator")]
    Indicator {
        #[serde(default)]
        id: String,
        name: String,
        value: f64,
        #[serde(default = "default_true")]
        closed: bool,
    },
    #[serde(rename = "portfolio")]
    Portfolio {
        #[serde(default)]
        cash: f64,
        #[serde(default)]
        equity: f64,
        #[serde(default)]
        buying_power: f64,
        positions: Vec<PortfolioPosition>,
    },
    #[serde(rename = "renko")]
    Renko {
        #[serde(default)]
        id: String,
        ticker: String,
        brick_size: f64,
        open: f64,
        close: f64,
        direction: String,
        #[serde(default = "default_true")]
        closed: bool,
    },
    #[serde(rename = "trained_model_params")]
    TrainedModelParams { name: String, data: Value },
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct StrategyInput {
    pub unixtime: i64,
    pub points: Vec<InputDataPoint>,
}

impl StrategyInput {
    pub fn ohlc(&self, subscription_id: &str) -> Option<(&str, &Ohlc, bool)> {
        self.points.iter().find_map(|point| match point {
            InputDataPoint::Ohlc {
                id,
                ticker,
                ohlc,
                closed,
            } if id == subscription_id => Some((ticker.as_str(), ohlc, *closed)),
            _ => None,
        })
    }

    pub fn indicator(&self, subscription_id: &str, output_name: &str) -> Option<(f64, bool)> {
        self.points.iter().find_map(|point| match point {
            InputDataPoint::Indicator {
                id,
                name,
                value,
                closed,
            } if id == subscription_id && name == output_name => Some((*value, *closed)),
            _ => None,
        })
    }

    pub fn portfolio(&self) -> Option<(f64, f64, f64, &[PortfolioPosition])> {
        self.points.iter().find_map(|point| match point {
            InputDataPoint::Portfolio {
                cash,
                equity,
                buying_power,
                positions,
            } => Some((*cash, *equity, *buying_power, positions.as_slice())),
            _ => None,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind")]
pub enum IndicatorSubscription {
    #[serde(rename = "sma")]
    Sma {
        id: String,
        ticker: String,
        scale: String,
        session: String,
        period: u32,
        update_scale: Option<String>,
        partial: bool,
    },
    #[serde(rename = "ema")]
    Ema {
        id: String,
        ticker: String,
        scale: String,
        session: String,
        period: u32,
        update_scale: Option<String>,
        partial: bool,
    },
    #[serde(rename = "macd")]
    Macd {
        id: String,
        ticker: String,
        scale: String,
        session: String,
        fast_period: u32,
        slow_period: u32,
        signal_period: u32,
        outputs: Vec<String>,
        update_scale: Option<String>,
        partial: bool,
    },
    #[serde(rename = "rsi")]
    Rsi {
        id: String,
        ticker: String,
        scale: String,
        session: String,
        period: u32,
        update_scale: Option<String>,
        partial: bool,
    },
    #[serde(rename = "atr")]
    Atr {
        id: String,
        ticker: String,
        scale: String,
        session: String,
        period: u32,
        update_scale: Option<String>,
        partial: bool,
    },
    #[serde(rename = "bb")]
    BollingerBands {
        id: String,
        ticker: String,
        scale: String,
        session: String,
        period: u32,
        std_dev: f64,
        outputs: Vec<String>,
        update_scale: Option<String>,
        partial: bool,
    },
    #[serde(rename = "stochastic")]
    Stochastic {
        id: String,
        ticker: String,
        scale: String,
        session: String,
        k_period: u32,
        k_slowing: u32,
        d_period: u32,
        outputs: Vec<String>,
        update_scale: Option<String>,
        partial: bool,
    },
    #[serde(rename = "fibonacci")]
    Fibonacci {
        id: String,
        ticker: String,
        scale: String,
        session: String,
        lookback: u32,
        outputs: Vec<String>,
        update_scale: Option<String>,
        partial: bool,
    },
    #[serde(rename = "renko")]
    Renko {
        id: String,
        ticker: String,
        scale: String,
        session: String,
        brick_size_mode: String,
        brick_size: Option<f64>,
        atr_period: u32,
        atr_multiplier: f64,
        update_scale: Option<String>,
        partial: bool,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndicatorSeriesCatalogEntry {
    pub name: String,
    pub description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind")]
pub enum OutputDataPoint {
    #[serde(rename = "indicator")]
    Indicator {
        unixtime: i64,
        name: String,
        value: f64,
    },
    #[serde(rename = "indicator_series_catalog")]
    IndicatorSeriesCatalog {
        series: Vec<IndicatorSeriesCatalogEntry>,
    },
    #[serde(rename = "market_order")]
    MarketOrder {
        ticker: String,
        direction: String,
        deposit_ratio: f64,
        short_explanation: String,
    },
    #[serde(rename = "ticker_subscription")]
    TickerSubscription {
        id: String,
        ticker: String,
        scale: String,
        session: String,
        update_scale: Option<String>,
        partial: bool,
    },
    #[serde(rename = "indicator_subscription")]
    IndicatorSubscription { indicator: IndicatorSubscription },
    #[serde(rename = "time_ack")]
    TimeAck { unixtime: i64 },
    #[serde(rename = "chart")]
    Chart { chart: Value },
    #[serde(rename = "trained_model_params")]
    TrainedModelParams { name: String, data: Value },
}

pub trait StrategyHandler {
    fn startup(&self) -> Vec<OutputDataPoint>;
    fn on_step(&mut self, input: &StrategyInput) -> Vec<OutputDataPoint>;
    fn on_finish(&mut self) -> Vec<OutputDataPoint> {
        Vec::new()
    }
}

pub fn load_params<T: DeserializeOwned>() -> Result<T, Box<dyn Error>> {
    let raw = fs::read_to_string("params.json")?;
    Ok(serde_json::from_str(&raw)?)
}

enum Transport {
    Jsonl,
    Msgpack,
}

impl Transport {
    fn from_env() -> Result<Self, Box<dyn Error>> {
        match std::env::var("STRATEGY_IPC_TRANSPORT")
            .unwrap_or_else(|_| "jsonl".to_owned())
            .to_lowercase()
            .as_str()
        {
            "jsonl" => Ok(Self::Jsonl),
            "msgpack" => Ok(Self::Msgpack),
            other => Err(format!("unsupported STRATEGY_IPC_TRANSPORT={other:?}").into()),
        }
    }
}

fn write_output<W: Write>(
    writer: &mut W,
    transport: &Transport,
    output: &[OutputDataPoint],
) -> Result<(), Box<dyn Error>> {
    match transport {
        Transport::Jsonl => {
            serde_json::to_writer(&mut *writer, output)?;
            writer.write_all(b"\n")?;
        }
        Transport::Msgpack => {
            let body = rmp_serde::to_vec_named(output)?;
            let size = u32::try_from(body.len())?;
            writer.write_all(&size.to_be_bytes())?;
            writer.write_all(&body)?;
        }
    }
    writer.flush()?;
    Ok(())
}

fn process_step<S: StrategyHandler, W: Write>(
    strategy: &mut S,
    writer: &mut W,
    transport: &Transport,
    input: StrategyInput,
) -> Result<(), Box<dyn Error>> {
    let mut output = strategy.on_step(&input);
    if output
        .iter()
        .any(|item| matches!(item, OutputDataPoint::TimeAck { .. }))
    {
        return Err("on_step must not emit time_ack; the Rust runtime adds it".into());
    }
    output.push(OutputDataPoint::TimeAck {
        unixtime: input.unixtime,
    });
    write_output(writer, transport, &output)
}

fn run_jsonl<S: StrategyHandler, W: Write>(
    strategy: &mut S,
    writer: &mut W,
    transport: &Transport,
) -> Result<(), Box<dyn Error>> {
    let stdin = io::stdin();
    let reader = BufReader::new(stdin.lock());
    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let input: StrategyInput = serde_json::from_str(&line)?;
        process_step(strategy, writer, transport, input)?;
    }
    Ok(())
}

fn run_msgpack<S: StrategyHandler, W: Write>(
    strategy: &mut S,
    writer: &mut W,
    transport: &Transport,
) -> Result<(), Box<dyn Error>> {
    let stdin = io::stdin();
    let mut reader = BufReader::new(stdin.lock());
    loop {
        let mut header = [0_u8; 4];
        let first = reader.read(&mut header[..1])?;
        if first == 0 {
            break;
        }
        reader.read_exact(&mut header[1..])?;
        let size = u32::from_be_bytes(header) as usize;
        let mut body = vec![0_u8; size];
        reader.read_exact(&mut body)?;
        let input: StrategyInput = rmp_serde::from_slice(&body)?;
        process_step(strategy, writer, transport, input)?;
    }
    Ok(())
}

pub fn run_strategy<S: StrategyHandler>(mut strategy: S) -> Result<(), Box<dyn Error>> {
    let transport = Transport::from_env()?;
    let stdout = io::stdout();
    let mut writer = BufWriter::new(stdout.lock());
    write_output(&mut writer, &transport, &strategy.startup())?;
    match transport {
        Transport::Jsonl => run_jsonl(&mut strategy, &mut writer, &transport)?,
        Transport::Msgpack => run_msgpack(&mut strategy, &mut writer, &transport)?,
    }
    let final_output = strategy.on_finish();
    if !final_output.is_empty() {
        write_output(&mut writer, &transport, &final_output)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn nested_indicator_subscription_matches_host_shape() {
        let output = OutputDataPoint::IndicatorSubscription {
            indicator: IndicatorSubscription::Rsi {
                id: "rsi".to_owned(),
                ticker: "SPY".to_owned(),
                scale: "1d".to_owned(),
                session: "all".to_owned(),
                period: 14,
                update_scale: None,
                partial: false,
            },
        };
        assert_eq!(
            serde_json::to_value(output).unwrap(),
            json!({
                "kind": "indicator_subscription",
                "indicator": {
                    "kind": "rsi",
                    "id": "rsi",
                    "ticker": "SPY",
                    "scale": "1d",
                    "session": "all",
                    "period": 14,
                    "update_scale": null,
                    "partial": false
                }
            })
        );
    }

    #[test]
    fn msgpack_input_deserializes_named_maps() {
        let body = rmp_serde::to_vec_named(&json!({
            "unixtime": 1700000000,
            "points": [{
                "kind": "ohlc",
                "id": "price",
                "ticker": "SPY",
                "ohlc": {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
                "closed": true
            }]
        }))
        .unwrap();
        let input: StrategyInput = rmp_serde::from_slice(&body).unwrap();
        assert_eq!(input.unixtime, 1_700_000_000);
        assert_eq!(input.ohlc("price").unwrap().1.close, 1.5);
    }
}
