use crate::utils::{
    load_params, IndicatorSubscription, InputDataPoint, OutputDataPoint, StrategyHandler,
    StrategyInput,
};
use serde::Deserialize;
use std::error::Error;

#[derive(Deserialize)]
struct Params {
    ticker: String,
    scale: String,
    fast_sma_period: u32,
    slow_sma_period: u32,
    position_fraction: f64,
}

pub struct GeneratedStrategy {
    params: Params,
    previous: Option<(f64, f64)>,
    fast: Option<f64>,
    slow: Option<f64>,
    is_long: bool,
}

impl StrategyHandler for GeneratedStrategy {
    fn startup(&self) -> Vec<OutputDataPoint> {
        vec![
            OutputDataPoint::TickerSubscription {
                id: "price".to_owned(),
                ticker: self.params.ticker.clone(),
                scale: self.params.scale.clone(),
                session: "all".to_owned(),
                update_scale: None,
                partial: false,
            },
            OutputDataPoint::IndicatorSubscription {
                indicator: IndicatorSubscription::Sma {
                    id: "fast_sma".to_owned(),
                    ticker: self.params.ticker.clone(),
                    scale: self.params.scale.clone(),
                    session: "all".to_owned(),
                    period: self.params.fast_sma_period,
                    update_scale: None,
                    partial: false,
                },
            },
            OutputDataPoint::IndicatorSubscription {
                indicator: IndicatorSubscription::Sma {
                    id: "slow_sma".to_owned(),
                    ticker: self.params.ticker.clone(),
                    scale: self.params.scale.clone(),
                    session: "all".to_owned(),
                    period: self.params.slow_sma_period,
                    update_scale: None,
                    partial: false,
                },
            },
        ]
    }

    fn on_step(&mut self, input: &StrategyInput) -> Vec<OutputDataPoint> {
        for point in &input.points {
            if let InputDataPoint::Portfolio { positions, .. } = point {
                self.is_long = positions.iter().any(|position| {
                    position.ticker == self.params.ticker && position.order_type == "long"
                });
            }
        }
        if let Some((value, true)) = input.indicator("fast_sma", "sma") {
            self.fast = Some(value);
        }
        if let Some((value, true)) = input.indicator("slow_sma", "sma") {
            self.slow = Some(value);
        }
        let (Some(fast), Some(slow)) = (self.fast, self.slow) else {
            return Vec::new();
        };
        let mut output = Vec::new();
        if let Some((previous_fast, previous_slow)) = self.previous {
            if !self.is_long && previous_fast <= previous_slow && fast > slow {
                output.push(OutputDataPoint::MarketOrder {
                    ticker: self.params.ticker.clone(),
                    direction: "buy".to_owned(),
                    deposit_ratio: self.params.position_fraction,
                    short_explanation: "Fast SMA crossed above slow SMA".to_owned(),
                });
            } else if self.is_long && previous_fast >= previous_slow && fast < slow {
                output.push(OutputDataPoint::MarketOrder {
                    ticker: self.params.ticker.clone(),
                    direction: "sell".to_owned(),
                    deposit_ratio: 1.0,
                    short_explanation: "Fast SMA crossed below slow SMA".to_owned(),
                });
            }
        }
        self.previous = Some((fast, slow));
        output
    }
}

pub fn build_strategy() -> Result<GeneratedStrategy, Box<dyn Error>> {
    Ok(GeneratedStrategy {
        params: load_params()?,
        previous: None,
        fast: None,
        slow: None,
        is_long: false,
    })
}
