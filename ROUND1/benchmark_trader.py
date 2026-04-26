"""
Benchmark the current trader.py across all days under both optimistic and
pessimistic passive-fill models.

Reports per-day PnL, worst-day PnL, and a compact summary table. The worst-day
number under the pessimistic model is the primary metric for shipping decisions.

Usage:  python ROUND1/benchmark_trader.py [--label NAME]
"""
import os
import sys
import argparse
import json
from datetime import datetime

_ROUND1 = os.path.dirname(os.path.abspath(__file__))
if _ROUND1 not in sys.path:
    sys.path.insert(0, _ROUND1)

from backtest.trader_replay import run_trader_all_days
from backtest.metrics import compute_composite_score
from utils.constants import DAYS, OUTPUT_DIR
from trader import Trader


def _fmt(x):
    return f"{x:>11,.2f}"


def run(label="current"):
    results = {}
    for mode in ("optimistic", "pessimistic"):
        summaries = run_trader_all_days(Trader, fill_mode=mode)
        score = compute_composite_score(summaries)
        score["day_details"] = summaries
        results[mode] = score

    print()
    print("=" * 74)
    print(f"  BENCHMARK: {label}   ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    print("=" * 74)
    header = f"  {'metric':<22}{'optimistic':>14}{'pessimistic':>14}{'delta':>14}"
    print(header)
    print("  " + "-" * 62)

    def _row(key, is_pnl=True):
        a = results["optimistic"][key]
        b = results["pessimistic"][key]
        delta = b - a
        print(f"  {key:<22}{_fmt(a)}{_fmt(b)}{_fmt(delta)}")

    _row("total_pnl")
    _row("min_day_pnl")
    _row("max_drawdown")
    _row("avg_inventory")
    print()

    for d_idx, d in enumerate(DAYS):
        for mode in ("optimistic", "pessimistic"):
            s = results[mode]["day_details"][d_idx]
            tot = s["TOTAL"]["final_pnl"]
            ash = s["ASH_COATED_OSMIUM"]["final_pnl"]
            pep = s["INTARIAN_PEPPER_ROOT"]["final_pnl"]
            print(f"  Day {d:+d} [{mode:>11s}]  TOTAL={_fmt(tot)}"
                  f"  ASH={_fmt(ash)}  PEPPER={_fmt(pep)}")
        print()

    # Persist
    outpath = os.path.join(OUTPUT_DIR, "metrics", f"benchmark_{label}.json")
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    with open(outpath, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  [saved] {outpath}")
    print()

    worst_opt = results["optimistic"]["min_day_pnl"]
    worst_pes = results["pessimistic"]["min_day_pnl"]
    print(f"  Worst-day PnL  (optimistic): {_fmt(worst_opt)}")
    print(f"  Worst-day PnL (pessimistic): {_fmt(worst_pes)}   ← primary metric")
    print("=" * 74)

    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="current")
    args = ap.parse_args()
    run(args.label)
