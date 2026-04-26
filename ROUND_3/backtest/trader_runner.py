"""
Adapter that runs the Round 3 trader (R3trader.py) through the replay engine.
Same pattern as ROUND1/backtest/trader_runner.py, parameterised on the module
path so we can swap the strategy without touching the harness.
"""
import os
import sys

_R3 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _R3 not in sys.path:
    sys.path.insert(0, _R3)

from datamodel import OrderDepth, TradingState
from strategy.base import BaseStrategy


class TraderRunner(BaseStrategy):
    def __init__(self, trader, products, all_books):
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
        self._positions[product] = new_position

    def on_tick(self, timestamp, product, bids, asks, mid_price, position, trades):
        self._positions[product] = position

        if self._cache_ts != timestamp:
            order_depths = {}
            for p in self.products:
                snap = self.all_books.get(timestamp, {}).get(p)
                if snap is None:
                    continue
                od = OrderDepth()
                od.buy_orders = dict(snap["bids"]) if snap["bids"] else {}
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
