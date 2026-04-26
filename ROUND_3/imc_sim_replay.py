"""
Replay a trader against the first 100k ticks of a given day (day 2 by default).

This mirrors the IMC submission test window that produced the -$13,218 result
for the frozen submitted trader (submission 436083, day 2, ts 0..99900).

Usage:
    python imc_sim_replay.py                          # defaults: R3trader, day 2
    python imc_sim_replay.py --module R3trader_submitted
    python imc_sim_replay.py --module R3trader --day 0 --ts-max 99900
"""
import argparse
import importlib
import os
import sys
from collections import defaultdict

_R3 = os.path.dirname(os.path.abspath(__file__))
if _R3 not in sys.path:
    sys.path.insert(0, _R3)

from utils.io import load_prices, load_trades, build_order_books, build_trade_index
from utils.constants import PRODUCTS, POSITION_LIMITS
from backtest.fill_model import (
    simulate_aggressive_fills,
    simulate_passive_fills_pessimistic,
)
from datamodel import OrderDepth, TradingState


def _load_trader(module_name):
    if module_name in sys.modules:
        importlib.reload(sys.modules[module_name])
    else:
        importlib.import_module(module_name)
    return sys.modules[module_name].Trader()


def _last_mid_before(books, ts_cutoff, product):
    for ts in reversed(sorted(t for t in books.keys() if t <= ts_cutoff)):
        snap = books[ts].get(product)
        if snap and snap["mid_price"] is not None:
            return snap["mid_price"]
    return None


def replay(module_name, day, ts_max, verbose=False):
    trader = _load_trader(module_name)

    prices = load_prices(day)
    books = build_order_books(prices)
    trade_rows = load_trades(day)
    trade_idx = build_trade_index(trade_rows)

    timestamps = sorted(t for t in books.keys() if t <= ts_max)

    pos = {p: 0 for p in PRODUCTS}
    cash = {p: 0.0 for p in PRODUCTS}
    pending = {p: [] for p in PRODUCTS}
    trade_count = defaultdict(int)
    vol_traded = defaultdict(int)
    td = ""

    for ts in timestamps:
        ods = {}
        for p in PRODUCTS:
            snap = books[ts].get(p)
            if snap is None:
                continue
            od = OrderDepth()
            od.buy_orders = dict(snap["bids"])
            od.sell_orders = {px: -abs(v) for px, v in snap["asks"].items()}
            ods[p] = od

        state = TradingState(
            traderData=td,
            timestamp=ts,
            order_depths=ods,
            position=dict(pos),
        )
        result = trader.run(state)
        if isinstance(result, tuple) and len(result) == 3:
            orders_dict, _conv, td = result
        elif isinstance(result, tuple) and len(result) == 2:
            orders_dict, td = result
        else:
            orders_dict = result

        for p in PRODUCTS:
            snap = books[ts].get(p)
            if snap is None:
                continue
            bids, asks = snap["bids"], snap["asks"]
            ts_trades = [t for t in trade_idx.get(ts, []) if t["symbol"] == p]
            limit = POSITION_LIMITS.get(p, 50)

            if pending[p] and (bids or asks):
                fills = simulate_passive_fills_pessimistic(
                    pending[p], bids, asks, ts_trades)
                for fp, fs in fills:
                    if fs > 0 and pos[p] + fs > limit:
                        fs = max(0, limit - pos[p])
                    elif fs < 0 and pos[p] + fs < -limit:
                        fs = min(0, -(limit + pos[p]))
                    if fs != 0:
                        pos[p] += fs
                        cash[p] -= fs * fp
                        trade_count[p] += 1
                        vol_traded[p] += abs(fs)
                pending[p] = []

            my_orders = orders_dict.get(p, []) if orders_dict else []
            ors = [(o.price, o.quantity) for o in my_orders]
            if bids or asks:
                agg, passive_rem = simulate_aggressive_fills(ors, bids, asks)
                for fp, fs in agg:
                    if fs > 0 and pos[p] + fs > limit:
                        fs = max(0, limit - pos[p])
                    elif fs < 0 and pos[p] + fs < -limit:
                        fs = min(0, -(limit + pos[p]))
                    if fs != 0:
                        pos[p] += fs
                        cash[p] -= fs * fp
                        trade_count[p] += 1
                        vol_traded[p] += abs(fs)
                pending[p] = passive_rem

    # Mark to last mid, aggregate PnL per product
    results = {}
    total = 0.0
    for p in PRODUCTS:
        last_mid = _last_mid_before(books, ts_max, p)
        if last_mid is None:
            last_mid = 0.0
        mark = pos[p] * last_mid
        pnl = cash[p] + mark
        total += pnl
        results[p] = {
            "final_pnl": pnl,
            "cash": cash[p],
            "mark": mark,
            "pos": pos[p],
            "last_mid": last_mid,
            "trades": trade_count[p],
            "volume": vol_traded[p],
        }
    return results, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", type=str, default="R3trader",
                    help="trader module name (default R3trader)")
    ap.add_argument("--day", type=int, default=2)
    ap.add_argument("--ts-max", type=int, default=99900,
                    help="inclusive upper ts bound")
    args = ap.parse_args()

    res, total = replay(args.module, args.day, args.ts_max)
    print(f"\n=== {args.module} on day {args.day} ts 0..{args.ts_max} "
          f"(pessimistic fills) ===")
    print(f"{'product':<22} {'pnl':>10} {'pos':>5} {'trades':>7} "
          f"{'volume':>8}")
    for p in sorted(res):
        r = res[p]
        if r["trades"] == 0 and r["pos"] == 0:
            continue
        print(f"{p:<22} {r['final_pnl']:>+10,.0f} {r['pos']:>+5d} "
              f"{r['trades']:>7d} {r['volume']:>8d}")
    print(f"{'TOTAL':<22} {total:>+10,.0f}")


if __name__ == "__main__":
    main()
