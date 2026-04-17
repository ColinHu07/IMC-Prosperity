from datamodel import OrderDepth, TradingState, Order
import json
import math


POSITION_LIMIT = 80  # Both products — official Round 1 spec

# ASH parameters — anchored mean-reverter with Bayesian safeguards.
#
# The 10,000 anchor is not a fit to our 3 backtest days; it is a design
# constant of the simulator, confirmed empirically on every day we have
# ever seen (3 backtest days + submission 185651's live Round-1 day,
# live mean = 9,999.91 across 998 ticks).  Submission 185651 used this
# anchor and scored our best-ever $10,573.  Every subsequent EWMA-based
# variant scored less.
#
# Safeguards (not present in 185651):
#  1. Bayesian blend with prior strength ASH_ANCHOR_PRIOR_K.  Posterior
#     fair value = (K*ANCHOR + sum_mid) / (K+n).  With K=500 on a
#     1000-tick live day, the anchor dominates all day; if a future round
#     actually has a different mean, observations slowly overtake the prior.
#  2. Circuit breaker: if a fast EWMA drifts more than
#     ASH_BREAKER_DEVIATION from the anchor, fall back to pure EWMA
#     market making.  Bounds the downside if the anchor is wrong.
#  3. Per-tick take cap limits tail exposure on outlier books.
ASH_ANCHOR = 10000.0
ASH_ANCHOR_PRIOR_K = 500         # Bayesian prior strength (pseudo-observations)
ASH_DEV_SKEW = 0.3               # Lean into oscillation: skew fair by -dev*0.3
ASH_INV_SKEW = 0.02              # Inventory skew per unit of position
ASH_L1_FRACTION = 0.6            # Passive split: L1=penny, L2=touch
ASH_BREAKER_ALPHA = 0.02         # Fast EWMA for regime-shift detection (~35-tick HL)
ASH_BREAKER_DEVIATION = 40.0     # Fall back to EWMA if |fast - anchor| > this
ASH_MAX_TAKE_PER_TICK = 80       # Per-tick aggressive volume cap (tail safeguard)


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
    - PEPPER drifts linearly upward (~+0.1/tick).  Online expanding-window
      OLS learns the rate each session; ride at max long to capture drift
      plus spread.
    - ASH oscillates around a design anchor of 10,000 (confirmed on every
      available day, live mean = 9,999.91).  Fair value is a Bayesian
      blend (K=500 pseudo-obs at anchor) of that anchor with the session
      running mean, with a fast-EWMA circuit breaker that falls back to
      pure EWMA market making if a future round's session mean drifts
      more than ASH_BREAKER_DEVIATION from the anchor.

    Design rules:
    - Structural assumptions first, tuning second.
    - Every strong prior has a safeguard that degrades gracefully if the
      prior is wrong (Bayesian blend + circuit breaker on ASH; no hardcoded
      drift rate on PEPPER).
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
    # Anchored mean-reverter.  Fair value = Bayesian blend of the 10,000
    # design anchor with the session's running mean, falling back to a
    # fast EWMA if the session mean diverges far from the anchor (regime-
    # shift safeguard).  Deviation + inventory skew lean us into the
    # oscillation; two-level passive quotes (penny + touch) and aggressive
    # takes of any level already crossed through adj_fair capture edge.
    #
    # This reproduces the behavior of submission 185651 (our best-ever live
    # score, $10,573), with three added safeguards: Bayesian prior on the
    # anchor (K=500), fast-EWMA circuit breaker, and a per-tick take cap.
    # ------------------------------------------------------------------ ASH

    def _trade_ash(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        MAX_SIZE = 80
        ANCHOR = ASH_ANCHOR
        K = ASH_ANCHOR_PRIOR_K
        L1_FRAC = ASH_L1_FRACTION

        # --- Session statistics for Bayesian blend + circuit breaker ---
        n = old.get("a_n", 0)
        s = old.get("a_sum", 0.0)
        fast = old.get("a_fast_ewma")
        if mid is not None:
            n += 1
            s += mid
            fast = mid if fast is None else (
                ASH_BREAKER_ALPHA * mid + (1 - ASH_BREAKER_ALPHA) * fast
            )
        td = {"a_n": n, "a_sum": s, "a_fast_ewma": fast}

        orders = []
        if mid is None or fast is None:
            return orders, td

        # --- Fair value: Bayesian posterior, or EWMA fallback if regime shifted ---
        bayes_fair = (K * ANCHOR + s) / (K + n)
        anchored = abs(fast - ANCHOR) <= ASH_BREAKER_DEVIATION
        fair = bayes_fair if anchored else fast

        # Lean INTO the oscillation.  Below fair -> raise adj_fair (buy more,
        # resist selling).  Above fair -> lower adj_fair (sell more, resist
        # buying).  Plus small inventory term to keep position bounded.
        dev = mid - fair
        skew = -dev * ASH_DEV_SKEW - position * ASH_INV_SKEW
        adj_fair = fair + skew
        pos = position

        # --- Aggressive takes: sweep any level already crossed through fair ---
        taken_buy = 0
        taken_sell = 0
        agg_buy_vol = 0
        agg_sell_vol = 0
        if asks:
            for ap in sorted(asks):
                if taken_buy >= ASH_MAX_TAKE_PER_TICK:
                    break
                if ap < adj_fair:
                    room = LIMIT - pos
                    vol = min(asks[ap], MAX_SIZE, room,
                              ASH_MAX_TAKE_PER_TICK - taken_buy)
                    if vol > 0:
                        orders.append(Order(sym, int(ap), vol))
                        pos += vol
                        taken_buy += vol
                        agg_buy_vol += vol
        if bids:
            for bp in sorted(bids, reverse=True):
                if taken_sell >= ASH_MAX_TAKE_PER_TICK:
                    break
                if bp > adj_fair:
                    room = LIMIT + pos
                    vol = min(bids[bp], MAX_SIZE, room,
                              ASH_MAX_TAKE_PER_TICK - taken_sell)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol
                        taken_sell += vol
                        agg_sell_vol += vol

        # --- Two-level passive: L1 pennies the book, L2 matches the touch ---
        best_bid = max(bids) if bids else int(fair) - 8
        best_ask = min(asks) if asks else int(fair) + 8

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

        buy_room = max(0, LIMIT - position - agg_buy_vol)
        sell_room = max(0, LIMIT + position - agg_sell_vol)
        bid_vol1 = int(buy_room * L1_FRAC)
        bid_vol2 = buy_room - bid_vol1
        ask_vol1 = int(sell_room * L1_FRAC)
        ask_vol2 = sell_room - ask_vol1

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
    # Structural linear drift: price rises ~0.1/tick. Online OLS learns the
    # rate from the session so far; no hardcoded rate magnitude. Edge is to
    # ride at max long and capture drift + spread.
    # -------------------------------------------------------------- PEPPER

    def _trade_pepper(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        MAX_SIZE = 25
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
            # Bootstrap: get initial long exposure immediately.
            if asks:
                best_ask = min(asks)
                buy_sz = min(asks[best_ask], MAX_SIZE, LIMIT - position)
                if buy_sz > 0:
                    orders.append(Order(sym, int(best_ask), int(buy_sz)))
            if bids and position < LIMIT:
                bid_px = max(bids) + 1
                bid_sz = min(20, LIMIT - position)
                if bid_sz > 0:
                    orders.append(Order(sym, int(bid_px), int(bid_sz)))
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
                # Because rate > 0 (drift confirmed by OLS), paying one tick
                # over fair is still expected-profit-positive: over the rest
                # of the day the drift will cover it many times over.  Cap at
                # floor(fair)+1 to avoid runaway chase.
                if bid_px > fair + 1:
                    bid_px = math.floor(fair) + 1
            else:
                bid_px = math.floor(fair) - 1
            orders.append(Order(sym, int(bid_px), int(bid_vol)))

        ask_vol = min(MAX_SIZE, LIMIT + pos)
        if ask_vol > 0:
            ask_px = math.ceil(fair + 15)
            orders.append(Order(sym, int(ask_px), -int(ask_vol)))

        return orders, td
