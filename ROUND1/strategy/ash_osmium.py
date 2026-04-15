"""
Strategy for ASH_COATED_OSMIUM.

Market structure summary:
- Near-static fair value oscillating around ~10000
- Wide spread (~16 ticks median)
- Slow mean-reverting deviations from fair (residual autocorr ~0.7)
- Book imbalance is highly predictive of next return (+/-3.5)
- EWMA(0.05) is the best single fair value model (MSE ~7.3)

Strategy: EWMA-based market making with aggressive taking and inventory skew.
"""
from strategy.base import BaseStrategy
from strategy.fair_value import EWMAFairValue
from strategy.signals import book_imbalance
from strategy.execution import compute_take_orders, compute_make_orders


class AshOsmiumStrategy(BaseStrategy):

    def __init__(self, params):
        self.params = params
        self.fair_model = EWMAFairValue(alpha=params["ewma_alpha"])
        self.step = 0

    def reset(self):
        self.fair_model = EWMAFairValue(alpha=self.params["ewma_alpha"])
        self.step = 0

    def on_tick(self, timestamp, product, bids, asks, mid_price, position, trades):
        self.step += 1
        p = self.params

        fair = self.fair_model.update(mid_price)
        if fair is None:
            return []

        # Book imbalance adjustment to fair value
        imb = book_imbalance(bids, asks)
        imb_adj = imb * p.get("imbalance_edge", 0.0)
        adjusted_fair = fair + imb_adj

        orders = []

        # Aggressive taking
        take_orders = compute_take_orders(
            adjusted_fair, bids, asks,
            take_threshold=p["take_threshold"],
            max_take_size=p["max_take_size"],
            position=position,
            position_limit=p["position_limit"],
            inventory_skew_factor=p["inventory_skew_factor"],
        )
        orders.extend(take_orders)

        # Update position estimate after takes
        pos_after_takes = position + sum(s for _, s in take_orders)

        # Passive making
        make_orders = compute_make_orders(
            adjusted_fair,
            make_width=p["make_width"],
            max_passive_size=p["max_passive_size"],
            position=pos_after_takes,
            position_limit=p["position_limit"],
            inventory_skew_factor=p["inventory_skew_factor"],
            bids=bids,
            asks=asks,
        )
        orders.extend(make_orders)

        return orders
