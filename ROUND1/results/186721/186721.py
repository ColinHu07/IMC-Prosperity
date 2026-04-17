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
        SKEW_FACTOR = 0.05         # Gentle inventory pressure
        MAX_SIZE = 80              # Max order size

        # --- Microprice: volume-weighted midpoint ---
        mprice = mid
        if bids and asks:
            bb = max(bids)
            ba = min(asks)
            bv = bids[bb]
            av = asks[ba]
            if bv + av > 0:
                mprice = (ba * bv + bb * av) / (bv + av)

        # --- Fair value: fixed anchor (no EWMA lag, no noise-chasing) ---
        fair = ANCHOR
        td = {}
        orders = []
        if fair is None:
            return orders, td

        # Deviation-based skew: lean INTO the oscillation.
        # When mid is below fair (bottom), raise adj_fair → buy more, resist selling.
        # When mid is above fair (top), lower adj_fair → sell more, resist buying.
        # Small inventory component keeps position bounded.
        dev = (mid - fair) if mid is not None else 0
        skew = -dev * 0.3 - position * 0.02
        adj_fair = fair + skew
        pos = position

        # Track aggressive fill volumes for position-limit safety
        agg_buy_vol = 0
        agg_sell_vol = 0

        # --- Aggressive takes: sweep all levels better than fair ---
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

        # --- Passive quotes: multi-level to increase fill rate ---
        best_bid = max(bids) if bids else int(fair) - 8
        best_ask = min(asks) if asks else int(fair) + 8

        # Remaining room after aggressive orders (worst case: all agg fill)
        buy_room = LIMIT - position - agg_buy_vol
        sell_room = LIMIT + position - agg_sell_vol

        # Level 1: penny the book (best priority)
        bid_px1 = best_bid + 1
        ask_px1 = best_ask - 1
        if bid_px1 >= adj_fair:
            bid_px1 = math.floor(adj_fair) - 1
        if ask_px1 <= adj_fair:
            ask_px1 = math.ceil(adj_fair) + 1

        # Level 2: match the book (more likely to fill)
        bid_px2 = best_bid
        ask_px2 = best_ask
        if bid_px2 >= adj_fair:
            bid_px2 = math.floor(adj_fair) - 2
        if ask_px2 <= adj_fair:
            ask_px2 = math.ceil(adj_fair) + 2

        # Split volume across levels (60/40 penny/match)
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

    # -------------------------------------------------------------- PEPPER
    # Structural linear drift: price rises ~0.1/tick (~1000/day).
    # Online OLS estimates trend from scratch each day.
    # Stay long to ride the drift, but avoid overpaying deep ask levels.
    # Execution edge here is robust: top-of-book taking + passive accumulation.
    # -------------------------------------------------------------- PEPPER

    def _trade_pepper(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        MAX_SIZE = 20
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

        # Target-position schedule: get long early without paying through depth.
        # This is execution shaping, not prediction fitting.
        if step < 20:
            target_pos = 25
        elif step < 80:
            target_pos = 50
        elif step < 200:
            target_pos = 70
        else:
            target_pos = LIMIT

        # If measured drift is weak after warmup, reduce target for safety.
        if n >= 40 and rate < 0.03:
            target_pos = min(target_pos, 40)

        # Build toward target with controlled aggression.
        if asks:
            sorted_asks = sorted(asks)
            need = max(0, target_pos - pos)
            if need > 0:
                best_ask = sorted_asks[0]
                premium_cap = 5 if need > 30 else 3 if need > 12 else 2
                take_clip = min(MAX_SIZE, need)
                if best_ask <= fair + premium_cap:
                    vol = min(asks[best_ask], take_clip, LIMIT - pos)
                else:
                    vol = 0
                if vol > 0:
                    orders.append(Order(sym, int(best_ask), int(vol)))
                    pos += vol

            # Optional second-level lift only when still underweight and cheap.
            need = max(0, target_pos - pos)
            if need > 12 and len(sorted_asks) > 1:
                second_ask = sorted_asks[1]
                if second_ask <= fair + 1:
                    vol2 = min(asks[second_ask], MAX_SIZE // 2, need, LIMIT - pos)
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
        bid_vol = min(20, max(0, target_pos - pos))
        if bid_vol > 0:
            if bids:
                bid_px = max(bids) + 1
                bid_cap = math.floor(fair + (1 if (target_pos - pos) > 20 else 0))
                if bid_px > bid_cap:
                    bid_px = bid_cap
            else:
                bid_px = math.floor(fair) - 1
            orders.append(Order(sym, int(bid_px), int(bid_vol)))

        # Ask far above fair to avoid selling into the trend
        ask_vol = min(10, LIMIT + pos)
        if ask_vol > 0:
            ask_px = math.ceil(fair + 15)
            orders.append(Order(sym, int(ask_px), -int(ask_vol)))

        return orders, td