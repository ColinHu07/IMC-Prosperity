"""
Replay engine that runs trader.py directly with either an optimistic or
pessimistic passive fill model.

Keeps the same position-limit enforcement and metrics collection as the
original replay_engine.py, but only needs to know about one TraderRunner
strategy for all products.
"""
import os
import sys

_ROUND1 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROUND1 not in sys.path:
    sys.path.insert(0, _ROUND1)

from utils.io import load_prices, load_trades, build_order_books, build_trade_index
from utils.constants import PRODUCTS, DAYS, POSITION_LIMITS
from backtest.fill_model import (
    simulate_aggressive_fills,
    simulate_passive_fills,
    simulate_passive_fills_pessimistic,
)
from backtest.metrics import BacktestMetrics
from backtest.trader_runner import TraderRunner


def run_trader_day(trader, day, fill_mode="optimistic", products=None, verbose=False):
    """
    Run a single day backtest using the given Trader instance.

    fill_mode: "optimistic" (original) or "pessimistic" (strict cross + ratio cap)
    """
    products = list(products or PRODUCTS)
    prices = load_prices(day)
    trade_rows = load_trades(day)
    books = build_order_books(prices)
    trade_idx = build_trade_index(trade_rows)

    passive_fn = (simulate_passive_fills
                  if fill_mode == "optimistic"
                  else simulate_passive_fills_pessimistic)

    runner = TraderRunner(trader, products, books)
    runner.reset()

    metrics = BacktestMetrics(products)
    timestamps = sorted(books.keys())
    pending_passive = {p: [] for p in products}

    for i, ts in enumerate(timestamps):
        for product in products:
            if product not in books[ts]:
                metrics.record_tick(product, metrics.last_mid.get(product))
                continue

            snap = books[ts][product]
            bids = snap["bids"]
            asks = snap["asks"]
            mid = snap["mid_price"]
            if mid is not None and mid < 100:
                mid = metrics.last_mid.get(product)

            ts_trades = [t for t in trade_idx.get(ts, []) if t["symbol"] == product]
            limit = POSITION_LIMITS.get(product, 50)

            # Resolve pending passive orders against this tick's book/trades
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

            position = metrics.position[product]
            orders = runner.on_tick(ts, product, bids, asks, mid, position, ts_trades)

            agg_fills, passive_orders = simulate_aggressive_fills(orders, bids, asks)
            for fp, fs in agg_fills:
                pos = metrics.position[product]
                if fs > 0 and pos + fs > limit:
                    fs = max(0, limit - pos)
                elif fs < 0 and pos + fs < -limit:
                    fs = min(0, -(limit + pos))
                if fs != 0:
                    metrics.record_fill(product, fp, fs, "aggressive")

            pending_passive[product] = passive_orders
            metrics.record_tick(product, mid)

    if verbose:
        print(f"  Day {day:+d} ({fill_mode}): {metrics.get_summary()}")
    return metrics


def run_trader_all_days(trader_factory, fill_mode="optimistic", products=None,
                        days=None, verbose=False):
    """
    trader_factory: zero-arg callable returning a fresh Trader() — so each day
                    starts with clean state (mirrors Prosperity's daily reset).
    """
    days = days or DAYS
    summaries = []
    for d in days:
        trader = trader_factory()
        m = run_trader_day(trader, d, fill_mode=fill_mode, products=products, verbose=verbose)
        summaries.append(m.get_summary())
    return summaries
