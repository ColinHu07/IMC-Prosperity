from datamodel import OrderDepth, TradingState, Order
import json
import math


# ═══════════════════════════════════════════════════════════════════════
#  Round 2 Trader v5 — Maximum volume extraction
# ═══════════════════════════════════════════════════════════════════════
#
#  310725 analysis: ASH only fills on 7.5% of ticks (75/1000).
#  Market spread is ~16 but we quote at best±1 ≈ fair±7. Too wide.
#  Counterparties have ~14 units/tick — OUR passive cap isn't the limit.
#
#  Strategy: quote TIGHT to fair (fair±3) to fill on MORE ticks.
#  Accept lower spread per trade (~6 vs ~7.3) but fill 2-3x more often.
#
#  PEPPER: once at 80, mini market-make: sell small clips at fair+7,
#  rebuy at fair. Each round trip nets ~7×5=35, costs drift ~25 = +10.
# ═══════════════════════════════════════════════════════════════════════

POSITION_LIMIT = 80

# ─── MAF Configuration ────────────────────────────────────────────────
MAF_BID = 1250

# ─── ASH Parameters ──────────────────────────────────────────────────
ASH_ANCHOR = 10000.0
ASH_ANCHOR_PRIOR_K = 20
ASH_INV_SKEW = 0.15
ASH_EWMA_ALPHA = 0.05
ASH_BREAKER_DEVIATION = 40.0
ASH_HALF_SPREAD = 3               # Quote at fair±3 (tight!). Was ~7-8 via pennying.
ASH_MAX_PASSIVE_PER_SIDE = 30     # Enough to fill, not enough to get crushed
ASH_FLATTEN_THRESHOLD = 25        # Flatten early


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

    # ────────────────────────────────── ASH ──────────────────────────────
    # Tight-spread market maker. Key insight: filling on more ticks
    # matters more than wider spread per fill. Quote at fair±3 instead
    # of pennying the 16-tick market spread.
    #
    # With MAF (125% volume), more counterparties cross our tight quotes.
    # ────────────────────────────────── ASH ──────────────────────────────

    def _trade_ash(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        ANCHOR = ASH_ANCHOR
        K = ASH_ANCHOR_PRIOR_K
        HS = ASH_HALF_SPREAD

        n = old.get("a_n", 0)
        s = old.get("a_sum", 0.0)
        ewma = old.get("a_ewma")
        if mid is not None:
            n += 1
            s += mid
            ewma = mid if ewma is None else (
                ASH_EWMA_ALPHA * mid + (1 - ASH_EWMA_ALPHA) * ewma
            )
        td = {"a_n": n, "a_sum": s, "a_ewma": ewma}

        orders = []
        if mid is None or ewma is None:
            return orders, td

        # Fair value: blend EWMA with weak Bayesian anchor
        bayes_fair = (K * ANCHOR + s) / (K + n)
        if abs(ewma - ANCHOR) <= ASH_BREAKER_DEVIATION:
            fair = 0.7 * ewma + 0.3 * bayes_fair
        else:
            fair = ewma

        # Inventory skew pushes adj_fair to discourage building position
        skew = -position * ASH_INV_SKEW
        adj_fair = fair + skew
        pos = position

        # ── Step 1: Aggressive takes ──
        # Take any mispriced orders (below our adj fair on ask side, above on bid)
        agg_buy_vol = 0
        agg_sell_vol = 0
        if asks:
            for ap in sorted(asks):
                if ap >= adj_fair:
                    break
                room = LIMIT - pos
                vol = min(asks[ap], room)
                if vol > 0:
                    orders.append(Order(sym, int(ap), vol))
                    pos += vol
                    agg_buy_vol += vol
        if bids:
            for bp in sorted(bids, reverse=True):
                if bp <= adj_fair:
                    break
                room = LIMIT + pos
                vol = min(bids[bp], room)
                if vol > 0:
                    orders.append(Order(sym, int(bp), -vol))
                    pos -= vol
                    agg_sell_vol += vol

        # ── Step 2: Active flattening when position extreme ──
        FLATTEN = ASH_FLATTEN_THRESHOLD
        if position < -FLATTEN and asks:
            want = min(abs(position) - FLATTEN // 2, LIMIT - pos)
            for ap in sorted(asks):
                if want <= 0:
                    break
                if ap <= adj_fair + 3:
                    vol = min(asks[ap], want, LIMIT - pos)
                    if vol > 0:
                        orders.append(Order(sym, int(ap), vol))
                        pos += vol
                        want -= vol
                        agg_buy_vol += vol
        elif position > FLATTEN and bids:
            want = min(abs(position) - FLATTEN // 2, LIMIT + pos)
            for bp in sorted(bids, reverse=True):
                if want <= 0:
                    break
                if bp >= adj_fair - 3:
                    vol = min(bids[bp], want, LIMIT + pos)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol
                        want -= vol
                        agg_sell_vol += vol

        # ── Step 3: TIGHT passive quotes at fair ± half_spread ──
        # This is the key change: quote INSIDE the market spread.
        # Market spread ~16 ticks, we quote ~6 tick spread (fair±3).
        # This puts us at the FRONT of the queue for any crossing flow.
        bid_px = math.floor(adj_fair - HS)
        ask_px = math.ceil(adj_fair + HS)

        # Ensure we don't cross ourselves
        if bid_px >= ask_px:
            bid_px = math.floor(adj_fair) - 1
            ask_px = math.ceil(adj_fair) + 1

        buy_room = min(ASH_MAX_PASSIVE_PER_SIDE,
                       max(0, LIMIT - pos - agg_buy_vol))
        sell_room = min(ASH_MAX_PASSIVE_PER_SIDE,
                        max(0, LIMIT + pos - agg_sell_vol))

        # Inventory-aware: reduce the side building more position
        inv_ratio = position / LIMIT
        bid_scale = max(0.2, 1.0 - max(0, inv_ratio) * 0.8)
        ask_scale = max(0.2, 1.0 + min(0, inv_ratio) * 0.8)
        buy_room = int(buy_room * bid_scale)
        sell_room = int(sell_room * ask_scale)

        if buy_room > 0:
            orders.append(Order(sym, int(bid_px), int(buy_room)))
        if sell_room > 0:
            orders.append(Order(sym, int(ask_px), -int(sell_room)))

        return orders, td

    # ─────────────────────────────── PEPPER ──────────────────────────────
    # Drift rider + mini market-maker once full.
    # Phase 1 (pos < 80): buy aggressively to get max long ASAP.
    # Phase 2 (pos = 80): sell small clips at fair+7, rebuy at fair.
    #   Each round trip: ~7 ticks × 5 units = 35 gain
    #   Drift cost: ~0.1/tick × 100 ticks × 5 units = 50
    #   BUT we rebuy within ~50 ticks so real cost ~25. Net ~+10/cycle.
    #   With MAF volume this cycles faster.
    # ─────────────────────────────── PEPPER ──────────────────────────────

    def _trade_pepper(self, sym, bids, asks, mid, position, old):
        LIMIT = POSITION_LIMIT
        MAX_SIZE = 80
        TREND_PRIOR = 0.1
        MM_CLIP = 8                # Size of mini market-making clips
        MM_ASK_PREMIUM = 7         # Sell at fair + 7
        MM_BID_PREMIUM = 1         # Buy back at fair + 1 (still above fair, drift helps)

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

        # ── Phase 1: Build to max long ──
        if pos < LIMIT:
            # Aggressive buying: sweep asks up to fair + premium
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

            # Passive bid for remaining
            bid_vol = min(MAX_SIZE, LIMIT - pos)
            if bid_vol > 0:
                if bids:
                    bid_px = max(bids) + 1
                    if bid_px > fair + 2:
                        bid_px = math.floor(fair) + 2
                else:
                    bid_px = math.floor(fair)
                orders.append(Order(sym, int(bid_px), int(bid_vol)))

        # ── Phase 2: Mini market-make while staying mostly long ──
        # Once at 80, offer a small clip at fair+7 to capture spread
        # AND always have a bid to rebuy immediately
        if pos >= LIMIT - MM_CLIP:
            # Sell clip at premium
            ask_vol = min(MM_CLIP, LIMIT + pos)
            ask_px = math.ceil(fair + MM_ASK_PREMIUM)
            if ask_vol > 0:
                orders.append(Order(sym, int(ask_px), -int(ask_vol)))

            # If we sold some (pos < LIMIT), bid aggressively to rebuy
            if pos < LIMIT:
                rebuy_vol = min(MAX_SIZE, LIMIT - pos)
                rebuy_px = math.floor(fair + MM_BID_PREMIUM)
                if rebuy_vol > 0:
                    orders.append(Order(sym, int(rebuy_px), int(rebuy_vol)))
        else:
            # Not at full position yet — defensive ask far away
            ask_vol = min(MAX_SIZE, LIMIT + pos)
            if ask_vol > 0:
                ask_px = math.ceil(fair + 30)
                orders.append(Order(sym, int(ask_px), -int(ask_vol)))

        return orders, td