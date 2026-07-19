use crate::utils::{load_params, OutputDataPoint, StrategyHandler, StrategyInput};
use serde::Deserialize;
use std::error::Error;

#[derive(Deserialize)]
struct Params {
    ticker: String,
    scale: String,
}

pub struct GeneratedStrategy {
    params: Params,
}

impl StrategyHandler for GeneratedStrategy {
    fn startup(&self) -> Vec<OutputDataPoint> {
        vec![OutputDataPoint::TickerSubscription {
            id: "price".to_owned(),
            ticker: self.params.ticker.clone(),
            scale: self.params.scale.clone(),
            session: "all".to_owned(),
            update_scale: None,
            partial: false,
        }]
    }

    fn on_step(&mut self, _input: &StrategyInput) -> Vec<OutputDataPoint> {
        Vec::new()
    }
}

pub fn build_strategy() -> Result<GeneratedStrategy, Box<dyn Error>> {
    let params = load_params()?;
    Ok(GeneratedStrategy { params })
}
