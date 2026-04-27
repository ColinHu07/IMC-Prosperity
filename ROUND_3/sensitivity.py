"""
Parameter sensitivity sweep for the current R3trader.py.

Each row sweeps ONE knob while holding the others at their defaults.
We want a broad plateau of acceptable PnL — a sharp peak means the
knob is fit to the three historical tapes.

Run with optimistic fills because we care about the relative shape of
the curve, not the absolute level.
"""
import importlib
import os
import sys

_R3 = os.path.dirname(os.path.abspath(__file__))
if _R3 not in sys.path:
    sys.path.insert(0, _R3)

from backtest.trader_replay import run_trader_day
from utils.constants import DAYS


KNOBS = {
    "HP_TREND_T":           [15.0, 25.0, 35.0, 50.0, 75.0],
    "HP_QUOTE_EDGE":        [2, 3, 4, 5],
    "HP_MAX_POS":           [60, 80, 100, 120, 150],
    "HP_LAYER_GAP":         [0, 2, 4, 6],
    "HP_LAYER_GAP_2":       [0, 2, 3, 4, 6],
    "VX_TREND_T":           [10.0, 15.0, 20.0, 25.0, 40.0],
    "VX_QUOTE_EDGE":        [1, 2, 3],
    "VX_MAX_POS":           [60, 80, 100, 120],
    "VX_DELTA_HEDGE":       [0.0, 0.5, 1.0, 1.5, 2.0],
    "VEV_QUOTE_EDGE_DEEP":  [3, 5, 7, 9],
    "VEV_QUOTE_EDGE_NEAR":  [1, 2, 3],
    "VEV_TREND_T":          [5.0, 8.0, 10.0, 15.0],
    "VEV_STREAK_VOL":       [3, 5, 8, 12],
    "VEV_STREAK_POS_FRAC":  [0.2, 0.3, 0.4, 0.5, 0.6],
    "CLOSEOUT_TICKS":       [100, 200, 500, 1000],
}


def _build_trader(overrides):
    if "R3trader" in sys.modules:
        importlib.reload(sys.modules["R3trader"])
    else:
        importlib.import_module("R3trader")
    mod = sys.modules["R3trader"]
    for k, v in overrides.items():
        setattr(mod, k, v)
    return mod.Trader()


def run():
    print("\n=== Sensitivity sweep for R3trader ===")
    print("Each row sweeps ONE knob; others fixed at defaults.")
    print("Numbers are 3-day mean PnL (optimistic fills).\n")

    for knob, values in KNOBS.items():
        row = [f"{knob:<18}"]
        for v in values:
            pnls = []
            for d in DAYS:
                trader = _build_trader({knob: v})
                m = run_trader_day(trader, d, fill_mode="optimistic")
                pnls.append(m.get_summary()["TOTAL"]["final_pnl"])
            mean = sum(pnls) / len(pnls)
            row.append(f"{v}={mean:+,.0f}")
        print("  " + " | ".join(row))


if __name__ == "__main__":
    run()
