"""
Replay engine: feeds historical data to strategy and simulates fills.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.io import load_prices, load_trades, build_order_books, build_trade_index
from utils.constants import PRODUCTS, DAYS
from backtest.fill_model import simulate_aggressive_fills, simulate_passive_fills
from backtest.metrics import BacktestMetrics


class ReplayEngine:

    def __init__(self, strategies, products=None):
        """
        strategies: dict {product_name: strategy_instance}
        """
        self.strategies = strategies
        self.products = products or PRODUCTS

    def run_day(self, day, verbose=False):
        """
        Replay one day of data.
        Returns BacktestMetrics for that day.
        """
        prices = load_prices(day)
        trade_rows = load_trades(day)
        books = build_order_books(prices)
        trade_idx = build_trade_index(trade_rows)

        metrics = BacktestMetrics(self.products)
        timestamps = sorted(books.keys())

        # Reset strategies for fresh day
        for strat in self.strategies.values():
            strat.reset()

        pending_passive = {p: [] for p in self.products}

        for i, ts in enumerate(timestamps):
            next_ts = timestamps[i + 1] if i + 1 < len(timestamps) else None

            for product in self.products:
                if product not in books[ts]:
                    metrics.record_tick(product, metrics.last_mid.get(product))
                    continue

                snap = books[ts][product]
                bids = snap["bids"]
                asks = snap["asks"]
                mid = snap["mid_price"]

                # Filter out degenerate mids (only one side)
                if mid is not None and mid < 100:
                    mid = metrics.last_mid.get(product)

                # Get trades at this timestamp
                ts_trades = [t for t in trade_idx.get(ts, []) if t["symbol"] == product]

                # First: resolve pending passive orders from previous tick
                if pending_passive[product] and bids and asks:
                    passive_fills = simulate_passive_fills(
                        pending_passive[product], bids, asks, ts_trades
                    )
                    for fp, fs in passive_fills:
                        pos = metrics.position[product]
                        limit = 50  # position limit
                        if fs > 0 and pos + fs > limit:
                            fs = max(0, limit - pos)
                        elif fs < 0 and pos + fs < -limit:
                            fs = max(0, -(limit + pos))
                            fs = -fs if fs != 0 else 0
                        if fs != 0:
                            metrics.record_fill(product, fp, fs, "passive")
                    pending_passive[product] = []

                position = metrics.position[product]
                strat = self.strategies.get(product)
                if strat is None:
                    metrics.record_tick(product, mid)
                    continue

                # Get strategy orders
                orders = strat.on_tick(ts, product, bids, asks, mid, position, ts_trades)

                # Simulate aggressive fills
                agg_fills, passive_orders = simulate_aggressive_fills(orders, bids, asks)

                for fp, fs in agg_fills:
                    pos = metrics.position[product]
                    limit = 50
                    if fs > 0 and pos + fs > limit:
                        fs = max(0, limit - pos)
                    elif fs < 0 and pos + fs < -limit:
                        fs = min(0, -(limit + pos))
                    if fs != 0:
                        metrics.record_fill(product, fp, fs, "aggressive")

                # Store passive orders for next tick resolution
                pending_passive[product] = passive_orders

                metrics.record_tick(product, mid)

        if verbose:
            summary = metrics.get_summary()
            print(f"  Day {day:+d}: {summary}")

        return metrics

    def run_all_days(self, days=None, verbose=False):
        """Run backtest across all specified days. Returns list of day summaries."""
        if days is None:
            days = DAYS

        day_summaries = []
        for day in days:
            m = self.run_day(day, verbose=verbose)
            day_summaries.append(m.get_summary())

        return day_summaries
