"""
IMC Prosperity Round 1 - Final Submission
==========================================
Self-contained Trader class for ASH_COATED_OSMIUM and INTARIAN_PEPPER_ROOT.

ASH_COATED_OSMIUM: Multi-level market maker with aggressive inventory cycling.
  - Known fair value = 10000 (strongly mean-reverting).
  - TAKING: sweep all depth through fair; flatten at fair.
  - MAKING: two layers —
      Tight layer at fair±2 for high fill rate + fast inventory turnover.
      Penny layer at best±1 for wide spread capture.
    Inventory-skewed sizing pushes harder to flatten when position builds.

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
        #    Buy asks <= fair-1; sell bids >= fair+1.
        #    At fair: flatten only (buy if short, sell if long).
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

        # 2. MAKING — two-layer quotes with inventory-skewed sizing.
        #    Tight layer (fair±2): high fill rate, fast inventory turnover.
        #    Penny layer (best±1): wide spread capture.
        #    When inventory builds, shift more size to the flattening side's
        #    tight layer to cycle faster and free capacity.
        best_bid = max(bids.keys()) if bids else None
        best_ask = min(asks.keys()) if asks else None

        tight_bid = fair - 2
        tight_ask = fair + 2

        penny_bid = fair - 1
        penny_ask = fair + 1
        if best_bid is not None:
            pb = int(best_bid) + 1
            if pb < fair:
                penny_bid = pb
        if best_ask is not None:
            pa = int(best_ask) - 1
            if pa > fair:
                penny_ask = pa

        buy_room = limit - pos
        sell_room = limit + pos

        inv_ratio = pos / limit  # -1 to +1

        # When long: push more size to tight ask (sell faster).
        # When short: push more size to tight bid (buy faster).
        tight_frac = min(0.5 + abs(inv_ratio) * 0.4, 0.9)
        flatten_tight = tight_frac
        build_tight = 1.0 - tight_frac

        if buy_room > 0:
            if penny_bid != tight_bid and penny_bid < tight_bid:
                # Penny is wider than tight — split across both levels.
                buy_tight_frac = flatten_tight if pos < 0 else build_tight
                tight_vol = max(1, int(buy_room * buy_tight_frac))
                penny_vol = buy_room - tight_vol
                orders.append(Order(sym, tight_bid, tight_vol))
                if penny_vol > 0:
                    orders.append(Order(sym, penny_bid, penny_vol))
            else:
                # Penny is at or above tight — just use tight level.
                orders.append(Order(sym, tight_bid, buy_room))

        if sell_room > 0:
            if penny_ask != tight_ask and penny_ask > tight_ask:
                sell_tight_frac = flatten_tight if pos > 0 else build_tight
                tight_vol = max(1, int(sell_room * sell_tight_frac))
                penny_vol = sell_room - tight_vol
                orders.append(Order(sym, tight_ask, -tight_vol))
                if penny_vol > 0:
                    orders.append(Order(sym, penny_ask, -penny_vol))
            else:
                orders.append(Order(sym, tight_ask, -sell_room))

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