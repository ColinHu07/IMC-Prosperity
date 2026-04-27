from datamodel import OrderDepth, TradingState, Order
import json
import math


POSITION_LIMIT = 80  # Both products — official Round 1 spec

# Execution knobs — structural, not tuned:
ASH_PASSIVE_SIZE = 15          # ~observed top-of-book depth; adverse-selection guard
ASH_MAX_TAKE_PER_TICK = 20     # Cap sweep volume per tick to limit tail risk
ASH_TAKE_THRESHOLD = 0.5       # Require edge >= 0.5 before crossing the spread
ASH_L1_FRACTION = 0.6          # Passive split: L1=penny, L2=touch


def _microprice(bids, asks):
    """
    Volume-weighted midpoint using top-of-book. Less noisy than raw mid for
    wide-spread products because it already reflects queue imbalance.
    """
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
    """
    Round 1 strategy for ASH_COATED_OSMIUM and INTARIAN_PEPPER_ROOT.

    Structural edges:
    - PEPPER drifts linearly upward (~+0.1/tick). Online expanding-window OLS
      estimates the rate from scratch each session; we ride at max long to
      capture drift + spread.
    - ASH oscillates with no trend. Fair value is tracked with a fast EWMA of
      microprice; we quote symmetrically with an inventory skew and take
      mispriced levels once the edge exceeds 0.5 ticks.

    Design rules:
    - Structural assumptions first, tuning second.
    - No hardcoded price levels — every estimator learns from session data so
      a different anchor or drift rate in future rounds works automatically.
    - Minimal state, no dead safety knobs.
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

    # ------------------------------------------------------------------ ASH
    # Pure market making around a fast-tracking EWMA of microprice.
    #
    # We tried three variants of a mean-reversion overlay — fixed anchor,
    # session running mean, Bayesian prior — all three gave ~+40% optimistic
    # ASH PnL but regressed pessimistic PnL 15-27%.  The regression is
    # structural: the dev-skew makes aggressive crosses more frequent, which
    # pays off only when passive queues fill reliably.  On short (1000-tick)
    # live sessions the anchor also cannot converge.  We therefore keep the
    # robust EWMA market maker and rely on inventory skew alone.
    # ------------------------------------------------------------------ ASH

    def _trade_ash(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        EWMA_ALPHA = 0.05
        SKEW_FACTOR = 0.05
        PASSIVE_SIZE = ASH_PASSIVE_SIZE
        MAX_TAKE_PER_TICK = ASH_MAX_TAKE_PER_TICK
        TAKE_THR = ASH_TAKE_THRESHOLD
        L1_FRAC = ASH_L1_FRACTION

        ewma = old.get("a_ewma")
        if ewma is None:
            ewma = mid
        elif mid is not None:
            ewma = EWMA_ALPHA * mid + (1 - EWMA_ALPHA) * ewma

        fair = ewma
        td = {"a_ewma": ewma}
        orders = []
        if fair is None:
            return orders, td

        skew = -position * SKEW_FACTOR
        adj_fair = fair + skew
        pos = position

        taken = 0
        if asks:
            for ap in sorted(asks):
                if taken >= MAX_TAKE_PER_TICK:
                    break
                if ap <= adj_fair - TAKE_THR:
                    room = LIMIT - pos
                    vol = min(asks[ap], MAX_TAKE_PER_TICK - taken, room)
                    if vol > 0:
                        orders.append(Order(sym, int(ap), vol))
                        pos += vol
                        taken += vol

        taken = 0
        if bids:
            for bp in sorted(bids, reverse=True):
                if taken >= MAX_TAKE_PER_TICK:
                    break
                if bp >= adj_fair + TAKE_THR:
                    room = LIMIT + pos
                    vol = min(bids[bp], MAX_TAKE_PER_TICK - taken, room)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol
                        taken += vol

        best_bid = max(bids) if bids else int(fair) - 8
        best_ask = min(asks) if asks else int(fair) + 8

        # Passive L1 (penny) keeps queue priority.
        bid_px1 = best_bid + 1
        ask_px1 = best_ask - 1
        if bid_px1 >= adj_fair:
            bid_px1 = math.floor(adj_fair) - 1
        if ask_px1 <= adj_fair:
            ask_px1 = math.ceil(adj_fair) + 1

        # Passive L2 (touch) captures fills when L1 is crowded.
        bid_px2 = best_bid
        ask_px2 = best_ask
        if bid_px2 >= adj_fair:
            bid_px2 = math.floor(adj_fair) - 2
        if ask_px2 <= adj_fair:
            ask_px2 = math.ceil(adj_fair) + 2

        buy_cap = min(PASSIVE_SIZE, max(0, LIMIT - pos))
        sell_cap = min(PASSIVE_SIZE, max(0, LIMIT + pos))
        bid_vol1 = int(buy_cap * L1_FRAC)
        ask_vol1 = int(sell_cap * L1_FRAC)
        bid_vol2 = buy_cap - bid_vol1
        ask_vol2 = sell_cap - ask_vol1

        if bid_vol1 > 0:
            orders.append(Order(sym, int(bid_px1), bid_vol1))
        if ask_vol1 > 0:
            orders.append(Order(sym, int(ask_px1), -ask_vol1))

        if bid_vol2 > 0:
            if bid_px2 == bid_px1:
                orders.append(Order(sym, int(bid_px1), bid_vol2))
            else:
                orders.append(Order(sym, int(bid_px2), bid_vol2))
        if ask_vol2 > 0:
            if ask_px2 == ask_px1:
                orders.append(Order(sym, int(ask_px1), -ask_vol2))
            else:
                orders.append(Order(sym, int(ask_px2), -ask_vol2))

        return orders, td

    # -------------------------------------------------------------- PEPPER
    # Structural linear drift: price rises ~0.1/tick. Online OLS learns the
    # rate from the session so far; no hardcoded rate magnitude. Edge is to
    # ride at max long and capture drift + spread.
    # -------------------------------------------------------------- PEPPER

    def _trade_pepper(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        MAX_SIZE = 80
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

        td = {"p_n": n, "p_sx": sx, "p_sy": sy, "p_sxy": sxy,
              "p_sxx": sxx, "p_rate": rate, "p_base": base}

        orders = []
        if base is None:
            return orders, td

        fair = base + rate * step
        pos = position

        if asks:
            for ap in sorted(asks):
                room = LIMIT - pos
                if room <= 0:
                    break
                max_premium = 8 if pos < LIMIT * 0.8 else 3
                if ap <= fair + max_premium:
                    vol = min(asks[ap], MAX_SIZE, room)
                    if vol > 0:
                        orders.append(Order(sym, int(ap), vol))
                        pos += vol

        if bids and pos > 0:
            for bp in sorted(bids, reverse=True):
                if bp > fair + 15:
                    vol = min(bids[bp], 5, pos)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol

        bid_vol = min(MAX_SIZE, LIMIT - pos)
        if bid_vol > 0:
            if bids:
                bid_px = max(bids) + 1
                if bid_px > fair:
                    bid_px = math.floor(fair)
            else:
                bid_px = math.floor(fair) - 1
            orders.append(Order(sym, int(bid_px), int(bid_vol)))

        ask_vol = min(MAX_SIZE, LIMIT + pos)
        if ask_vol > 0:
            ask_px = math.ceil(fair + 15)
            orders.append(Order(sym, int(ask_px), -int(ask_vol)))

        return orders, td