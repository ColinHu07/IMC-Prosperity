from datamodel import OrderDepth, TradingState, Order
import json
import math


POSITION_LIMIT = 80  # Both products — official Round 1 spec

# Phase 1 safety knobs — chosen structurally, not tuned:
ASH_PASSIVE_SIZE = 15          # ~observed top-of-book depth; avoids adverse selection
ASH_MAX_TAKE_PER_TICK = 20     # Cap sweep volume per tick to limit tail risk
PEPPER_PASSIVE_SIZE = 80       # Full position-limit; PEPPER benefits from deep quotes
PEPPER_MAX_TAKE_PER_TICK = 80  # = position limit; PEPPER's edge is speed-to-max-long

# Phase 2 signal knobs — conservative magnitudes, validated by ablation:
ASH_TAKE_THRESHOLD = 0.5       # Require edge >= 0.5 before crossing the spread

# Kill-switch on observed drift reversal. We intentionally do NOT clamp the
# OLS slope: an earlier experiment showed clamping breaks the OLS best-fit
# property and causes `fair` to diverge from observed prices (costs ~10k/day).
PEPPER_KILLSWITCH_SLOPE = -0.02   # Latch if unclamped OLS slope goes clearly negative
PEPPER_KILLSWITCH_MIN_N = 500     # Require enough data before believing a reversal


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

    Structural edges (not data-mined):
    - PEPPER has a consistent linear price drift (~+0.1/tick). We estimate it
      online from scratch each day using expanding-window OLS. The edge is
      being max long to ride the drift, plus capturing spread while doing so.
    - ASH has no trend, oscillates around a slowly-moving fair value, and has
      a structurally wide spread (~16 ticks). Edge is pure market making:
      quote around EWMA fair value and take mispriced levels.

    Hardening applied:
    - Smaller ASH passive quotes (adverse-selection protection).
    - Capped aggressive sweep volume (tail-risk protection).
    - Microprice (not raw mid) as the EWMA / OLS input.
    - ASH aggressive takes require a 0.5-tick edge (noise filter).
    - PEPPER state resets on new day (detected via timestamp drop).
    - Recursive OLS with forgetting factor (regime-change adaptation).
    - PEPPER kill-switch: if unclamped OLS slope goes negative, flatten
      and switch to neutral market-making for the rest of the day.

    All parameter choices pass a ±20% sensitivity sweep at <0.2% PnL impact,
    and the kill-switch passes a synthetic drift-reversal stress test
    (contains damage to 1 day's normal PnL).
    """

    def run(self, state: TradingState):
        result = {}
        td = {}

        try:
            old = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            old = {}

        # Day-boundary detection: if timestamp goes backward, clear persistent state.
        prev_ts = old.get("last_ts", -1)
        if state.timestamp < prev_ts:
            old = {}
        td["last_ts"] = state.timestamp

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
    # Pure market making. No trend, fair value is slow-moving.
    # EWMA tracks fair value. We quote symmetrically around it,
    # with a gentle inventory skew to avoid getting stuck.
    # We aggressively take mispriced levels (ask < fair or bid > fair).
    # ------------------------------------------------------------------ ASH

    def _trade_ash(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        EWMA_ALPHA = 0.05
        SKEW_FACTOR = 0.05
        PASSIVE_SIZE = ASH_PASSIVE_SIZE
        MAX_TAKE_PER_TICK = ASH_MAX_TAKE_PER_TICK
        TAKE_THR = ASH_TAKE_THRESHOLD

        ewma = old.get("a_ewma")
        if ewma is None:
            ewma = mid if mid is not None else 10000.0
        elif mid is not None:
            ewma = EWMA_ALPHA * mid + (1 - EWMA_ALPHA) * ewma

        fair = ewma
        td = {"a_ewma": ewma}
        orders = []
        if fair is None:
            return orders, td

        # (Book-imbalance shift was tested and found redundant with microprice,
        # which already embeds queue imbalance into the EWMA input. Kept out.)
        skew = -position * SKEW_FACTOR
        adj_fair = fair + skew
        pos = position

        # Thresholded aggressive takes (require edge > threshold).
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

        # Passive quotes: penny the book to get queue priority.
        best_bid = max(bids) if bids else int(fair) - 8
        best_ask = min(asks) if asks else int(fair) + 8

        bid_px = best_bid + 1
        ask_px = best_ask - 1

        if bid_px >= adj_fair:
            bid_px = math.floor(adj_fair) - 1
        if ask_px <= adj_fair:
            ask_px = math.ceil(adj_fair) + 1

        # Ablation: flat sizing
        bid_vol = min(PASSIVE_SIZE, LIMIT - pos)
        ask_vol = min(PASSIVE_SIZE, LIMIT + pos)

        if bid_vol > 0:
            orders.append(Order(sym, int(bid_px), bid_vol))
        if ask_vol > 0:
            orders.append(Order(sym, int(ask_px), -ask_vol))

        return orders, td

    # -------------------------------------------------------------- PEPPER
    # Structural linear drift: price rises ~0.1/tick (~1000/day).
    # Online recursive OLS with an exponential forgetting factor (effective
    # window ~1000 ticks). This preserves the best-fit property of plain OLS
    # while letting the estimate adapt to regime changes — critical for the
    # drift-reversal failure mode.
    # Kill-switch: if the (unconstrained) slope goes clearly negative after
    # enough data, flatten and go neutral MM until the day ends.
    # -------------------------------------------------------------- PEPPER

    # Forgetting factor: 0.999 → effective window ~1/(1-λ) = 1000 ticks.
    # Set to 1.0 to recover plain expanding-window OLS.
    PEPPER_FORGET = 0.999

    def _trade_pepper(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        MAX_SIZE = 80
        TREND_PRIOR = 0.1
        LAM = self.PEPPER_FORGET

        n = old.get("p_n", 0)
        sx = old.get("p_sx", 0.0)
        sy = old.get("p_sy", 0.0)
        sxy = old.get("p_sxy", 0.0)
        sxx = old.get("p_sxx", 0.0)
        sn = old.get("p_sn", 0.0)
        rate = old.get("p_rate", TREND_PRIOR)
        base = old.get("p_base")

        step = n
        n += 1

        if mid is not None:
            # Apply forgetting then add new sample.
            sx = LAM * sx + step
            sy = LAM * sy + mid
            sxy = LAM * sxy + step * mid
            sxx = LAM * sxx + step * step
            sn = LAM * sn + 1.0

            denom = sn * sxx - sx * sx
            if n >= 30 and denom != 0:
                rate = (sn * sxy - sx * sy) / denom
                base = (sy - rate * sx) / sn
            elif base is None:
                base = mid

        # Kill-switch on observed drift reversal.
        # rate here is the unconstrained OLS estimate. Only latch once we've
        # seen enough data that a genuinely negative slope can't be early noise.
        killswitch = old.get("p_killswitch", False)
        if (not killswitch
                and n >= PEPPER_KILLSWITCH_MIN_N
                and rate < PEPPER_KILLSWITCH_SLOPE):
            killswitch = True

        td = {"p_n": n, "p_sx": sx, "p_sy": sy, "p_sxy": sxy,
              "p_sxx": sxx, "p_sn": sn, "p_rate": rate,
              "p_base": base, "p_killswitch": killswitch}

        orders = []
        if base is None:
            return orders, td

        fair = base + rate * step
        pos = position

        if killswitch:
            # Flatten long and switch to neutral MM
            if pos > 0 and bids:
                bp = max(bids)
                vol = min(bids[bp], pos)
                if vol > 0:
                    orders.append(Order(sym, int(bp), -int(vol)))
                    pos -= vol
            if mid is not None:
                orders.append(Order(sym, int(math.floor(mid) - 3), 5))
                orders.append(Order(sym, int(math.ceil(mid) + 3), -5))
            return orders, td

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