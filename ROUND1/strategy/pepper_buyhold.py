"""
INTARIAN_PEPPER_ROOT — simple buy-and-hold.

Accumulate long up to position_limit (aggressive lift at best ask + passive penny bid),
then hold with no sell orders. Relies on structural upward drift in the capsule data.
"""
from strategy.base import BaseStrategy


class PepperBuyHoldStrategy(BaseStrategy):
    """Buy aggressively toward limit, then hold flat long."""

    def __init__(self, params):
        self.params = params

    def reset(self):
        pass

    def on_tick(self, timestamp, product, bids, asks, mid_price, position, trades):
        p = self.params
        limit = p["position_limit"]
        if position >= limit:
            return []

        orders = []
        pos = position
        max_take = p.get("max_take_size", 50)
        max_passive = p.get("max_passive_size", 20)

        # Aggressive: lift best ask up to remaining room (one level per tick; engine-safe)
        if asks:
            best_ask = min(asks.keys())
            vol = min(asks[best_ask], limit - pos, max_take)
            if vol > 0:
                orders.append((best_ask, vol))
                pos += vol

        # Passive penny bid to add on dips (resolved on following ticks)
        if bids and pos < limit:
            best_bid = max(bids.keys())
            penny = best_bid + 1
            bid_need = limit - pos
            bid_vol = min(max_passive, bid_need)
            if bid_vol > 0:
                orders.append((penny, bid_vol))

        return orders
