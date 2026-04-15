# Round 1 Research Report: Ash-Coated Osmium & Intarian Pepper Root

## Executive Summary

Two products with fundamentally different market structures:
- **ASH_COATED_OSMIUM**: Near-static fair value (~10000), wide spread (~16), slow mean reversion. Edge from market making.
- **INTARIAN_PEPPER_ROOT**: Deterministic linear drift (+0.1/step, ~1000/day), tight after detrending. Edge from trend riding + spread capture.

Optimized strategy: **205,116 total PnL** across 3 days (~68k/day), up from 30,545 baseline.

---

## 1. Market Structure: ASH_COATED_OSMIUM

### Price Behavior
- Fair value oscillates around ~10000 with slow, mean-reverting deviations
- Intraday range: ~40 points (9977-10023)
- Net daily drift: negligible (-16, -1, -6 across days -2, -1, 0)
- Returns autocorrelation at lag 1: **-0.49** (bid-ask bounce)
- Residual autocorrelation (after detrending): **0.65-0.79** (slow mean reversion)

### Spread & Depth
- Median spread: **16 ticks** (extremely wide)
- Mean depth per side: ~31 lots across ~2-3 levels
- Top-of-book volume: 10-15 per level

### Trades
- ~420 market trades per day
- Average trade size: ~5 lots
- Prices range: fair +/- 20

### Book Imbalance
- Highly predictive: imbalance > 0.3 predicts +3.5 avg return, < -0.3 predicts -3.5
- Consistent across all 3 days

### Best Fair Value Model
| Model | MSE (avg) |
|-------|-----------|
| Static mean | ~26 |
| Rolling 50 | ~8.6 |
| **EWMA(0.05)** | **~7.3** |
| Linear trend | ~26 |

**EWMA is the clear winner** for ASH. Linear trend is useless because there is no trend.

### Strategy Implication
Market making around EWMA fair value with:
- Wide passive quotes (make_width=7) to capture the 16-tick spread
- Aggressive taking when price crosses fair by 0.5+ ticks
- Book imbalance adjustment to fair value (+/- 1.0 based on imbalance)
- Light inventory skew (0.02) to avoid getting stuck, but not so much it fights profitable positions

---

## 2. Market Structure: INTARIAN_PEPPER_ROOT

### Price Behavior
- **Deterministic linear drift: +0.1002 per step (+1001 per day)**
- Remarkably stable across all 3 days:
  - Day -2: slope=0.100169, intercept=9999.97
  - Day -1: slope=0.100160, intercept=11000.13
  - Day  0: slope=0.100221, intercept=12000.17
- After detrending, residual std: ~2.0-2.4
- **Detrended residual autocorrelation: ~0.01** (essentially white noise)

### Key Insight
After removing the linear trend, the residual is white noise. This means:
- Mean reversion on the residual does NOT work
- There is no predictive signal in the residual for timing entries
- The entire edge is in **riding the trend** and **capturing spread around the trend line**

### Spread & Depth
- Median spread: **12-14 ticks** (increasing slightly over days)
- Mean depth per side: ~25 lots

### Best Fair Value Model
| Model | MSE (avg) |
|-------|-----------|
| Static mean | ~83,000 |
| Rolling 50 | ~11.5 |
| EWMA(0.05) | ~8.1 |
| **Linear trend** | **~4.9** |
| **Online linear** | **~4.8** |

**Online linear regression is optimal.** The trend is so dominant that anything without trend modeling fails catastrophically.

### Strategy Implication
Online linear trend estimation with:
- Strong long bias (directional_skew=1.5) to ride the structural upward drift
- No inventory skew (skew_factor=0.0) — fighting the trend is expensive
- Wide-ish passive quotes (make_width=5) for spread capture
- Moderate take threshold (2.0) — only take clearly mispriced orders

---

## 3. What TA-Style Concepts Were Useful vs Useless

### Useful (translated to quant/microstructure equivalents)
| Concept | Translation | Product | Value |
|---------|-------------|---------|-------|
| Moving average | EWMA fair value | ASH | High |
| Trend direction | Online linear regression | PEPPER | Critical |
| Support/resistance | Book walls, depth imbalance | ASH | Moderate |
| Volume analysis | Book imbalance as direction predictor | Both | Moderate |
| Mean reversion | Residual mean reversion to EWMA | ASH | High |

### Useless or Harmful
| Concept | Why |
|---------|-----|
| RSI | No oscillatory structure found |
| MACD crossover | No multi-speed trend in ASH; single trend in PEPPER |
| Bollinger bands on raw price | PEPPER has trend; raw bands give false signals |
| Residual z-score for PEPPER | Residual is white noise; z-score has no predictive power |
| Pattern recognition | No significant patterns beyond linear drift and mean reversion |

---

## 4. Exploitable Signals

### ASH_COATED_OSMIUM
1. **EWMA deviation**: When mid deviates from EWMA fair, it mean-reverts. Take aggressively.
2. **Book imbalance**: Strong predictor (+3.5 avg return per direction). Adjust fair value accordingly.
3. **Spread capture**: 16-tick spread with ~31 lots depth. Wide passive quotes profit from patient filling.

### INTARIAN_PEPPER_ROOT
1. **Structural drift**: +0.1/step is consistent and dominant. Being long captures this.
2. **Spread capture**: 12-14 tick spread. Passive quotes around trend fair capture additional edge.

### Confidence Ratings
| Product | Signal | Confidence | Why |
|---------|--------|------------|-----|
| ASH | EWMA fair | 9/10 | Stable across 3 days, minimal sensitivity |
| ASH | Book imbalance | 7/10 | Consistent but small sample per day |
| ASH | Spread capture | 9/10 | Structural; spread is wide and stable |
| PEPPER | Linear drift | 10/10 | Perfect consistency across all 3 days |
| PEPPER | Long bias | 9/10 | Directly follows from drift; robust |
| PEPPER | Spread capture | 8/10 | Works but secondary to drift |

---

## 5. Why This Strategy May Generalize

1. **ASH**: EWMA adapts to any slow-moving fair value. No day-specific hardcoding. The wide-spread market making edge is structural.
2. **PEPPER**: Online OLS estimates slope and intercept from scratch each day. No hardcoded daily offsets. Works for any starting price and any consistent drift rate.
3. **Book imbalance** is a market microstructure signal, not data-mined.
4. **Parameters are stable**: sensitivity analysis shows most parameters have moderate impact except the structural ones (directional_skew, which is a direct consequence of the drift).

## 6. Potential Overfitting Risks

1. **PEPPER directional_skew=1.5**: If the drift disappears or reverses, this becomes a constant 1.5 bias that would lose money. Mitigation: the online OLS would detect a slope change within ~100 steps.
2. **ASH make_width=7**: Optimized for the specific spread distribution observed. If spreads narrow significantly, wider quotes get fewer fills.
3. **Fill model bias**: Our backtester may be optimistic about passive fills. Real competition fills could differ.
4. **3-day sample**: All analysis is based on 3 days. Parameters tuned to this sample may not generalize to very different market regimes.

## 7. Failure Modes

1. **PEPPER drift reversal**: If PEPPER suddenly drifts downward, the long bias creates large losses. Online OLS takes ~20 steps to adapt.
2. **ASH regime change**: If ASH transitions to a trending product, the EWMA lag could cause losses.
3. **Spread collapse**: If spreads compress to 2-3 ticks, our wide quotes would never fill and the taking edge shrinks.
4. **Competition adversarial**: Other participants' algorithms could change the market structure.

---

## 8. Parameter Sensitivity Summary

| Parameter | Product | Range of Impact | Classification |
|-----------|---------|----------------|----------------|
| directional_skew | PEPPER | 90,183 | **CRITICAL** |
| inventory_skew_factor | PEPPER | 79,700 | **CRITICAL** |
| make_width | PEPPER | 11,470 | High |
| make_width | ASH | 8,649 | High |
| take_threshold | PEPPER | 3,427 | Moderate |
| inventory_skew_factor | ASH | 3,293 | Moderate |
| ewma_alpha_base | PEPPER | 2,147 | Moderate |
| residual_zscore_threshold | PEPPER | 2,059 | Moderate |
| imbalance_edge | ASH | 974 | Low |
| take_threshold | ASH | 884 | Low |
| ewma_alpha | ASH | 836 | Low |
| max_take_size | ASH | 212 | Negligible |
| trend_rate | PEPPER | 7 | **Negligible** |

The near-zero sensitivity of trend_rate confirms that the online OLS overrides the prior quickly, making the strategy robust to the initial trend assumption.

---

## 9. Final Backtest Results

| Strategy | Total PnL | Per Day (avg) | Max DD | Score |
|----------|-----------|---------------|--------|-------|
| Baseline | 30,545 | 10,182 | 327 | 40,375 |
| Improved | 41,126 | 13,709 | 408 | 54,051 |
| **Optimized** | **205,116** | **68,372** | **1,150** | **271,484** |

### Optimized Per-Product Breakdown (average per day)
| Product | PnL/day | Max Position | Avg Position | Fills/day |
|---------|---------|-------------|-------------|-----------|
| ASH | ~15,976 | 50 | ~23 | ~623 |
| PEPPER | ~52,396 | 50 | ~46 | ~337 |

PEPPER provides 77% of total PnL, primarily from structural drift (50 * 1000 = 50,000 theoretical max from trend alone).
