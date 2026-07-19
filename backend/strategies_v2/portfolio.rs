use crate::utils::{InputDataPoint, OutputDataPoint, PortfolioPosition};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

const EPSILON: f64 = 1e-9;

#[derive(Debug, Clone)]
pub struct Position {
    pub qty: f64,
    pub avg_entry_price: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct Trade {
    pub unixtime: i64,
    pub ticker: String,
    pub direction: String,
    pub action: String,
    pub price: f64,
    pub qty: f64,
    pub deposit_ratio: f64,
    pub position_before_order: f64,
    pub position_after_order_filled: f64,
    pub reason: String,
    pub valid: bool,
}

impl Trade {
    pub fn label(&self) -> &'static str {
        match self.action.as_str() {
            "buy" => "BUY",
            "sell" => "SELL",
            "sell_short" => "SELL SHORT",
            "buy_to_cover" => "BUY TO COVER",
            _ => "INVALID",
        }
    }
}

pub struct Portfolio {
    initial_deposit: f64,
    primary_ticker: String,
    max_leverage: f64,
    pub cash: f64,
    pub positions: BTreeMap<String, Position>,
    pub trades: Vec<Trade>,
    pub last_marks: BTreeMap<String, f64>,
}

impl Portfolio {
    pub fn new(initial_deposit: f64, primary_ticker: String, max_leverage: f64) -> Self {
        Self {
            initial_deposit,
            primary_ticker,
            max_leverage,
            cash: initial_deposit,
            positions: BTreeMap::new(),
            trades: Vec::new(),
            last_marks: BTreeMap::new(),
        }
    }

    fn qty(&self, ticker: &str) -> f64 {
        self.positions
            .get(ticker)
            .map_or(0.0, |position| position.qty)
    }

    pub fn equity(&mut self, marks: &BTreeMap<String, f64>) -> f64 {
        for (ticker, price) in marks {
            if *price > 0.0 {
                self.last_marks.insert(ticker.clone(), *price);
            }
        }
        self.cash
            + self
                .positions
                .iter()
                .map(|(ticker, position)| {
                    let price = marks
                        .get(ticker)
                        .or_else(|| self.last_marks.get(ticker))
                        .copied()
                        .unwrap_or(position.avg_entry_price);
                    position.qty * price
                })
                .sum::<f64>()
    }

    fn gross_exposure(
        &self,
        marks: &BTreeMap<String, f64>,
        quantities: &BTreeMap<String, f64>,
    ) -> f64 {
        quantities
            .iter()
            .map(|(ticker, qty)| {
                let fallback = self
                    .positions
                    .get(ticker)
                    .map_or(0.0, |position| position.avg_entry_price);
                let price = marks
                    .get(ticker)
                    .or_else(|| self.last_marks.get(ticker))
                    .copied()
                    .unwrap_or(fallback);
                qty.abs() * price.max(0.0)
            })
            .sum()
    }

    fn exceeds_leverage(&mut self, ticker: &str, projected_qty: f64, price: f64) -> bool {
        let mut mark = BTreeMap::new();
        mark.insert(ticker.to_owned(), price);
        let equity = self.equity(&mark);
        if equity <= 0.0 {
            return true;
        }
        let mut quantities: BTreeMap<String, f64> = self
            .positions
            .iter()
            .map(|(name, position)| (name.clone(), position.qty))
            .collect();
        if projected_qty.abs() <= 1e-12 {
            quantities.remove(ticker);
        } else {
            quantities.insert(ticker.to_owned(), projected_qty);
        }
        self.gross_exposure(&mark, &quantities) > equity * self.max_leverage + EPSILON
    }

    fn invalid(
        &mut self,
        ticker: &str,
        direction: &str,
        deposit_ratio: f64,
        price: f64,
        qty: f64,
        unixtime: i64,
        explanation: &str,
        reason: &str,
    ) {
        let detail = if explanation.trim().is_empty() {
            reason.to_owned()
        } else {
            format!("{}: {}", explanation.trim(), reason)
        };
        let before = self.qty(ticker);
        self.trades.push(Trade {
            unixtime,
            ticker: ticker.to_owned(),
            direction: direction.trim().to_lowercase(),
            action: "invalid".to_owned(),
            price: price.max(0.0),
            qty: qty.max(0.0),
            deposit_ratio,
            position_before_order: before,
            position_after_order_filled: before,
            reason: detail,
            valid: false,
        });
    }

    #[allow(clippy::too_many_arguments)]
    fn apply_one(
        &mut self,
        ticker: &str,
        direction: &str,
        deposit_ratio: f64,
        price: f64,
        unixtime: i64,
        explanation: &str,
        cash_basis: Option<f64>,
    ) {
        if ticker.trim().is_empty() {
            let primary = self.primary_ticker.clone();
            self.invalid(
                &primary,
                direction,
                deposit_ratio,
                price,
                0.0,
                unixtime,
                explanation,
                "ticker is required",
            );
            return;
        }
        if !(deposit_ratio > 0.0 && deposit_ratio <= 1.0) {
            self.invalid(
                ticker,
                direction,
                deposit_ratio,
                price,
                0.0,
                unixtime,
                explanation,
                "deposit_ratio must be in (0, 1]",
            );
            return;
        }
        if price <= 0.0 {
            self.invalid(
                ticker,
                direction,
                deposit_ratio,
                price,
                0.0,
                unixtime,
                explanation,
                "price must be positive",
            );
            return;
        }
        let direction = direction.trim().to_lowercase();
        let before = self.qty(ticker);
        if direction == "buy" {
            if before < 0.0 {
                let qty = before.abs() * deposit_ratio;
                let spend = qty * price;
                if spend > self.cash + EPSILON {
                    self.invalid(
                        ticker,
                        &direction,
                        deposit_ratio,
                        price,
                        qty,
                        unixtime,
                        explanation,
                        "insufficient cash for market_order batch",
                    );
                    return;
                }
                self.cash -= spend;
                let after = before + qty;
                if after.abs() <= 1e-12 {
                    self.positions.remove(ticker);
                } else if let Some(position) = self.positions.get_mut(ticker) {
                    position.qty = after;
                }
                self.push_trade(
                    ticker,
                    &direction,
                    "buy_to_cover",
                    price,
                    qty,
                    deposit_ratio,
                    before,
                    if after.abs() <= 1e-12 { 0.0 } else { after },
                    unixtime,
                    explanation,
                );
                return;
            }
            let basis = cash_basis.unwrap_or(self.cash);
            let spend = basis * deposit_ratio;
            if spend <= 0.0 {
                return;
            }
            let qty = spend / price;
            if spend > self.cash + EPSILON {
                self.invalid(
                    ticker,
                    &direction,
                    deposit_ratio,
                    price,
                    qty,
                    unixtime,
                    explanation,
                    "insufficient cash for market_order batch",
                );
                return;
            }
            if self.exceeds_leverage(ticker, before + qty, price) {
                self.invalid(
                    ticker,
                    &direction,
                    deposit_ratio,
                    price,
                    qty,
                    unixtime,
                    explanation,
                    "max_leverage exceeded",
                );
                return;
            }
            self.cash -= spend;
            if let Some(position) = self.positions.get_mut(ticker) {
                let total = position.qty + qty;
                position.avg_entry_price =
                    (position.avg_entry_price * position.qty + price * qty) / total;
                position.qty = total;
            } else {
                self.positions.insert(
                    ticker.to_owned(),
                    Position {
                        qty,
                        avg_entry_price: price,
                    },
                );
            }
            self.push_trade(
                ticker,
                &direction,
                "buy",
                price,
                qty,
                deposit_ratio,
                before,
                before + qty,
                unixtime,
                explanation,
            );
        } else if direction == "sell" {
            if before > 0.0 {
                let qty = before * deposit_ratio;
                self.cash += qty * price;
                let after = before - qty;
                if after <= 1e-12 {
                    self.positions.remove(ticker);
                } else if let Some(position) = self.positions.get_mut(ticker) {
                    position.qty = after;
                }
                self.push_trade(
                    ticker,
                    &direction,
                    "sell",
                    price,
                    qty,
                    deposit_ratio,
                    before,
                    if after <= 1e-12 { 0.0 } else { after },
                    unixtime,
                    explanation,
                );
                return;
            }
            let mut basis = if self.last_marks.is_empty() {
                self.initial_deposit
            } else {
                let marks = self.last_marks.clone();
                self.equity(&marks)
            };
            if basis <= 0.0 {
                basis = self.initial_deposit;
            }
            let qty = basis * deposit_ratio / price;
            if self.exceeds_leverage(ticker, before - qty, price) {
                self.invalid(
                    ticker,
                    &direction,
                    deposit_ratio,
                    price,
                    qty,
                    unixtime,
                    explanation,
                    "max_leverage exceeded",
                );
                return;
            }
            self.cash += qty * price;
            if let Some(position) = self.positions.get_mut(ticker) {
                let open_qty = position.qty.abs();
                let total = open_qty + qty;
                position.avg_entry_price =
                    (position.avg_entry_price * open_qty + price * qty) / total;
                position.qty -= qty;
            } else {
                self.positions.insert(
                    ticker.to_owned(),
                    Position {
                        qty: -qty,
                        avg_entry_price: price,
                    },
                );
            }
            self.push_trade(
                ticker,
                &direction,
                "sell_short",
                price,
                qty,
                deposit_ratio,
                before,
                before - qty,
                unixtime,
                explanation,
            );
        } else {
            self.invalid(
                ticker,
                &direction,
                deposit_ratio,
                price,
                0.0,
                unixtime,
                explanation,
                &format!("Unsupported direction: {direction:?}"),
            );
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn push_trade(
        &mut self,
        ticker: &str,
        direction: &str,
        action: &str,
        price: f64,
        qty: f64,
        deposit_ratio: f64,
        before: f64,
        after: f64,
        unixtime: i64,
        reason: &str,
    ) {
        self.trades.push(Trade {
            unixtime,
            ticker: ticker.to_owned(),
            direction: direction.to_owned(),
            action: action.to_owned(),
            price,
            qty,
            deposit_ratio,
            position_before_order: before,
            position_after_order_filled: after,
            reason: reason.to_owned(),
            valid: true,
        });
    }

    pub fn apply_outputs(
        &mut self,
        outputs: &[OutputDataPoint],
        prices: &BTreeMap<String, f64>,
        unixtime: i64,
        single_ticker: bool,
    ) {
        let mut pending: Vec<(&str, &str, f64, &str, f64)> = Vec::new();
        for output in outputs {
            let OutputDataPoint::MarketOrder {
                ticker,
                direction,
                deposit_ratio,
                short_explanation,
            } = output
            else {
                continue;
            };
            let price = prices.get(ticker).copied().or_else(|| {
                if single_ticker {
                    prices.values().next().copied()
                } else {
                    None
                }
            });
            if direction.trim().eq_ignore_ascii_case("buy") && self.qty(ticker) >= 0.0 {
                if let Some(price) = price {
                    pending.push((ticker, direction, *deposit_ratio, short_explanation, price));
                    continue;
                }
            }
            self.flush_buys(&mut pending, unixtime);
            match price {
                Some(price) => self.apply_one(
                    ticker,
                    direction,
                    *deposit_ratio,
                    price,
                    unixtime,
                    short_explanation,
                    None,
                ),
                None => self.invalid(
                    ticker,
                    direction,
                    *deposit_ratio,
                    0.0,
                    0.0,
                    unixtime,
                    short_explanation,
                    &format!("no fill price available for ticker {ticker:?}"),
                ),
            }
        }
        self.flush_buys(&mut pending, unixtime);
    }

    fn flush_buys(&mut self, pending: &mut Vec<(&str, &str, f64, &str, f64)>, unixtime: i64) {
        if pending.is_empty() {
            return;
        }
        let batch_cash = self.cash;
        if pending
            .iter()
            .map(|(_, _, ratio, _, _)| ratio * batch_cash)
            .sum::<f64>()
            > self.cash + EPSILON
        {
            for (ticker, direction, ratio, explanation, price) in pending.drain(..) {
                self.invalid(
                    ticker,
                    direction,
                    ratio,
                    price,
                    ratio * batch_cash / price,
                    unixtime,
                    explanation,
                    "market_order buy batch exceeds available cash",
                );
            }
        } else {
            for (ticker, direction, ratio, explanation, price) in pending.drain(..) {
                self.apply_one(
                    ticker,
                    direction,
                    ratio,
                    price,
                    unixtime,
                    explanation,
                    Some(batch_cash),
                );
            }
        }
    }

    pub fn input_point(&mut self) -> InputDataPoint {
        let marks = self.last_marks.clone();
        let equity = if marks.is_empty() {
            self.initial_deposit
        } else {
            self.equity(&marks)
        };
        let denominator = if equity > 0.0 {
            equity
        } else {
            self.initial_deposit
        };
        let positions = self
            .positions
            .iter()
            .map(|(ticker, position)| {
                let price = self
                    .last_marks
                    .get(ticker)
                    .copied()
                    .unwrap_or(position.avg_entry_price);
                PortfolioPosition {
                    ticker: ticker.clone(),
                    order_type: if position.qty >= 0.0 { "long" } else { "short" }.to_owned(),
                    deposit_ratio: (position.qty.abs() * price / denominator).clamp(0.0, 1.0),
                    volume_weighted_avg_entry_price: position.avg_entry_price,
                }
            })
            .collect();
        InputDataPoint::Portfolio {
            cash: self.cash.max(0.0),
            equity,
            buying_power: self.cash.max(0.0),
            positions,
        }
    }

    pub fn traded_tickers(&self) -> BTreeSet<String> {
        self.trades
            .iter()
            .filter(|trade| trade.valid)
            .map(|trade| trade.ticker.clone())
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn order(direction: &str, ratio: f64) -> OutputDataPoint {
        OutputDataPoint::MarketOrder {
            ticker: "SPY".to_owned(),
            direction: direction.to_owned(),
            deposit_ratio: ratio,
            short_explanation: "test".to_owned(),
        }
    }

    #[test]
    fn buy_then_sell_round_trip_matches_cash_accounting() {
        let mut portfolio = Portfolio::new(10_000.0, "SPY".to_owned(), 1.0);
        let prices = BTreeMap::from([("SPY".to_owned(), 100.0)]);
        portfolio.apply_outputs(&[order("buy", 1.0)], &prices, 1, true);
        assert_eq!(portfolio.cash, 0.0);
        assert_eq!(portfolio.positions["SPY"].qty, 100.0);

        let exit_prices = BTreeMap::from([("SPY".to_owned(), 110.0)]);
        portfolio.apply_outputs(&[order("sell", 1.0)], &exit_prices, 2, true);
        assert_eq!(portfolio.cash, 11_000.0);
        assert!(!portfolio.positions.contains_key("SPY"));
        assert_eq!(portfolio.trades.len(), 2);
    }

    #[test]
    fn buy_batch_over_available_cash_records_invalid_orders() {
        let mut portfolio = Portfolio::new(10_000.0, "SPY".to_owned(), 1.0);
        let prices = BTreeMap::from([("SPY".to_owned(), 100.0)]);
        portfolio.apply_outputs(&[order("buy", 0.75), order("buy", 0.75)], &prices, 1, true);
        assert_eq!(portfolio.cash, 10_000.0);
        assert!(portfolio.positions.is_empty());
        assert_eq!(portfolio.trades.len(), 2);
        assert!(portfolio.trades.iter().all(|trade| !trade.valid));
    }
}
