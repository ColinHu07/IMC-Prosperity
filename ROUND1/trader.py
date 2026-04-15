from datamodel import OrderDepth, TradingState, Order
import json
import math

# ===== OPTIMIZED PARAMETERS =====

ASH_PARAMS = {
    "anchor_price": 10000,
    "ewma_alpha": 0.07,
    "take_threshold": 0.2,       # More aggressive dip buying
    "make_width": 3,
    "inventory_skew_factor": 0.06,
    "max_passive_size": 40,
    "max_take_size": 30,
    "position_limit": 80,
    "imbalance_edge": 1.5,
}

PEPPER_PARAMS = {
    "rolling_window": 15,
    "take_threshold": 0.01,      # Max aggression on entries
    "sell_threshold": 50.0,      # EXIT at 50 points - ride winners
    "make_width": 1,
    "inventory_skew_factor": 0.015,  # Less drag on positions
    "max_passive_size": 80,
    "max_take_size": 80,
    "position_limit": 80,
    "z_score_alpha": 0.1,
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
        p = ASH_PARAMS
        bids, asks, mid = self._get_book(state, sym)
        position = state.position.get(sym, 0)
        limit = p["position_limit"]

        ewma_fair = old_td.get("ash_ewma", p["anchor_price"])
        if mid is not None:
            ewma_fair = p["ewma_alpha"] * mid + (1 - p["ewma_alpha"]) * ewma_fair

        td_out = {"ash_ewma": ewma_fair}
        orders = []

        if ewma_fair is None:
            return orders, td_out

        # Book imbalance
        imb = 0.0
        if bids and asks:
            bb = max(bids.keys())
            ba = min(asks.keys())
            bv = bids.get(bb, 0)
            av = asks.get(ba, 0)
            total = bv + av
            if total > 0:
                imb = (bv - av) / total
        
        base_fair = (ewma_fair + p["anchor_price"] * 2) / 3
        fair = base_fair + imb * p["imbalance_edge"]

        skew = -position * p["inventory_skew_factor"]
        pos = position

        # Spread capture: Buy and sell aggressively
        if asks:
            for ap in sorted(asks.keys()):
                if ap < fair - p["take_threshold"] + skew:
                    max_buy = limit - pos
                    vol = min(asks[ap], p["max_take_size"], max_buy)
                    if vol > 0:
                        orders.append(Order(sym, int(ap), vol))
                        pos += vol

        if bids:
            for bp in sorted(bids.keys(), reverse=True):
                if bp > fair + p["take_threshold"] + skew:
                    max_sell = limit + pos
                    vol = min(bids[bp], p["max_take_size"], max_sell)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol

        # Tight passive making for spread capture
        bid_price = math.floor(fair - p["make_width"] + skew)
        ask_price = math.ceil(fair + p["make_width"] + skew)

        bid_vol = min(p["max_passive_size"], limit - pos)
        ask_vol = min(p["max_passive_size"], limit + pos)

        if bid_vol > 0:
            orders.append(Order(sym, int(bid_price), int(bid_vol)))
        if ask_vol > 0:
            orders.append(Order(sym, int(ask_price), -int(ask_vol)))

        return orders, td_out

    def _trade_pepper(self, state, sym, old_td):
        p = PEPPER_PARAMS
        bids, asks, mid = self._get_book(state, sym)
        position = state.position.get(sym, 0)
        limit = p["position_limit"]

        history = old_td.get("pep_history", [])

        if mid is not None:
            history.append(mid)
            if len(history) > p["rolling_window"]:
                history.pop(0)

        td_out = {"pep_history": history}
        orders = []

        # Simple: just accumulate
        fair = mid if mid is not None else (history[-1] if history else 12000)
        pos = position

        # AGGRESSIVE TAKING: Buy any ask
        if asks:
            for ap in sorted(asks.keys()):
                max_buy = limit - pos
                if max_buy > 0:
                    vol = min(asks[ap], p["max_take_size"], max_buy)
                    if vol > 0:
                        orders.append(Order(sym, int(ap), vol))
                        pos += vol
                        if pos >= limit:
                            break

        # AGGRESSIVE PASSIVE: Post big buy orders far from mid to catch more
        if pos < limit:
            # Post buy orders at multiple levels to catch volume
            for offset in [5, 10, 15, 20]:
                if pos >= limit:
                    break
                bid_price = int(fair - offset)
                bid_vol = min(50, limit - pos)
                if bid_vol > 0:
                    orders.append(Order(sym, bid_price, bid_vol))
                    pos += bid_vol

        # NEVER sell under normal circumstances
        # Only sell if position is at max and we need to rebalance
        if pos > limit - 1:
            # Post a tiny ask at a huge price to avoid selling
            ask_price = int(fair + 100)
            orders.append(Order(sym, ask_price, -1))

        return orders, td_out