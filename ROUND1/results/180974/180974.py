"""
IMC Prosperity Round 1 - Final Submission
==========================================
Self-contained Trader class for ASH_COATED_OSMIUM and INTARIAN_PEPPER_ROOT.

ASH_COATED_OSMIUM: Pure penny market maker (no taking).
  - Known fair value = 10000 (strongly mean-reverting, ACF(1)=-0.5).
  - MAKING ONLY: penny the book (best_bid+1 / best_ask-1) with full capacity.
  - No taking code — visible book depth is stale/unmatchable on the platform,
    and taking orders create tight passive levels that cannibalize wider pennies.

INTARIAN_PEPPER_ROOT: Buy-and-hold drift capture.
  - Structural upward drift of ~0.1 pt/tick.
  - Sweep only cheapest ask level per tick (saves ~1.7 pt/unit on entry).
  - Passive penny bid catches remaining capacity.
"""
from datamodel import OrderDepth, TradingState, Order

FAIR_ASH = 10000
POSITION_LIMIT = 80

PEPPER_PARAMS = {
    "max_passive_size": 30,
    "position_limit": 80,
}


class Trader:

    def run(self, state: TradingState):
        result = {}

        sym = "ASH_COATED_OSMIUM"
        if sym in state.order_depths:
            result[sym] = self._trade_ash(state, sym)

        sym = "INTARIAN_PEPPER_ROOT"
        if sym in state.order_depths:
            result[sym] = self._trade_pepper(state, sym)

        return result, 0, ""

    def _get_book(self, state, sym):
        od = state.order_depths.get(sym, OrderDepth())
        bids = {p: abs(v) for p, v in od.buy_orders.items()} if od.buy_orders else {}
        asks = {p: abs(v) for p, v in od.sell_orders.items()} if od.sell_orders else {}
        mid = None
        if bids and asks:
            mid = (max(bids.keys()) + min(asks.keys())) / 2
        elif bids:
            mid = max(bids.keys())
        elif asks:
            mid = min(asks.keys())
        return bids, asks, mid

    def _trade_ash(self, state, sym):
        bids, asks, mid = self._get_book(state, sym)
        position = state.position.get(sym, 0)
        limit = POSITION_LIMIT
        fair = FAIR_ASH
        orders = []
        pos = position

        # MAKING ONLY — penny the book with full remaining capacity.
        best_bid = max(bids.keys()) if bids else None
        best_ask = min(asks.keys()) if asks else None

        bid_price = fair - 1
        ask_price = fair + 1

        if best_bid is not None:
            penny_bid = int(best_bid) + 1
            if penny_bid < fair:
                bid_price = penny_bid
        if best_ask is not None:
            penny_ask = int(best_ask) - 1
            if penny_ask > fair:
                ask_price = penny_ask

        bid_vol = limit - pos
        ask_vol = limit + pos

        if bid_vol > 0:
            orders.append(Order(sym, int(bid_price), int(bid_vol)))
        if ask_vol > 0:
            orders.append(Order(sym, int(ask_price), -int(ask_vol)))

        return orders

    def _trade_pepper(self, state, sym):
        p = PEPPER_PARAMS
        bids, asks, _ = self._get_book(state, sym)
        position = state.position.get(sym, 0)
        limit = p["position_limit"]
        orders = []
        if position >= limit:
            return orders

        pos = position

        # Sweep only the cheapest ask level per tick to save on entry cost.
        if asks:
            cheapest_ask = min(asks.keys())
            room = limit - pos
            vol = min(asks[cheapest_ask], room)
            if vol > 0:
                orders.append(Order(sym, int(cheapest_ask), int(vol)))
                pos += vol

        # Passive penny bid to catch remaining room.
        if bids and pos < limit:
            best_bid = max(bids.keys())
            bid_price = int(best_bid) + 1
            bid_vol = min(limit - pos, p["max_passive_size"])
            if bid_vol > 0:
                orders.append(Order(sym, bid_price, int(bid_vol)))

        return orders