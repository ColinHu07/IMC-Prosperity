"""
Adapter that runs the actual trader.py submission file through our backtest engine.

This guarantees "what we validate" == "what we ship". The replay engine expects
BaseStrategy.on_tick(...) returning a list of (price, size) tuples; we wrap the
Trader.run(state) API (which takes a full TradingState and returns a dict of
Order lists) to match.

Because trader.py operates per-state, we build a TradingState from the per-tick
book snapshot and feed all configured products to it in one call. We then split
the returned orders back per product.
"""
import os
import sys
import json

_ROUND1 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROUND1 not in sys.path:
    sys.path.insert(0, _ROUND1)

from datamodel import OrderDepth, TradingState  # noqa: E402
from strategy.base import BaseStrategy  # noqa: E402


class TraderRunner(BaseStrategy):
    """
    Wraps a Trader instance. Shared across products in a given run by the
    replay engine, but the engine still calls on_tick once per product. We
    therefore cache the most recent call per timestamp so we only invoke
    Trader.run once per timestamp.
    """

    def __init__(self, trader, products, all_books):
        """
        trader: instance exposing .run(state)
        products: list of product symbols to include in the TradingState
        all_books: {ts: {product: {'bids':..., 'asks':..., 'mid_price':...}}}
                   Needed because on_tick is called per product but Trader.run
                   expects all products at once.
        """
        self.trader = trader
        self.products = list(products)
        self.all_books = all_books
        self._trader_data = ""
        self._cache_ts = None
        self._cache_orders = {}
        self._positions = {p: 0 for p in products}

    def reset(self):
        self._trader_data = ""
        self._cache_ts = None
        self._cache_orders = {}
        self._positions = {p: 0 for p in self.products}

    def update_position(self, product, new_position):
        """The engine calls this so we can reflect fills in the next tick's state."""
        self._positions[product] = new_position

    def on_tick(self, timestamp, product, bids, asks, mid_price, position, trades):
        # Use engine-provided position (it has the most up-to-date fill info)
        self._positions[product] = position

        if self._cache_ts != timestamp:
            # Build TradingState for all products at this timestamp
            order_depths = {}
            for p in self.products:
                snap = self.all_books.get(timestamp, {}).get(p)
                if snap is None:
                    continue
                od = OrderDepth()
                od.buy_orders = dict(snap["bids"]) if snap["bids"] else {}
                # Prosperity convention: sell_orders have negative volumes.
                # trader.py takes abs() of them, so either sign works.
                od.sell_orders = {px: -abs(v) for px, v in snap["asks"].items()} \
                    if snap["asks"] else {}
                order_depths[p] = od

            state = TradingState(
                traderData=self._trader_data,
                timestamp=timestamp,
                order_depths=order_depths,
                position=dict(self._positions),
            )

            result = self.trader.run(state)
            # Trader.run returns (orders_dict, conversions, trader_data)
            # Some Prosperity templates return just orders_dict — handle both.
            if isinstance(result, tuple) and len(result) == 3:
                orders_dict, _conversions, new_td = result
            elif isinstance(result, tuple) and len(result) == 2:
                orders_dict, new_td = result
            else:
                orders_dict, new_td = result, self._trader_data

            self._trader_data = new_td or ""
            self._cache_ts = timestamp
            self._cache_orders = {}
            for p, orders in (orders_dict or {}).items():
                self._cache_orders[p] = [(o.price, o.quantity) for o in orders]

        return self._cache_orders.get(product, [])
