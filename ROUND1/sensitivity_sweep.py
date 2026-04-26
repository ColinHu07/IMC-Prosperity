"""
Sensitivity sweep.  For each tunable knob in trader.py, nudge it to a
reasonable low/high pair and measure the change in pessimistic worst-day PnL.

A parameter that moves worst-day PnL by more than 10% is a red flag — it is
either overfit or structurally unstable.  Small impact = robust.

Restores the original value after each test.
"""
import os
import sys

_ROUND1 = os.path.dirname(os.path.abspath(__file__))
if _ROUND1 not in sys.path:
    sys.path.insert(0, _ROUND1)

from backtest.trader_replay import run_trader_all_days
from backtest.metrics import compute_composite_score
import trader as T
from trader import Trader


def _score(mode="pessimistic"):
    summaries = run_trader_all_days(Trader, fill_mode=mode)
    return compute_composite_score(summaries)["min_day_pnl"]


def _test_module_attr(attr, value):
    original = getattr(T, attr)
    setattr(T, attr, value)
    try:
        return _score("pessimistic")
    finally:
        setattr(T, attr, original)


def main():
    baseline = _score("pessimistic")
    print(f"\nBaseline pessimistic worst-day PnL: {baseline:,.2f}\n")

    sweeps = [
        ("ASH_ANCHOR_PRIOR_K", "ASH Bayesian prior K", 100, 2000),
        ("ASH_DEV_SKEW", "ASH deviation skew", 0.2, 0.4),
        ("ASH_INV_SKEW", "ASH inventory skew", 0.01, 0.05),
        ("ASH_BREAKER_DEVIATION", "ASH breaker dev", 20, 100),
        ("ASH_MAX_TAKE_PER_TICK", "ASH take/tick cap", 40, 80),
        ("ASH_L1_FRACTION", "ASH L1 fraction", 0.4, 0.8),
    ]

    header = f"  {'parameter':<26}{'low':>10}{'low PnL':>14}{'high':>10}{'high PnL':>14}{'spread%':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for attr, label, lo, hi in sweeps:
        lo_pnl = _test_module_attr(attr, lo)
        hi_pnl = _test_module_attr(attr, hi)
        spread = (max(lo_pnl, hi_pnl) - min(lo_pnl, hi_pnl)) / baseline * 100
        flag = "  ** OVERFIT" if spread > 10 else ""
        print(f"  {label:<26}{lo:>10}{lo_pnl:>14,.0f}{hi:>10}{hi_pnl:>14,.0f}{spread:>9.1f}%{flag}")

    print()


if __name__ == "__main__":
    main()
