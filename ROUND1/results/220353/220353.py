from datamodel import OrderDepth, TradingState, Order
import json
import math


POS_LIMIT_ASH = 80
POS_LIMIT_PEP = 50


class Trader:
    """Drift core (PEPPER) + inventory-band MM (ASH)."""

    def run(self, state: TradingState):
        results = {}
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

            results[sym] = orders
            td.update(td_part)

        try:
            trader_data = json.dumps(td)
        except Exception:
            trader_data = ""

        return results, 0, trader_data

    def _trade_ash(self, sym, bids, asks, mid, position, old):
        LIMIT = POS_LIMIT_ASH
        HARD_BAND = 50
        SOFT_BAND = 20
        TAKE_THRESH = 7
        MAX_TAKE = 12
        MAX_MAKE = 24
        ANCHOR = 10000.0
        EWMA_ALPHA = 0.08

        prev_ewma = old.get("a_ewma")
        if prev_ewma is None:
            prev_ewma = mid if mid is not None else ANCHOR
        ewma = prev_ewma if mid is None else EWMA_ALPHA * mid + (1 - EWMA_ALPHA) * prev_ewma
        fair = 0.7 * ANCHOR + 0.3 * ewma

        pos = position
        orders = []

        # Aggressively take only clear deviations from fair.
        if asks and mid is not None:
            for ap in sorted(asks):
                if ap <= fair - TAKE_THRESH and pos < HARD_BAND:
                    room = HARD_BAND - pos
                    vol = min(asks[ap], MAX_TAKE, room)
                    if vol > 0:
                        orders.append(Order(sym, int(ap), vol))
                        pos += vol
                else:
                    break

        if bids and mid is not None:
            for bp in sorted(bids, reverse=True):
                if bp >= fair + TAKE_THRESH and pos > -HARD_BAND:
                    room = HARD_BAND + pos
                    vol = min(bids[bp], MAX_TAKE, room)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol
                else:
                    break

        best_bid = max(bids) if bids else int(fair) - 8
        best_ask = min(asks) if asks else int(fair) + 8

        # Inventory banding: lean quotes against current inventory.
        inv_shift = 0
        if pos > SOFT_BAND:
            inv_shift = 2
        elif pos < -SOFT_BAND:
            inv_shift = -2

        bid_px1 = min(best_bid + 1 - inv_shift, math.floor(fair) - 1)
        ask_px1 = max(best_ask - 1 - inv_shift, math.ceil(fair) + 1)
        bid_px2 = bid_px1 - 2
        ask_px2 = ask_px1 + 2

        buy_room = max(0, HARD_BAND - pos)
        sell_room = max(0, HARD_BAND + pos)

        bid_v1 = min(MAX_MAKE, int(buy_room * 0.6))
        bid_v2 = min(MAX_MAKE, max(0, buy_room - bid_v1))
        ask_v1 = min(MAX_MAKE, int(sell_room * 0.6))
        ask_v2 = min(MAX_MAKE, max(0, sell_room - ask_v1))

        if bid_v1 > 0:
            orders.append(Order(sym, int(bid_px1), bid_v1))
        if bid_v2 > 0 and bid_px2 != bid_px1:
            orders.append(Order(sym, int(bid_px2), bid_v2))
        if ask_v1 > 0:
            orders.append(Order(sym, int(ask_px1), -ask_v1))
        if ask_v2 > 0 and ask_px2 != ask_px1:
            orders.append(Order(sym, int(ask_px2), -ask_v2))

        return orders, {"a_ewma": ewma}

    def _trade_pepper(self, sym, bids, asks, mid, position, old):
        LIMIT = POS_LIMIT_PEP
        MAX_TAKE = 15
        MAX_PASSIVE = 15
        TREND_PRIOR = 0.1

        n = old.get("p_n", 0)
        sx = old.get("p_sx", 0.0)
        sy = old.get("p_sy", 0.0)
        sxy = old.get("p_sxy", 0.0)
        sxx = old.get("p_sxx", 0.0)
        slope = old.get("p_slope", TREND_PRIOR)
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
                slope = (n * sxy - sx * sy) / denom
                base = (sy - slope * sx) / n
            elif base is None:
                base = mid

        td = {
            "p_n": n,
            "p_sx": sx,
            "p_sy": sy,
            "p_sxy": sxy,
            "p_sxx": sxx,
            "p_slope": slope,
            "p_base": base,
        }

        orders = []
        if base is None or mid is None:
            return orders, td

        fair = base + slope * step
        residual = mid - fair
        pos = position

        # Target-position schedule: mostly max long, de-risk only on strong positive residual.
        if residual < 6:
            target = LIMIT
        elif residual < 12:
            target = 40
        else:
            target = 25

        # Guard for drift regime changes.
        if slope < 0:
            target = 0
        elif slope < 0.03:
            target = min(target, 30)

        if asks and pos < target:
            best_ask = min(asks)
            room = target - pos
            vol = min(asks[best_ask], MAX_TAKE, room)
            if vol > 0:
                orders.append(Order(sym, int(best_ask), vol))
                pos += vol

        if pos < target:
            best_bid = max(bids) if bids else int(fair) - 2
            bid_px = min(best_bid + 1, math.floor(fair + 1))
            bid_vol = min(MAX_PASSIVE, target - pos)
            if bid_vol > 0:
                orders.append(Order(sym, int(bid_px), bid_vol))

        if bids and pos > target:
            for bp in sorted(bids, reverse=True):
                if bp >= fair + 18 and pos > target:
                    vol = min(bids[bp], 6, pos - target)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol

        if pos > 0:
            ask_px = math.ceil(fair + 25)
            ask_vol = min(6, pos)
            orders.append(Order(sym, int(ask_px), -ask_vol))

        return orders, td