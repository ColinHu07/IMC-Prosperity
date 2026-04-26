"""
Head-to-head: current R3trader.py vs the frozen R3trader_submitted.py.

Runs both over all 3 historical days in both fill models. Useful sanity
check before submitting a change: the new build should beat (or at least
match) the frozen submitted version on every day, at least in pessimistic.
"""
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
        importlib.import_module(name)
    return sys.modules[name].Trader


def run():
    versions = (
        ("current",   "R3trader"),
        ("submitted", "R3trader_submitted"),
    )
    for fill_mode in ("optimistic", "pessimistic"):
        print(f"\n=== Fill model: {fill_mode} ===")
        print(f"{'version':<12} {'day0':>12} {'day1':>12} {'day2':>12} {'mean':>12} {'min':>12}")
        print("-" * 68)
        for label, mod in versions:
            TraderCls = _load(mod)
            pnls = []
            for d in DAYS:
                m = run_trader_day(TraderCls(), d, fill_mode=fill_mode)
                pnls.append(m.get_summary()["TOTAL"]["final_pnl"])
            print(f"{label:<12} " + " ".join(f"{x:>12,.0f}" for x in pnls)
                  + f" {sum(pnls)/len(pnls):>12,.0f} {min(pnls):>12,.0f}")


if __name__ == "__main__":
    run()
