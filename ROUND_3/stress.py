"""
Synthetic stress tests for the current R3trader.py.

Mutate the historical tape to simulate regimes we haven't seen, then
re-run the trader to check it doesn't blow up on a regime shift.
Acceptance criterion: no scenario's mean PnL should fall more than ~2x
the baseline below zero.

Scenarios:
  1. Anchor shift +  : HYDROGEL all prices +200 (center at 10190, not 9990)
  2. Anchor shift -  : HYDROGEL all prices -200 (center at 9790)
  3. Vol shock       : multiply HYDROGEL deviations from 10000 by 3x
  4. Drift           : add +0.002/tick drift to HYDROGEL
"""
import copy
import csv
import importlib
import os
import sys
import tempfile

_R3 = os.path.dirname(os.path.abspath(__file__))
if _R3 not in sys.path:
    sys.path.insert(0, _R3)

from backtest.trader_replay import run_trader_day
from utils.constants import DAYS


def _mutate_prices(day, mutation_fn):
    """Load prices_round_3_day_{day}.csv, apply mutation_fn(row), write to temp dir."""
    src = os.path.join(_R3, f"prices_round_3_day_{day}.csv")
    tmpdir = tempfile.mkdtemp(prefix="r3_stress_")
    dst = os.path.join(tmpdir, f"prices_round_3_day_{day}.csv")
    # Copy trades unchanged
    src_trades = os.path.join(_R3, f"trades_round_3_day_{day}.csv")
    dst_trades = os.path.join(tmpdir, f"trades_round_3_day_{day}.csv")
    if os.path.exists(src_trades):
        with open(src_trades) as rf, open(dst_trades, "w") as wf:
            wf.write(rf.read())

    with open(src) as f:
        reader = csv.DictReader(f, delimiter=";")
        fieldnames = reader.fieldnames
        rows = list(reader)

    new_rows = [mutation_fn(copy.deepcopy(r)) for r in rows]

    with open(dst, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(new_rows)
    return tmpdir


def _run_with_data_dir(data_dir, day, module="R3trader"):
    # Monkey-patch DATA_DIR in utils.constants for this run, reload io.
    import utils.constants as uc
    import utils.io as uio
    original = uc.DATA_DIR
    uc.DATA_DIR = data_dir
    uio.DATA_DIR = data_dir
    try:
        if module in sys.modules:
            importlib.reload(sys.modules[module])
        else:
            importlib.import_module(module)
        trader = sys.modules[module].Trader()
        m = run_trader_day(trader, day, fill_mode="optimistic")
        return m.get_summary()["TOTAL"]["final_pnl"]
    finally:
        uc.DATA_DIR = original
        uio.DATA_DIR = original


def _only_hp(fn):
    """Wrap a mutation fn so it only affects HYDROGEL_PACK rows."""
    def inner(row):
        if row["product"] != "HYDROGEL_PACK":
            return row
        return fn(row)
    return inner


def _shift(delta):
    def fn(row):
        for key in ("bid_price_1","bid_price_2","bid_price_3",
                    "ask_price_1","ask_price_2","ask_price_3","mid_price"):
            if row.get(key):
                row[key] = str(float(row[key]) + delta)
        return row
    return fn


def _drift(slope):
    def fn(row):
        ts = int(row["timestamp"])
        offset = slope * ts
        for key in ("bid_price_1","bid_price_2","bid_price_3",
                    "ask_price_1","ask_price_2","ask_price_3","mid_price"):
            if row.get(key):
                row[key] = str(float(row[key]) + offset)
        return row
    return fn


def _vol_shock(mult):
    # Amplify deviation from a running mean.
    # Simple impl: multiply (price - 10000) by mult.
    def fn(row):
        for key in ("bid_price_1","bid_price_2","bid_price_3",
                    "ask_price_1","ask_price_2","ask_price_3","mid_price"):
            if row.get(key):
                px = float(row[key])
                row[key] = str(10000.0 + (px - 10000.0) * mult)
        return row
    return fn


def run(module="R3trader"):
    print(f"\n=== Stress tests for {module} ===")
    print("Baseline (untouched days) for reference:")

    baseline = []
    for d in DAYS:
        if module in sys.modules:
            importlib.reload(sys.modules[module])
        else:
            importlib.import_module(module)
        trader = sys.modules[module].Trader()
        m = run_trader_day(trader, d, fill_mode="optimistic")
        baseline.append(m.get_summary()["TOTAL"]["final_pnl"])
    print(f"  baseline PnL per day: {baseline}  mean={sum(baseline)/3:,.0f}")
    print()

    scenarios = [
        ("anchor +200",   _only_hp(_shift(+200))),
        ("anchor -200",   _only_hp(_shift(-200))),
        ("vol shock 3x",  _only_hp(_vol_shock(3.0))),
        ("drift +0.002/t", _only_hp(_drift(0.002))),
    ]

    for name, mut in scenarios:
        pnls = []
        for d in DAYS:
            tmpdir = _mutate_prices(d, mut)
            pnls.append(_run_with_data_dir(tmpdir, d, module=module))
        mean = sum(pnls) / len(pnls)
        mn = min(pnls)
        base_mean = sum(baseline) / 3
        delta = mean - base_mean
        print(f"  {name:<18}  mean={mean:>+12,.0f}  min={mn:>+12,.0f}  (delta vs baseline: {delta:+,.0f})")

    # Restore constants (ensure other runs aren't contaminated)
    import utils.constants as uc
    uc.DATA_DIR = os.path.dirname(os.path.abspath(__file__))


if __name__ == "__main__":
    run()
