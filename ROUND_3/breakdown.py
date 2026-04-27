"""
Per-product PnL breakdown for a given trader module.
"""
import argparse
import importlib
import os
import sys

_R3 = os.path.dirname(os.path.abspath(__file__))
if _R3 not in sys.path:
    sys.path.insert(0, _R3)

from backtest.trader_replay import run_trader_day
from utils.constants import DAYS


def _load(name):
    if name in sys.modules:
        importlib.reload(sys.modules[name])
    else:
        __import__(name)
    return sys.modules[name].Trader


def run(mod_name):
    TraderCls = _load(mod_name)
    products = ["HYDROGEL_PACK", "VELVETFRUIT_EXTRACT",
                "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100",
                "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500",
                "VEV_6000", "VEV_6500"]
    print(f"\n=== {mod_name} per-product PnL by day ===")
    header = f"{'product':<22} " + " ".join(f"{'day'+str(d):>10}" for d in DAYS) + f" {'mean':>10} {'fills/day':>10} {'maxpos':>8}"
    print(header)
    print("-" * len(header))
    all_summ = []
    for d in DAYS:
        m = run_trader_day(TraderCls(), d, fill_mode="optimistic")
        all_summ.append(m.get_summary())
    for p in products:
        pnls = [s.get(p, {}).get("final_pnl", 0) for s in all_summ]
        fills = [s.get(p, {}).get("fill_count", 0) for s in all_summ]
        maxpos = max(s.get(p, {}).get("max_position", 0) for s in all_summ)
        print(f"{p:<22} " + " ".join(f"{x:>10,.0f}" for x in pnls)
              + f" {sum(pnls)/3:>10,.0f} {sum(fills)/3:>10.0f} {maxpos:>8}")
    totals = [s["TOTAL"]["final_pnl"] for s in all_summ]
    print("-" * len(header))
    print(f"{'TOTAL':<22} " + " ".join(f"{x:>10,.0f}" for x in totals)
          + f" {sum(totals)/3:>10,.0f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("module", nargs="?", default="R3trader")
    args = ap.parse_args()
    run(args.module)
