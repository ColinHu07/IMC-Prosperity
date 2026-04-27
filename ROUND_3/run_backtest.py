"""
Round 3 backtest driver (Test A: walk-forward OOS on all 3 historical days).

Usage:
    python run_backtest.py                  # optimistic fills, all 3 days
    python run_backtest.py --pessimistic    # conservative fill model
    python run_backtest.py --day 0          # single day

Reports per-day PnL per product and the TOTAL. No tuning happens here; this is
purely a report card for whatever is in R3trader.py right now.
"""
import argparse
import json
import os
import sys

_R3 = os.path.dirname(os.path.abspath(__file__))
if _R3 not in sys.path:
    sys.path.insert(0, _R3)

from backtest.trader_replay import run_trader_day
from utils.constants import DAYS, OUTPUT_DIR


TRADER_MODULE = os.environ.get("R3_TRADER", "R3trader")


def _fresh_trader():
    # Reload so parameter tweaks are picked up without restarting the
    # interpreter. Swap the trader module via R3_TRADER env var
    # (e.g. R3_TRADER=R3trader_submitted to replay the frozen submitted
    # version). Default is the current working trader R3trader.py.
    import importlib
    if TRADER_MODULE in sys.modules:
        importlib.reload(sys.modules[TRADER_MODULE])
    else:
        importlib.import_module(TRADER_MODULE)
    return sys.modules[TRADER_MODULE].Trader()


def _fmt_pnl(x):
    return f"{x:>+11,.0f}"


def run(days, fill_mode, save_name=None):
    per_day = {}
    totals = []
    print(f"\n=== Round 3 {TRADER_MODULE} backtest ({fill_mode} fills) ===")
    header_prods = ["HYDROGEL_PACK", "VELVETFRUIT_EXTRACT",
                    "VEV_4000", "VEV_4500",
                    "VEV_5000", "VEV_5100", "VEV_5200",
                    "VEV_5300", "VEV_5400", "VEV_5500",
                    "VEV_6000", "VEV_6500"]
    col_w = 12
    print(" day  " + " ".join(f"{p[:11]:>{col_w}}" for p in header_prods) + f" {'TOTAL':>{col_w}}")
    for d in days:
        trader = _fresh_trader()
        m = run_trader_day(trader, d, fill_mode=fill_mode)
        summ = m.get_summary()
        per_day[d] = summ
        row = [f"  {d}  "]
        for p in header_prods:
            row.append(f"{summ.get(p, {}).get('final_pnl', 0):>{col_w},.0f}")
        row.append(f"{summ['TOTAL']['final_pnl']:>{col_w},.0f}")
        totals.append(summ["TOTAL"]["final_pnl"])
        print(" ".join(row))
    print()
    print(f"  mean TOTAL : {sum(totals)/len(totals):,.0f}")
    print(f"  min  TOTAL : {min(totals):,.0f}   <-- worst-day is the acceptance metric")
    print(f"  max  TOTAL : {max(totals):,.0f}")
    print()

    # Extra diagnostics: final position per product on each day (should be 0 after closeout).
    print("  Closeout sanity (final position per product per day):")
    nonzero = False
    for d in days:
        bad = []
        for p, stats in per_day[d].items():
            if p == "TOTAL":
                continue
            fp = stats.get("final_position", 0)
            if fp != 0:
                bad.append((p, fp))
        if bad:
            nonzero = True
            msg = ", ".join(f"{p}={fp:+d}" for p, fp in bad)
            print(f"    day {d}: NONZERO -> {msg}")
        else:
            print(f"    day {d}: all flat.")
    if nonzero:
        print("  NOTE: nonzero final positions get marked at last mid, which is an")
        print("        optimistic proxy for the sim's hidden-fair-value closeout.")

    if save_name:
        path = os.path.join(OUTPUT_DIR, save_name)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"per_day": per_day, "totals": totals,
                       "mean": sum(totals)/len(totals),
                       "min": min(totals), "max": max(totals)}, f, indent=2)
        print(f"\n  saved: {path}")

    return per_day, totals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int, default=None)
    ap.add_argument("--pessimistic", action="store_true")
    ap.add_argument("--save", type=str, default=None,
                    help="filename (under round3_results/) to dump JSON summary")
    args = ap.parse_args()
    days = [args.day] if args.day is not None else DAYS
    fill_mode = "pessimistic" if args.pessimistic else "optimistic"
    run(days, fill_mode, save_name=args.save)


if __name__ == "__main__":
    main()
