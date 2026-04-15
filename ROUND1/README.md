# IMC Prosperity Round 1 - Trading System

## Quick Start

### Run Analysis
```bash
cd ROUND1
python analysis/round1_analysis.py
```

### Run Backtests (Baseline → Improved → Optimized)
```bash
python run_backtest.py
```

### Run Optimization
```bash
# Phase 1: Random + local search (60 trials)
python backtest/optimize.py

# Phase 2: Coordinate descent refinement
python backtest/optimize_v2.py

# Phase 3: Fine-grained sensitivity analysis
python backtest/optimize_v3.py
```

### Export Final Submission
The file `trader.py` is the self-contained Prosperity-compatible submission.
Copy it directly to the competition upload interface.

## Architecture

```
ROUND1/
├── trader.py                    # Final Prosperity submission (self-contained)
├── run_backtest.py              # Main backtest runner (baseline/improved/optimized)
├── strategy/
│   ├── config.py                # All tunable parameters
│   ├── base.py                  # Abstract strategy interface
│   ├── ash_osmium.py            # ASH_COATED_OSMIUM strategy
│   ├── pepper_root.py           # INTARIAN_PEPPER_ROOT strategy
│   ├── fair_value.py            # Fair value models (EWMA, OnlineLinearTrend)
│   ├── signals.py               # Signal utilities (book imbalance, z-score)
│   └── execution.py             # Execution logic (taking, making, skew)
├── backtest/
│   ├── replay_engine.py         # Historical data replay engine
│   ├── fill_model.py            # Fill simulation (aggressive + passive)
│   ├── metrics.py               # PnL, drawdown, composite scoring
│   ├── optimize.py              # Phase 1 optimizer (random + local)
│   ├── optimize_v2.py           # Phase 2 optimizer (coordinate descent)
│   └── optimize_v3.py           # Phase 3 optimizer (sensitivity analysis)
├── analysis/
│   ├── round1_analysis.py       # Full quantitative analysis script
│   └── report.md                # Research memo with findings
├── utils/
│   ├── io.py                    # Data loading utilities
│   └── constants.py             # Global constants
├── output/
│   ├── metrics/                 # Saved backtest results
│   ├── best_params/             # Best parameter configurations
│   └── logs/                    # Optimization logs
└── README.md                    # This file
```

## Strategy Summary

### ASH_COATED_OSMIUM (Market Making)
- **Fair Value**: EWMA(alpha=0.06) of mid price
- **Signal**: Book imbalance adjusts fair value (+/- 1.0 based on top-of-book ratio)
- **Taking**: Aggressive fills when market crosses fair by 0.5+ ticks
- **Making**: Wide passive quotes (make_width=7) to capture the 16-tick spread
- **Inventory**: Light skew (0.02) to avoid getting stuck
- **PnL driver**: Spread capture + mean reversion taking

### INTARIAN_PEPPER_ROOT (Trend Following)
- **Fair Value**: Online linear regression (slope ~0.1/step, intercept estimated live)
- **Signal**: Structural upward drift of ~1000/day
- **Taking**: Moderate threshold (2.0) for clear mispricings only
- **Making**: Directional bias (skew=1.5) to accumulate long position early
- **Inventory**: No skew (0.0) — fighting the trend is expensive
- **PnL driver**: 77% from structural drift, 23% from spread capture

## Results

| Strategy | Total PnL | Per Day Avg | Consistency |
|----------|-----------|-------------|-------------|
| Baseline | 30,545 | 10,182 | +/- 5% |
| Improved | 41,126 | 13,709 | +/- 6% |
| **Optimized** | **205,116** | **68,372** | **+/- 3%** |

## Dependencies

Only Python standard library. No external packages required.

## Key Research Finding

INTARIAN_PEPPER_ROOT has a remarkably consistent linear drift of +0.1 per 100ms step
(+1000 per day). After detrending, residuals are white noise. The optimal strategy is
simply to go maximum long as fast as possible and ride the drift.
