from datamodel import OrderDepth, TradingState, Order
import json
import math


POSITION_LIMIT = 80  # Both products


class Trader:
    """
    Round 1 strategy for ASH_COATED_OSMIUM and INTARIAN_PEPPER_ROOT.

    Structural edges (not data-mined):
    - PEPPER has a consistent linear price drift (~+0.1/tick). We estimate it
      online from scratch each day using expanding-window OLS. The edge is
      being max long to ride the drift, plus capturing spread while doing so.
    - ASH has no trend, oscillates around a slowly-moving fair value, and has
      a structurally wide spread (~16 ticks). Edge is pure market making:
      quote around EWMA fair value and take mispriced levels.
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

    # ------------------------------------------------------------------ ASH
    # Pure market making. No trend, fair value is slow-moving.
    # EWMA tracks fair value. We quote symmetrically around it,
    # with a gentle inventory skew to avoid getting stuck.
    # We aggressively take mispriced levels (ask < fair or bid > fair).
    # ------------------------------------------------------------------ ASH

    def _trade_ash(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        ANCHOR = 10000.0           # ASH mean-reverts to this level
        MAX_SIZE = 80              # Max order size

        # --- Fair value: fixed anchor (no EWMA lag, no noise-chasing) ---
        fair = ANCHOR
        td = {}
        orders = []

        skew = -position * SKEW_FACTOR
        adj_fair = fair + skew
        pos = position

        # --- Aggressive takes: sweep all levels better than fair ---
        # With 16-tick spread, anything below our fair is a good buy,
        # anything above is a good sell. No need for extra edge threshold.
        if asks:
            for ap in sorted(asks):
                if ap < adj_fair:
                    room = LIMIT - pos
                    vol = min(asks[ap], MAX_SIZE, room)
                    if vol > 0:
                        orders.append(Order(sym, int(ap), vol))
                        pos += vol

        if bids:
            for bp in sorted(bids, reverse=True):
                if bp > adj_fair:
                    room = LIMIT + pos
                    vol = min(bids[bp], MAX_SIZE, room)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol

        # --- Passive quotes: penny the book to get queue priority ---
        # Post just inside the best bid/ask to capture spread.
        best_bid = max(bids) if bids else int(fair) - 8
        best_ask = min(asks) if asks else int(fair) + 8

        bid_px = best_bid + 1
        ask_px = best_ask - 1

        # Safety: don't cross our own fair value
        if bid_px >= adj_fair:
            bid_px = math.floor(adj_fair) - 1
        if ask_px <= adj_fair:
            ask_px = math.ceil(adj_fair) + 1

        bid_vol = min(MAX_SIZE, LIMIT - pos)
        ask_vol = min(MAX_SIZE, LIMIT + pos)

        if bid_vol > 0:
            orders.append(Order(sym, int(bid_px), int(bid_vol)))
        if ask_vol > 0:
            orders.append(Order(sym, int(ask_px), -int(ask_vol)))

        return orders, td

    # -------------------------------------------------------------- PEPPER
    # Structural linear drift: price rises ~0.1/tick (~1000/day).
    # Online OLS estimates trend from scratch each day.
    # Stay long to ride the drift, but avoid overpaying deep ask levels.
    # Execution edge here is robust: top-of-book taking + passive accumulation.
    # -------------------------------------------------------------- PEPPER

    def _trade_pepper(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        MAX_SIZE = 80
        TREND_PRIOR = 0.1

        # --- Online OLS for trend fair value ---
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

        td = {"p_n": n, "p_sx": sx, "p_sy": sy, "p_sxy": sxy,
              "p_sxx": sxx, "p_rate": rate, "p_base": base}

        orders = []
        if base is None:
            return orders, td

        fair = base + rate * step
        pos = position

        # Build long exposure quickly with controlled aggression:
        # always lift best ask (single level) while underweight, and avoid
        # sweeping deep/expensive ask levels.
        if asks:
            sorted_asks = sorted(asks)
            room = LIMIT - pos
            if room > 0:
                best_ask = sorted_asks[0]
                # Opportunity cost of staying underinvested is large because
                # of structural drift. Take top-of-book every tick.
                take_clip = MAX_SIZE if pos < LIMIT * 0.6 else MAX_SIZE // 2
                vol = min(asks[best_ask], take_clip, room)
                if vol > 0:
                    orders.append(Order(sym, int(best_ask), int(vol)))
                    pos += vol

            # Optionally lift one more level only if still cheap vs fair.
            room = LIMIT - pos
            if room > 0 and len(sorted_asks) > 1:
                second_ask = sorted_asks[1]
                if second_ask <= fair + 2:
                    vol2 = min(asks[second_ask], MAX_SIZE // 2, room)
                    if vol2 > 0:
                        orders.append(Order(sym, int(second_ask), int(vol2)))
                        pos += vol2

        # Only sell at extreme premium (rare dislocation)
        if bids and pos > 0:
            for bp in sorted(bids, reverse=True):
                if bp > fair + 15:
                    vol = min(bids[bp], 5, pos)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol

        # Passive bid: primary way to fill remaining room.
        bid_vol = min(MAX_SIZE, LIMIT - pos)
        if bid_vol > 0:
            if bids:
                bid_px = max(bids) + 1
                bid_cap = math.floor(fair + 1)
                if bid_px > bid_cap:
                    bid_px = bid_cap
            else:
                bid_px = math.floor(fair) - 1
            orders.append(Order(sym, int(bid_px), int(bid_vol)))

        # Ask far above fair to avoid selling into the trend
        ask_vol = min(MAX_SIZE, LIMIT + pos)
        if ask_vol > 0:
            ask_px = math.ceil(fair + 15)
            orders.append(Order(sym, int(ask_px), -int(ask_vol)))

        return orders, td