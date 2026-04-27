"""
Replay engine for Round 3. Takes an instance exposing Trader.run(state) and
walks it through the historical CSVs, returning a BacktestMetrics per day.

FIXED: v1 of this harness iterated products one at a time and called
`runner.on_tick(...)` per-product, which meant the trader.run call (cached on
the first product of each tick) saw STALE positions for every product except
the first. Aggressive fills on tick N-1 for product X were not reflected in
the position the trader saw on tick N. With multi-product traders this caused
the trader to under-skew inventory and over-quote, producing huge spurious
fill counts (e.g. V4 showed 1073 VEX fills vs a real 69).

This version:
  1. Processes passive fills from the previous tick for ALL products first.
  2. Calls trader.run ONCE with a fully-current positions dict.
  3. Processes aggressive fills for ALL products and records new passive
     residuals for next tick.
"""
import os
import sys

_R3 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _R3 not in sys.path:
    sys.path.insert(0, _R3)

from datamodel import OrderDepth, TradingState
from utils.io import load_prices, load_trades, build_order_books, build_trade_index
from utils.constants import PRODUCTS, DAYS, POSITION_LIMITS
from backtest.fill_model import (
    simulate_aggressive_fills,
    simulate_passive_fills,
    simulate_passive_fills_pessimistic,
)
from backtest.metrics import BacktestMetrics


def _run_trader(trader, td, ts, books_at_ts, positions):
    """Build a TradingState for timestamp `ts` and call trader.run once."""
    order_depths = {}
    for p, snap in books_at_ts.items():
        od = OrderDepth()
        od.buy_orders = dict(snap["bids"]) if snap["bids"] else {}
        od.sell_orders = {px: -abs(v) for px, v in snap["asks"].items()} \
            if snap["asks"] else {}
        order_depths[p] = od
    state = TradingState(
        traderData=td,
        timestamp=ts,
        order_depths=order_depths,
        position=dict(positions),
    )
    result = trader.run(state)
    if isinstance(result, tuple) and len(result) == 3:
        orders_dict, _conversions, new_td = result
    elif isinstance(result, tuple) and len(result) == 2:
        orders_dict, new_td = result
    else:
        orders_dict, new_td = result, td
    out = {}
    for p, orders in (orders_dict or {}).items():
        out[p] = [(o.price, o.quantity) for o in orders]
    return out, new_td or ""


def run_trader_day(trader, day, fill_mode="optimistic", products=None, verbose=False):
    products = list(products or PRODUCTS)
    prices = load_prices(day)
    trade_rows = load_trades(day)
    books = build_order_books(prices)
    trade_idx = build_trade_index(trade_rows)

    passive_fn = (simulate_passive_fills
                  if fill_mode == "optimistic"
                  else simulate_passive_fills_pessimistic)

    metrics = BacktestMetrics(products)
    timestamps = sorted(books.keys())
    pending_passive = {p: [] for p in products}
    td = ""

    for ts in timestamps:
        books_at_ts = books.get(ts, {})

        # ----- 1. Process passive fills from previous tick for ALL products
        for product in products:
            if product not in books_at_ts:
                continue
            snap = books_at_ts[product]
            bids, asks = snap["bids"], snap["asks"]
            ts_trades = [t for t in trade_idx.get(ts, []) if t["symbol"] == product]
            limit = POSITION_LIMITS.get(product, 50)

            if pending_passive[product] and (bids or asks):
                passive_fills = passive_fn(pending_passive[product], bids, asks, ts_trades)
                for fp, fs in passive_fills:
                    pos = metrics.position[product]
                    if fs > 0 and pos + fs > limit:
                        fs = max(0, limit - pos)
                    elif fs < 0 and pos + fs < -limit:
                        fs = min(0, -(limit + pos))
                    if fs != 0:
                        metrics.record_fill(product, fp, fs, "passive")
                pending_passive[product] = []

        # ----- 2. Call trader.run ONCE with current positions for all products
        orders_dict, td = _run_trader(
            trader, td, ts,
            {p: s for p, s in books_at_ts.items() if p in products},
            metrics.position,
        )

        # ----- 3. Process aggressive fills for all products + stash residuals
        for product in products:
            if product not in books_at_ts:
                metrics.record_tick(product, metrics.last_mid.get(product))
                continue
            snap = books_at_ts[product]
            bids, asks = snap["bids"], snap["asks"]
            mid = snap["mid_price"]
            if mid is not None and mid < 0:
                mid = metrics.last_mid.get(product)
            limit = POSITION_LIMITS.get(product, 50)

            orders = orders_dict.get(product, [])
            if bids or asks:
                agg_fills, passive_residuals = simulate_aggressive_fills(
                    orders, bids, asks)
                for fp, fs in agg_fills:
                    pos = metrics.position[product]
                    if fs > 0 and pos + fs > limit:
                        fs = max(0, limit - pos)
                    elif fs < 0 and pos + fs < -limit:
                        fs = min(0, -(limit + pos))
                    if fs != 0:
                        metrics.record_fill(product, fp, fs, "aggressive")
                pending_passive[product] = passive_residuals
            else:
                pending_passive[product] = []

            metrics.record_tick(product, mid)

    if verbose:
        print(f"  Day {day} ({fill_mode}): {metrics.get_summary()['TOTAL']}")
    return metrics


def run_trader_all_days(trader_factory, fill_mode="optimistic", products=None,
                        days=None, verbose=False):
    days = days or DAYS
    summaries = []
    for d in days:
        trader = trader_factory()
        m = run_trader_day(trader, d, fill_mode=fill_mode, products=products, verbose=verbose)
        summaries.append(m.get_summary())
    return summaries
