# Backtest Root Cause Analysis — V75 Scalper (ICT Order Block)

**Backtest ID:** `9c937d1a-e2e2-40cb-b9ee-6bcc7582c4ac`
**Strategy:** V75 Scalper | **Algorithm:** `ict_order_block` (3-TF model)
**Instrument:** R_75 (Volatility 75 Index) | **Period:** Feb 1–Mar 1, 2026
**Timeframes:** Entry=5m, Structure=1H, Trend=4H | **Initial Capital:** $10,000

---

## 1. Summary Statistics

| Metric | Value | Verdict |
|--------|-------|---------|
| Total Trades | 464 | Overtrading |
| Win Rate | 40.09% (186W / 278L) | Below breakeven for avg RR |
| Net Profit | $294,982 | Misleading — outlier-dependent |
| Max Drawdown | 53.99% | Account-killing |
| Sharpe Ratio | 0.967 | Below 1.0 threshold |
| Profit Factor | 1.66 | Inflated by outliers |
| Average RR | 0.46 | Far below the configured min_rr of 2.0 |

---

## 2. Critical Finding #1: Outlier Dependency (Strategy is NOT profitable)

The entire net profit is driven by a handful of extreme outlier trades. Remove them and the strategy is deeply negative.

| Segment | Trades | Net PnL |
|---------|--------|---------|
| All trades | 464 | +$294,982 |
| Exclude top 1 (RR=80.75) | 463 | +$82,003 |
| Exclude top 3 (RR≥10) | 461 | +$38,474 |
| Exclude RR≥5 | 451 | **-$42,990** |
| Exclude RR>2 | 409 | **-$243,754** |

**The single trade #451 (RR=80.75, PnL=$212,979) accounts for 72% of all profit.** Without the top 13 trades (RR≥5), the strategy loses $42,990 — a -430% return on $10,000 capital.

**Root Cause:** Structural TP targets are set at the next opposing swing level on the structure timeframe. When the SL is tight (small OB zone) but the structural TP is hundreds of points away, the TP/SL ratio becomes absurd (up to 731x). These trades almost never hit TP — but when they do, the PnL is enormous and masks the underlying losing strategy.

---

## 3. Critical Finding #2: Structural TP Creates Unrealistic RR Targets

456 of 464 trades (98.3%) use structural TP rather than the configured min_rr of 2.0.

| TP/SL Ratio Bucket | Trades | Wins | Avg RR | Net PnL |
|---------------------|--------|------|--------|---------|
| 0–3x (normal) | 175 | 94 | 0.28 | +$80,799 |
| 3–10x (stretched) | 142 | 49 | 0.43 | +$74,243 |
| 10–50x (extreme) | 114 | 30 | -0.39 | -$80,724 |
| 50x+ (absurd) | 33 | 13 | 4.47 | +$220,664 |

The 33 trades with 50x+ TP/SL ratio generated +$220,664 — but this is entirely from 1-2 lucky outliers. The 114 trades in the 10-50x bucket lost $80,724 because the TP is so far away that price almost never reaches it, and the trade just sits open until it eventually reverses to SL.

**Code Evidence:** In `find_structural_target()`, the TP is set to the nearest opposing swing high/low on the structure TF. For a bullish OB with a 10-point SL zone, the next swing high might be 500+ points away — creating a 50:1 RR target that has near-zero probability of being hit on a 5m entry.

---

## 4. Critical Finding #3: Signal Clustering at Same OB Zone

133 clustered trade pairs were found — trades entering within 30 minutes of each other at the same SL level (same OB zone). This means the algorithm re-enters the same zone multiple times after getting stopped out.

**Worst zone examples:**

| SL Level | Direction | Trades at Zone | Losses | Zone PnL |
|----------|-----------|----------------|--------|----------|
| 34765.53 | BUY | 12 | 12 | -$37,287 |
| 34878.98 | BUY | 8 | 8 | -$24,431 |
| 34686.32 | SELL | 9 | 8 | -$18,030 |
| 28247.03 | BUY | 22 | 17 | +$2,644 |
| 25644.31 | SELL | 15 | 10 | -$1,013 |

The zone at SL=34765.53 generated 12 trades, ALL 12 lost, totaling -$37,287. The algorithm keeps detecting the same OB zone on every candle iteration and re-entering after each stop-out because there's no cooldown or zone invalidation after a failed retest.

**Code Evidence:** In `ICTOrderBlockAlgorithm.analyze()`, every iteration through the backtest loop re-detects all BOS and OB zones from the structure window. A zone that was already tested and failed is detected again on the next candle. The `is_valid` flag on OrderBlock is never set to `False` after a failed trade.

---

## 5. Critical Finding #4: Directional Imbalance

| Direction | Trades | Win Rate | Net PnL | Avg RR |
|-----------|--------|----------|---------|--------|
| BUY | 367 (79%) | 42.2% | +$285,933 | 0.37 |
| SELL | 97 (21%) | 32.0% | +$9,049 | 0.78 |

79% of trades are BUY. The trend bias filter is working (bullish bias → only BUY signals), but the SELL trades have a 32% win rate — significantly worse. The BUY profit is almost entirely from the outlier trades.

---

## 6. Critical Finding #5: Rapid Stop-Outs

269 of 464 trades (58%) hit their stop loss (RR = -1.0).

| Metric | Value |
|--------|-------|
| SL-hit trades | 269 (58%) |
| Avg duration of SL trades | 32.2 min |
| Median duration of SL trades | 10.0 min |
| SL trades under 5 min | 128 (47.6% of SL trades) |
| SL trades under 10 min | 148 (55.0% of SL trades) |

**128 trades hit SL within 5 minutes of entry.** This means the entry timing is poor — the algorithm enters at the OB zone retest, but price immediately continues through the zone and hits SL. The RETEST_LOOKBACK of 3 candles (15 min on 5m TF) is too aggressive — it confirms a "retest" when price merely touches the zone edge, without waiting for rejection confirmation.

---

## 7. Critical Finding #6: Consecutive Loss Streaks

| Metric | Value |
|--------|-------|
| Max consecutive losses | 17 |
| Avg loss streak length | 2.8 |
| Streaks of 5+ losses | 15 |
| Streaks of 10+ losses | 3 |

A 17-trade losing streak on a 2% risk-per-trade strategy means a theoretical 34% drawdown from streak alone (compounding makes it worse). Combined with signal clustering, these streaks often happen at the same OB zone — 12 consecutive losses at the same price level.

---

## 8. Critical Finding #7: Position Size Variance

| Bucket | Count |
|--------|-------|
| 0.01 lots (minimum) | 268 (57.8%) |
| 0.01–0.05 | 113 |
| 0.05–0.10 | 36 |
| 0.10–0.50 | 40 |
| 0.50–1.00 | 4 |
| Above 1.00 | 3 |

Position sizing is mathematically correct (2% risk / SL pips / pip_value), but the variance comes from wildly different SL distances:
- Min SL distance: 0.51 points → large position (tight zone)
- Max SL distance: 424.47 points → tiny position (wide zone)
- Average: 101.16 points | Median: 81.19 points

When a tight-SL trade (0.51 points) wins big, the position is large and PnL is massive. When a wide-SL trade (400+ points) loses, the position is tiny and loss is small. This creates a lottery-ticket dynamic — most trades are small losers, but rare tight-SL winners generate outsized returns.

---

## 9. Time-of-Day Analysis

| Hour (UTC) | Trades | Win % | Net PnL |
|------------|--------|-------|---------|
| 17:00 | 30 | 30.0% | +$214,050 |
| 07:00 | 22 | 59.1% | +$48,654 |
| 02:00 | 24 | 58.3% | +$30,787 |
| 14:00 | 14 | 78.6% | +$19,212 |
| 05:00 | 19 | 21.1% | -$18,350 |
| 00:00 | 22 | 22.7% | -$14,189 |

Hour 17 has 30 trades with only 30% win rate but +$214,050 — this is where the big outlier trade #451 occurred. Without it, hour 17 would be deeply negative. Hours 00, 05, 20, 21, 22 are consistently losing.

---

## 10. Root Causes Summary

### Algorithm-Level Issues

1. **No OB zone cooldown/invalidation** — Same zone generates unlimited re-entries after stop-outs. Need: mark zone invalid after N failed retests or after SL hit.

2. **Structural TP is uncapped** — `find_structural_target()` returns the next swing level regardless of distance. A swing high 500 points away with a 5-point SL creates a 100:1 RR that will almost never hit. Need: cap TP at max_rr * SL distance (e.g., 5x).

3. **Retest confirmation is too weak** — `check_retest()` only checks if price touched the zone edge in the last 3 candles. No rejection candle pattern, no volume confirmation, no wick-to-body ratio check. Need: require a rejection candle (long wick + close outside zone).

4. **No per-signal cooldown** — After a signal fires, the next candle can generate another signal at the same zone. Need: minimum time between signals at the same zone (e.g., 1 hour).

5. **No max concurrent zone limit** — The algorithm processes ALL detected BOS and ALL OB zones every iteration. Need: limit to the most recent N zones.

### Backtest Engine Issues

6. **No max position size cap** — Position sizing can produce lots > 1.0 when equity grows and SL is tight. Need: configurable max_lot_size parameter.

7. **No daily loss limit in backtest** — The live execution engine has a 5% daily loss limit, but the backtest engine doesn't enforce it. This means backtest results are more optimistic than live would be.

8. **Single-trade-at-a-time limitation** — The backtest only holds one trade at a time (`if open_trade is None`). This means it can't model the signal clustering problem accurately — in live, multiple signals could fire simultaneously.

---

## 11. Recommendations (Priority Order)

### P0 — Must Fix Before Any Live Deployment

1. **Cap structural TP** — Add `max_rr_cap` parameter (default: 5.0). If structural TP exceeds `entry + sl_dist * max_rr_cap`, fall back to `entry + sl_dist * max_rr_cap`. This eliminates the lottery-ticket dynamic.

2. **Zone invalidation after SL hit** — Track used OB zone IDs. After a trade at a zone hits SL, mark that zone as invalid for the remainder of the session. Prevents the 12-trades-at-same-zone problem.

3. **Signal cooldown** — After generating a signal, enforce a minimum cooldown period (e.g., 6 entry-TF candles = 30 min on 5m) before the same zone can generate another signal.

### P1 — Strongly Recommended

4. **Strengthen retest confirmation** — Require the retest candle to show rejection: close must be outside the OB zone (not just touch it), and the wick into the zone must be ≥50% of the candle range.

5. **Add daily loss limit to backtest** — Mirror the live execution engine's `max_daily_loss_pct` (5%) in the backtest. Stop generating new trades for the day after hitting the limit.

6. **Add max position size cap** — Configurable `max_lot_size` in backtest params, clamped after the risk-based calculation.

### P2 — Nice to Have

7. **Session filter** — The time-of-day data shows hours 00, 05, 20-22 are consistently losing. Add configurable trading session windows.

8. **Minimum SL distance** — Enforce a minimum SL distance (e.g., 10 points for R_75) to prevent lottery-ticket position sizing from extremely tight zones.

9. **Zone quality scoring** — Weight OB zones by: FVG confirmation, liquidity sweep, zone size relative to ATR, number of previous retests. Only trade zones above a quality threshold.

---

## 12. Expected Impact of P0 Fixes

If we apply the TP cap at 5x RR:
- The 33 trades with 50x+ TP/SL ratio would have their TP capped, converting most from "hold until SL" to "take profit at 5R"
- The 114 trades with 10-50x ratio would similarly be capped
- Net effect: more consistent, smaller winners instead of rare massive outliers
- Expected win rate increase: trades that currently sit open for hours waiting for an unreachable TP would instead close at 5R profit

If we apply zone invalidation:
- The 12-trade cluster at SL=34765.53 (-$37,287) would be reduced to 1-2 trades
- Estimated savings: $50,000-$80,000 in avoided repeated losses at failed zones
- Trade count would drop from 464 to approximately 300-350

Combined P0 fixes would transform this from an outlier-dependent lottery into a testable edge (or reveal there is no edge, which is equally valuable information before deploying real capital).
