try:
    from datamodel import OrderDepth, TradingState, Order
except ImportError:
    from prosperity4bt.datamodel import OrderDepth, TradingState, Order

import json
import math


# Based on 310818.py, but with a stronger ASH_COATED_OSMIUM market-making block.
# Local backtests on the provided round-2 days:
# - 310818.py:        267,070
# - this hybrid:      293,726

POSITION_LIMIT = 80

# Keep the baseline MAF that already performed well for the user in challenge runs.
MAF_BID = 1250


# ASH parameters tuned locally on the provided days.
ASH_INV_SKEW = 0.08
ASH_EWMA_ALPHA = 0.03
ASH_TAKE_EDGE = 1.5
ASH_QUOTE_EDGE = 2.5
ASH_MAX_PASSIVE_PER_SIDE = 16


def _microprice(bids, asks):
    if bids and asks:
        bb = max(bids)
        ba = min(asks)
        bv = bids.get(bb, 0)
        av = asks.get(ba, 0)
        if bv + av > 0:
            return (bb * av + ba * bv) / (bv + av)
        return (bb + ba) / 2
    if bids:
        return float(max(bids))
    if asks:
        return float(min(asks))
    return None


class Trader:
    def bid(self):
        return MAF_BID

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

            mid = _microprice(bids, asks)
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
        orders = []
        fair = old.get("a_fair")

        if fair is None:
            fair = mid

        if mid is not None:
            fair = mid if fair is None else ASH_EWMA_ALPHA * mid + (1 - ASH_EWMA_ALPHA) * fair

        td = {"a_fair": fair}

        if fair is None:
            return orders, td

        pos = position
        buy_room = POSITION_LIMIT - pos
        sell_room = POSITION_LIMIT + pos

        if asks:
            for ap in sorted(asks):
                if ap >= fair - ASH_TAKE_EDGE:
                    break
                vol = min(asks[ap], buy_room)
                if vol > 0:
                    orders.append(Order(sym, int(ap), int(vol)))
                    pos += vol
                    buy_room -= vol

        if bids:
            for bp in sorted(bids, reverse=True):
                if bp <= fair + ASH_TAKE_EDGE:
                    break
                vol = min(bids[bp], sell_room)
                if vol > 0:
                    orders.append(Order(sym, int(bp), int(-vol)))
                    pos -= vol
                    sell_room -= vol

        adj_fair = fair - pos * ASH_INV_SKEW
        bid_px = math.floor(adj_fair - ASH_QUOTE_EDGE)
        ask_px = math.ceil(adj_fair + ASH_QUOTE_EDGE)

        if bids:
            bid_px = min(bid_px, max(bids) + 1)
        if asks:
            ask_px = max(ask_px, min(asks) - 1)

        if bid_px >= ask_px:
            bid_px = ask_px - 1

        if buy_room > 0:
            orders.append(Order(sym, int(bid_px), int(min(ASH_MAX_PASSIVE_PER_SIDE, buy_room))))
        if sell_room > 0:
            orders.append(Order(sym, int(ask_px), -int(min(ASH_MAX_PASSIVE_PER_SIDE, sell_room))))

        return orders, td

    def _trade_pepper(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        MAX_SIZE = 80
        TREND_PRIOR = 0.1
        MM_CLIP = 8
        MM_ASK_PREMIUM = 7
        MM_BID_PREMIUM = 1

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

        if pos < LIMIT:
            if asks:
                for ap in sorted(asks):
                    room = LIMIT - pos
                    if room <= 0:
                        break
                    max_prem = 10 if pos < LIMIT * 0.8 else 5
                    if ap <= fair + max_prem:
                        vol = min(asks[ap], MAX_SIZE, room)
                        if vol > 0:
                            orders.append(Order(sym, int(ap), vol))
                            pos += vol

            bid_vol = min(MAX_SIZE, LIMIT - pos)
            if bid_vol > 0:
                if bids:
                    bid_px = max(bids) + 1
                    if bid_px > fair + 2:
                        bid_px = math.floor(fair) + 2
                else:
                    bid_px = math.floor(fair)
                orders.append(Order(sym, int(bid_px), int(bid_vol)))

        if pos >= LIMIT - MM_CLIP:
            ask_vol = min(MM_CLIP, LIMIT + pos)
            ask_px = math.ceil(fair + MM_ASK_PREMIUM)
            if ask_vol > 0:
                orders.append(Order(sym, int(ask_px), -int(ask_vol)))

            if pos < LIMIT:
                rebuy_vol = min(MAX_SIZE, LIMIT - pos)
                rebuy_px = math.floor(fair + MM_BID_PREMIUM)
                if rebuy_vol > 0:
                    orders.append(Order(sym, int(rebuy_px), int(rebuy_vol)))
        else:
            ask_vol = min(MAX_SIZE, LIMIT + pos)
            if ask_vol > 0:
                ask_px = math.ceil(fair + 30)
                orders.append(Order(sym, int(ask_px), -int(ask_vol)))

        return orders, td
