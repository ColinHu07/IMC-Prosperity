"""
IMC Prosperity Round 1 - Final Submission
==========================================
Self-contained Trader class for ASH_COATED_OSMIUM and INTARIAN_PEPPER_ROOT.

ASH_COATED_OSMIUM: Tight-spread market maker with inventory skew.
  - Known fair value = 10000 (strongly mean-reverting).
  - TAKING: sweep all depth through fair.
  - MAKING: tight quotes at fair-1/fair+1, skewed by inventory.
    Multi-level: tight layer (15 units) + deep penny layer (rest).

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

        # 1. TAKING — sweep all depth through fair.
        #    Buy asks < fair; sell bids > fair.
        #    At fair: only flatten (buy at fair if short, sell at fair if long).
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

        # 2. MAKING — tight quotes at fair±1, skewed by inventory.
        #    Inventory skew: when long, widen bid & keep ask tight to sell faster.
        #    When short, widen ask & keep bid tight to buy faster.
        #    Multi-level: tight layer near fair + deep penny layer for sweep flow.
        inv_ratio = pos / limit  # -1 to +1
        skew = round(inv_ratio * 3)  # -3 to +3

        tight_bid = fair - 1 - max(0, skew)
        tight_ask = fair + 1 + max(0, -skew)
        tight_bid = min(tight_bid, fair - 1)
        tight_ask = max(tight_ask, fair + 1)

        best_bid = max(bids.keys()) if bids else None
        best_ask = min(asks.keys()) if asks else None

        deep_bid = tight_bid
        deep_ask = tight_ask
        if best_bid is not None:
            penny_bid = int(best_bid) + 1
            if penny_bid < tight_bid and penny_bid < fair:
                deep_bid = penny_bid
        if best_ask is not None:
            penny_ask = int(best_ask) - 1
            if penny_ask > tight_ask and penny_ask > fair:
                deep_ask = penny_ask

        buy_room = limit - pos
        sell_room = limit + pos

        TIGHT_SIZE = 25

        if buy_room > 0:
            tight_buy = min(buy_room, TIGHT_SIZE)
            orders.append(Order(sym, int(tight_bid), tight_buy))
            deep_buy = buy_room - tight_buy
            if deep_buy > 0 and deep_bid != tight_bid:
                orders.append(Order(sym, int(deep_bid), deep_buy))
            elif deep_buy > 0:
                orders[-1] = Order(sym, int(tight_bid), buy_room)

        if sell_room > 0:
            tight_sell = min(sell_room, TIGHT_SIZE)
            orders.append(Order(sym, int(tight_ask), -tight_sell))
            deep_sell = sell_room - tight_sell
            if deep_sell > 0 and deep_ask != tight_ask:
                orders.append(Order(sym, int(deep_ask), -deep_sell))
            elif deep_sell > 0:
                orders[-1] = Order(sym, int(tight_ask), -sell_room)

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