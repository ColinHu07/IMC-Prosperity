"""
IMC Prosperity Round 1 - Final Submission
==========================================
Self-contained Trader class for ASH_COATED_OSMIUM and INTARIAN_PEPPER_ROOT.

ASH_COATED_OSMIUM: Penny-the-book market maker.
  - Known fair value = 10000 (strongly mean-reverting).
  - TAKING: sweep all depth through fair; flatten at fair.
  - MAKING: penny the book (best_bid+1 / best_ask-1) with full capacity.
    Captures wide spread (~15-20pt) instead of quoting tight at fair±1.

INTARIAN_PEPPER_ROOT: Buy-and-hold drift capture.
  - Structural upward drift of ~100/day on IMC engine (1000-step day).
  - Sweep all ask levels to reach +80 as fast as possible.
  - Hold with no selling.
"""
from datamodel import OrderDepth, TradingState, Order
import json

FAIR_ASH = 10000
POSITION_LIMIT = 80

PEPPER_PARAMS = {
    "max_passive_size": 30,
    "position_limit": 80,
}


class Trader:

    def run(self, state: TradingState):
        result = {}
        new_td = {}

        try:
            old_td = json.loads(state.traderData) if state.traderData else {}
        except:
            old_td = {}

        # ASH_COATED_OSMIUM
        sym = "ASH_COATED_OSMIUM"
        if sym in state.order_depths:
            orders, td_update = self._trade_ash(state, sym, old_td)
            result[sym] = orders
            new_td.update(td_update)

        # INTARIAN_PEPPER_ROOT
        sym = "INTARIAN_PEPPER_ROOT"
        if sym in state.order_depths:
            orders, td_update = self._trade_pepper(state, sym, old_td)
            result[sym] = orders
            new_td.update(td_update)

        try:
            trader_data = json.dumps(new_td)
        except:
            trader_data = ""

        return result, 0, trader_data

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

    def _trade_ash(self, state, sym, old_td):
        bids, asks, mid = self._get_book(state, sym)
        position = state.position.get(sym, 0)
        limit = POSITION_LIMIT
        fair = FAIR_ASH
        orders = []
        pos = position

        # 1. TAKING — buy asks < fair, sell bids > fair.
        #    Flatten at fair (buy at fair if short, sell at fair if long).
        if asks:
            for ap in sorted(asks.keys()):
                if ap < fair or (ap == fair and pos < 0):
                    max_buy = limit - pos
                    vol = min(asks[ap], max_buy)
                    if vol > 0:
                        orders.append(Order(sym, int(ap), vol))
                        pos += vol

        if bids:
            for bp in sorted(bids.keys(), reverse=True):
                if bp > fair or (bp == fair and pos > 0):
                    max_sell = limit + pos
                    vol = min(bids[bp], max_sell)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol

        # 2. MAKING — penny the book with full remaining capacity.
        #    Wide book (e.g. 9992/10011) → penny at 9993/10010 to capture ~17pt spread.
        #    Cap at fair±1 to never cross fair.
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

        return orders, {}

    def _trade_pepper(self, state, sym, old_td):
        p = PEPPER_PARAMS
        bids, asks, _ = self._get_book(state, sym)
        position = state.position.get(sym, 0)
        limit = p["position_limit"]
        orders = []
        if position >= limit:
            return orders, {}

        pos = position

        # Sweep all ask levels aggressively to reach +80 as fast as possible.
        if asks:
            for ap in sorted(asks.keys()):
                room = limit - pos
                if room <= 0:
                    break
                vol = min(asks[ap], room)
                if vol > 0:
                    orders.append(Order(sym, int(ap), int(vol)))
                    pos += vol

        # Passive penny bid to catch any remaining room.
        if bids and pos < limit:
            best_bid = max(bids.keys())
            bid_price = int(best_bid) + 1
            bid_vol = min(p["max_passive_size"], limit - pos)
            if bid_vol > 0:
                orders.append(Order(sym, bid_price, int(bid_vol)))

        return orders, {}