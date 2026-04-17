from datamodel import OrderDepth, TradingState, Order
import json
import math


POSITION_LIMIT = 80  # Both products


class Trader:
    """
    Round 1 strategy for ASH_COATED_OSMIUM and INTARIAN_PEPPER_ROOT.
    """

    def run(self, state: TradingState):
        result = {}
        td = {}

        try:
            old = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            old = {}

        for sym in ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]:
            if sym not in state.order_depths:
                continue
            od = state.order_depths[sym]
            bids = {p: abs(v) for p, v in od.buy_orders.items()} if od.buy_orders else {}
            asks = {p: abs(v) for p, v in od.sell_orders.items()} if od.sell_orders else {}

            mid = None
            if bids and asks:
                mid = (max(bids) + min(asks)) / 2
            elif bids:
                mid = float(max(bids))
            elif asks:
                mid = float(min(asks))

            pos = state.position.get(sym, 0)

            if sym == "ASH_COATED_OSMIUM":
                orders, td_part = self._trade_ash(sym, bids, asks, mid, pos, old)
            else:
                orders, td_part = self._trade_pepper(sym, bids, asks, mid, pos, old)

            result[sym] = orders
            td.update(td_part)

        try:
            trader_data = json.dumps(td)
        except Exception:
            trader_data = ""

        return result, 0, trader_data

    def _trade_ash(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        ANCHOR = 10000.0
        MAX_SIZE = 80

        fair = ANCHOR
        td = {}
        orders = []
        if fair is None:
            return orders, td

        dev = (mid - fair) if mid is not None else 0
        skew = -dev * 0.3 - position * 0.02
        adj_fair = fair + skew
        pos = position

        agg_buy_vol = 0
        agg_sell_vol = 0

        if asks:
            for ap in sorted(asks):
                if ap < adj_fair:
                    room = LIMIT - pos
                    vol = min(asks[ap], MAX_SIZE, room)
                    if vol > 0:
                        orders.append(Order(sym, int(ap), vol))
                        pos += vol
                        agg_buy_vol += vol

        if bids:
            for bp in sorted(bids, reverse=True):
                if bp > adj_fair:
                    room = LIMIT + pos
                    vol = min(bids[bp], MAX_SIZE, room)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol
                        agg_sell_vol += vol

        best_bid = max(bids) if bids else int(fair) - 8
        best_ask = min(asks) if asks else int(fair) + 8

        buy_room = LIMIT - position - agg_buy_vol
        sell_room = LIMIT + position - agg_sell_vol

        bid_px1 = best_bid + 1
        ask_px1 = best_ask - 1
        if bid_px1 >= adj_fair:
            bid_px1 = math.floor(adj_fair) - 1
        if ask_px1 <= adj_fair:
            ask_px1 = math.ceil(adj_fair) + 1

        bid_px2 = best_bid
        ask_px2 = best_ask
        if bid_px2 >= adj_fair:
            bid_px2 = math.floor(adj_fair) - 2
        if ask_px2 <= adj_fair:
            ask_px2 = math.ceil(adj_fair) + 2

        bid_vol1 = min(int(buy_room * 0.6), buy_room)
        bid_vol2 = min(buy_room - bid_vol1, buy_room)
        ask_vol1 = min(int(sell_room * 0.6), sell_room)
        ask_vol2 = min(sell_room - ask_vol1, sell_room)

        if bid_vol1 > 0:
            orders.append(Order(sym, int(bid_px1), int(bid_vol1)))
        if bid_vol2 > 0 and bid_px2 != bid_px1:
            orders.append(Order(sym, int(bid_px2), int(bid_vol2)))
        if ask_vol1 > 0:
            orders.append(Order(sym, int(ask_px1), -int(ask_vol1)))
        if ask_vol2 > 0 and ask_px2 != ask_px1:
            orders.append(Order(sym, int(ask_px2), -int(ask_vol2)))

        return orders, td

    def _trade_pepper(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        MAX_SIZE = 20
        TREND_PRIOR = 0.1

        n = old.get("p_n", 0)
        sx = old.get("p_sx", 0.0)
        sy = old.get("p_sy", 0.0)
        sxy = old.get("p_sxy", 0.0)
        sxx = old.get("p_sxx", 0.0)
        rate = old.get("p_rate", TREND_PRIOR)
        base = old.get("p_base")

        step = n
        n += 1

        if mid is not None:
            sx += step
            sy += mid
            sxy += step * mid
            sxx += step * step

            denom = n * sxx - sx * sx
            if n >= 30 and denom != 0:
                rate = (n * sxy - sx * sy) / denom
                base = (sy - rate * sx) / n
            elif base is None:
                base = mid

        td = {
            "p_n": n,
            "p_sx": sx,
            "p_sy": sy,
            "p_sxy": sxy,
            "p_sxx": sxx,
            "p_rate": rate,
            "p_base": base,
        }

        orders = []
        if base is None:
            return orders, td

        fair = base + rate * step
        pos = position

        if asks:
            sorted_asks = sorted(asks)
            room = LIMIT - pos
            if room > 0:
                best_ask = sorted_asks[0]
                take_clip = MAX_SIZE if pos < LIMIT * 0.6 else MAX_SIZE // 2
                vol = min(asks[best_ask], take_clip, room)
                if vol > 0:
                    orders.append(Order(sym, int(best_ask), int(vol)))
                    pos += vol

            room = LIMIT - pos
            if room > 0 and len(sorted_asks) > 1:
                second_ask = sorted_asks[1]
                if second_ask <= fair + 2:
                    vol2 = min(asks[second_ask], MAX_SIZE // 2, room)
                    if vol2 > 0:
                        orders.append(Order(sym, int(second_ask), int(vol2)))
                        pos += vol2

        if bids and pos > 0:
            for bp in sorted(bids, reverse=True):
                if bp > fair + 15:
                    vol = min(bids[bp], 5, pos)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol

        bid_vol = min(20, LIMIT - pos)
        if bid_vol > 0:
            if bids:
                bid_px = max(bids) + 1
                bid_cap = math.floor(fair + 1)
                if bid_px > bid_cap:
                    bid_px = bid_cap
            else:
                bid_px = math.floor(fair) - 1
            orders.append(Order(sym, int(bid_px), int(bid_vol)))

        ask_vol = min(MAX_SIZE, LIMIT + pos)
        if ask_vol > 0:
            ask_px = math.ceil(fair + 15)
            orders.append(Order(sym, int(ask_px), -int(ask_vol)))

        return orders, td