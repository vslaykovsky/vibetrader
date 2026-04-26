# Scope overview

The team should prioritize:

1. **Cross-sectional derivatives-aware stat-arb across 50–100 liquid instruments**
2. **Funding, basis, OI, and liquidation regime signals**
3. **Medium-frequency momentum/reversal with volatility and liquidity filters**
4. **Multi-exchange consensus signals, but not cross-exchange arbitrage**
5. **Stablecoin/on-chain liquidity regime overlays**
6. **Event/calendar-aware risk filters**
7. **Simple execution model with conservative cost assumptions**

The core strategy should probably trade on **5-minute to 4-hour horizons**, not seconds.

---

# Priority ranking

| Rank | Research area                               | Revised priority | Why                                                                        |
| ---: | ------------------------------------------- | ---------------: | -------------------------------------------------------------------------- |
|    1 | Derivatives-aware cross-sectional stat-arb  |    **Very high** | Fits large instrument universe; does not require HFT execution             |
|    2 | Funding / basis / OI / liquidation regimes  |    **Very high** | Structural, observable, scalable across assets                             |
|    3 | Medium-frequency momentum / reversal        |         **High** | Works naturally across many liquid instruments                             |
|    4 | Multi-exchange consensus features           |         **High** | Uses exchange breadth without requiring arb execution                      |
|    5 | On-chain / stablecoin liquidity overlays    |  **Medium-high** | Useful as slower regime and sizing input                                   |
|    6 | Liquidation exhaustion signals              |  **Medium-high** | Good if traded over minutes/hours, not during the liquidation spike itself |
|    7 | Event-driven filters                        |       **Medium** | Useful for avoiding bad trades or sizing catalysts                         |
|    8 | Social / attention features                 |   **Low-medium** | Better for regime/crowding than direct entries                             |
|    9 | Raw order-book imbalance                    |    **Lower now** | Still useful, but only if downsampled and not execution-sensitive          |
|   10 | Market making                               | **Deprioritize** | Too execution-heavy                                                        |
|   11 | CEX-DEX / MEV arbitrage                     | **Deprioritize** | Too infrastructure-heavy                                                   |
|   12 | Sub-second lead-lag / HFT-style propagation | **Deprioritize** | Requires latency and routing expertise                                     |

---

# The best strategy shape

The final strategy should look like this:

> A **cross-sectional long/short or long/flat crypto MFT portfolio** across the most liquid instruments, using derivatives positioning, funding, basis, OI, liquidation history, medium-term price behavior, liquidity, and multi-exchange confirmation to rank assets every 15 minutes to 4 hours.

The strategy does **not** need to predict every tick. It needs to identify which assets are likely to outperform or underperform over the next few hours.

---

# Recommended alpha families

## 1. Derivatives-aware cross-sectional ranking

This should become the main research line.

Instead of asking:

> “Should we long BTC now?”

Ask:

> “Among the top 100 liquid instruments, which assets have the best expected risk-adjusted return over the next 1–8 hours?”

Useful features:

* funding rate percentile,
* predicted funding,
* funding change,
* perp-spot basis,
* OI change,
* OI change normalized by volume,
* liquidation imbalance,
* taker buy/sell imbalance,
* volume acceleration,
* realized volatility,
* spread and depth,
* cross-exchange funding dispersion,
* recent residual return versus sector or market beta.

Example hypotheses:

* Assets with rising OI, positive price momentum, but not-yet-extreme funding outperform over the next 1–4 hours.
* Assets with extreme positive funding, high OI, and weakening price momentum underperform.
* Assets with negative funding but positive spot-led price action outperform due to short squeeze risk.
* Assets with funding dispersion across exchanges mean-revert or converge in relative returns.
* Liquid alts with improving volume, OI, and basis outperform BTC/ETH during risk-on regimes.

This is much better suited to the team than pure market making.

---

## 2. Funding and basis regime engine

This remains top priority, but it should be implemented as both:

1. a **directional signal**, and
2. a **portfolio construction input**.

Do not only test “earn funding.” Test whether funding tells you about crowding and future returns.

Useful hypotheses:

* High funding is bullish during early trend formation but bearish when combined with stretched OI and slowing momentum.
* Negative funding plus stable price predicts short squeeze.
* Basis expansion with rising volume confirms continuation.
* Basis expansion without spot confirmation predicts reversal.
* Funding dispersion across venues predicts crowding and relative-value opportunity.

Recommended horizon:

* 1 hour,
* 4 hours,
* 8 hours,
* 24 hours.

Avoid ultra-short funding-window games unless execution improves.

---

## 3. Medium-frequency momentum and reversal

Given the ability to cover many instruments, this is attractive.

The alpha is not “buy what went up.” It should be regime-conditioned.

Research hypotheses:

* In high-liquidity, low-volatility regimes, 1–4 hour momentum works better.
* After liquidation cascades, 30-minute to 4-hour reversal works better.
* For small liquid alts, momentum has faster decay and needs stricter exits.
* BTC/ETH trend regime determines whether alt momentum should be traded aggressively or faded.
* Strong assets with low funding are better longs than strong assets with extreme funding.

Feature examples:

* 15m / 1h / 4h return,
* return residual versus BTC/ETH,
* volume-adjusted momentum,
* volatility-adjusted momentum,
* distance from rolling VWAP,
* intraday breakout strength,
* reversal after liquidation wick,
* trend consistency across exchanges.

---

## 4. Multi-exchange consensus, not arbitrage

This is an important reframing.

Do **not** start by trying to capture cross-exchange price dislocations. That is execution-heavy.

Instead, use multiple exchanges to build more reliable signals.

Examples:

* If OI is rising on Binance, Bybit, and OKX simultaneously, the signal is stronger.
* If funding is extreme on only one exchange, treat it as venue-specific noise.
* If volume leads on one exchange and later appears on others, use that as confirmation, not as latency arb.
* If an asset has fragmented liquidity and inconsistent prices, reduce size or exclude it.
* If one venue shows abnormal liquidations while others do not, check whether it is venue-specific forced flow.

Useful multi-exchange features:

* median funding across exchanges,
* funding dispersion,
* OI consensus,
* OI dispersion,
* price dispersion,
* volume share by exchange,
* exchange-specific taker imbalance,
* liquidation concentration,
* cross-exchange spread stability,
* depth-weighted fair price.

This uses the team’s willingness to include multiple exchanges without requiring elite execution.

---

## 5. Liquidation exhaustion and crowding signals

These are still attractive, but should be traded after the event, not during the fastest part of the cascade.

Good hypotheses:

* After a large long-liquidation event, if OI falls sharply and price stabilizes, reversal probability increases.
* If liquidation intensity remains high and depth does not refill, continuation risk remains high.
* If funding resets from extreme to neutral after a liquidation, trend may resume in the original direction.
* If liquidations are isolated to one venue, the signal is weaker than if liquidations are broad-based.
* Assets with repeated liquidation clusters and no recovery should be excluded or short-biased.

Recommended horizon:

* 15 minutes to 6 hours.

Avoid trying to catch the exact bottom/top of liquidation spikes.

---

## 6. On-chain and stablecoin overlays

These should not be the primary entry engine. They should modify regime and sizing.

Best uses:

* increase crypto beta exposure when stablecoin exchange inflows rise,
* reduce exposure when native-asset exchange inflows spike,
* overweight chains receiving large bridge inflows,
* use labeled wallet accumulation as confirmation,
* avoid assets with large exchange inflows before unlocks or known supply events.

Useful hypotheses:

* USDT/USDC exchange inflow surprise improves expected returns for BTC/ETH and liquid alts.
* Stablecoin inflows into a chain predict short-term outperformance of that chain’s liquid tokens.
* Native-token exchange inflows predict underperformance over the next few hours.
* Smart-money accumulation only works when confirmed by derivatives positioning.

---

# What to deprioritize now

## 1. Market making

The report correctly identifies toxicity-aware market making as promising, but for this team it should be delayed. Market making requires:

* queue-position modeling,
* fill probability modeling,
* adverse-selection modeling,
* fast cancel/replace logic,
* venue-specific microstructure expertise,
* reliable hedging.

Without that, market making can become a strategy that earns spread in backtests and loses to informed flow live.

Recommendation:

> Do not make market making a first-wave strategy. Build a simple execution model first.

---

## 2. CEX-DEX / MEV arbitrage

This is too infrastructure-heavy for the initial scope.

It requires:

* gas modeling,
* private orderflow,
* builder relationships,
* block inclusion probability,
* failed transaction modeling,
* sandwich/frontrun protection,
* chain-specific execution logic.

Recommendation:

> Use DEX data as signal input, not as an execution venue, until the team has specialist infrastructure.

---

## 3. Sub-second order book alpha

Raw order-flow and book imbalance can still be useful, but only after downsampling.

Instead of:

* 100 ms prediction,
* queue imbalance at top of book,
* maker/taker timing,
* immediate fill logic,

use:

* 1-minute order-flow imbalance,
* 5-minute aggressive flow,
* liquidity depletion over several minutes,
* volume imbalance by exchange,
* liquidation-adjusted flow pressure.

Recommendation:

> Keep order-flow features, but push the holding period out to 5–60 minutes.

---

# Revised final architecture

## Signal layer

Build signals in this order:

1. **Cross-sectional derivatives rank**
2. **Momentum/reversal rank**
3. **Crowding and liquidation rank**
4. **Liquidity and tradability rank**
5. **Multi-exchange confirmation rank**
6. **On-chain/stablecoin regime overlay**
7. **Event-risk filter**

Then combine them into an expected return score:

```text
Expected Return Score =
  derivatives score
+ momentum/reversal score
+ liquidation/crowding score
+ multi-exchange confirmation
+ on-chain regime adjustment
- liquidity/cost penalty
- event-risk penalty
```

---

## Portfolio construction

Use the large instrument universe as the edge.

Recommended structure:

* Universe: top 50–100 liquid perps per major exchange.
* Trade only instruments passing liquidity, spread, and data-quality filters.
* Rank instruments cross-sectionally.
* Long top bucket, short bottom bucket if shorting is clean.
* If shorting is expensive or unstable, use long/flat plus BTC/ETH hedge.
* Cap exposure by asset, sector, exchange, and funding direction.
* Neutralize BTC/ETH beta unless the regime model intentionally wants beta.
* Rebalance every 15 minutes, 1 hour, or 4 hours.
* Avoid excessive turnover with no-trade bands.

A practical first version:

```text
Every 1 hour:
1. Select top 100 liquid instruments.
2. Compute derivatives, momentum, liquidity, and crowding features.
3. Rank instruments by expected 4-hour return.
4. Long top 10–20.
5. Short bottom 10–20 or hedge with BTC/ETH/SOL.
6. Skip assets with bad spread, poor depth, abnormal data, or event risk.
7. Rebalance only when score change exceeds threshold.
```

---

## Execution approach

Because execution expertise is limited, use conservative execution rules:

* Prefer liquid perps.
* Use marketable limits rather than pure market orders.
* Avoid passive maker strategies initially.
* Use TWAP/VWAP-style slicing for larger orders.
* Do not trade during severe spread widening.
* Do not trade immediately into liquidation spikes.
* Penalize high-turnover signals heavily.
* Require expected edge to exceed estimated cost by a large multiple.
* Use one primary execution venue per instrument at first.
* Use other exchanges for signal confirmation before using them for routing.

Minimum execution model:

```text
Net expected alpha =
  forecast return
- taker fee
- half spread
- estimated slippage
- funding cost
- borrow/collateral cost
- adverse-selection buffer
```

Only trade when net expected alpha is clearly positive.

---

# Revised research roadmap

## Phase 1 — Broad-universe data and simple signals

Build:

* top-100 liquid universe,
* exchange-normalized funding,
* OI,
* liquidations,
* basis,
* volume,
* spread,
* depth,
* returns,
* volatility,
* BTC/ETH beta,
* sector tags.

Test:

* cross-sectional ranking,
* derivatives crowding,
* medium-frequency momentum,
* reversal after liquidation,
* funding-adjusted trend.

This is the highest ROI phase.

---

## Phase 2 — Multi-exchange feature expansion

Add:

* funding dispersion,
* OI consensus,
* price dispersion,
* exchange volume share,
* cross-exchange liquidation concentration,
* venue-specific flow imbalance.

Use these as signal-strength modifiers, not arb triggers.

---

## Phase 3 — Portfolio construction and risk

Build:

* beta-neutral portfolios,
* long/flat portfolios,
* sector-neutral portfolios,
* funding-aware position sizing,
* volatility targeting,
* turnover controls,
* exchange exposure caps.

This is where the breadth of 100 instruments becomes valuable.

---

## Phase 4 — Execution upgrade

Only after the first strategies work on conservative assumptions, improve:

* smart order routing,
* maker/taker choice,
* venue selection,
* queue-aware execution,
* passive quoting,
* liquidation-event execution.

Do not depend on this in the first version.

---

# Final recommendation

The revised mandate should be:

> Build a **broad, liquid, multi-exchange, derivatives-aware cross-sectional MFT strategy** with 15-minute to 24-hour horizons. Use funding, basis, OI, liquidations, momentum, reversal, liquidity, and multi-exchange confirmation to rank up to 100 instruments. Avoid execution-heavy market making, sub-second lead-lag, and CEX-DEX arbitrage until the team has stronger execution infrastructure.

The most promising first-wave strategy is:

> **A derivatives-aware cross-sectional momentum/reversal strategy over liquid perps**, where funding, OI, basis, and liquidations define crowding; medium-frequency returns define trend/reversal; multi-exchange data confirms signal quality; and a conservative execution model filters out trades whose expected edge does not clearly exceed costs.
