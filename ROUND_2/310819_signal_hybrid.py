try:
    from datamodel import OrderDepth, TradingState, Order
except ImportError:
    from prosperity4bt.datamodel import OrderDepth, TradingState, Order

import json
import math


POSITION_LIMIT = 80
MAF_BID = 6400

ASH_EWMA_ALPHA = 0.06
ASH_IMBALANCE_ALPHA = 4.25
ASH_TAKE_EDGE = 1.25
ASH_QUOTE_EDGE = 2.35
ASH_INV_SKEW = 0.08
ASH_MAX_PASSIVE_PER_SIDE = 18
ASH_ANCHOR = 10000.0
ASH_ANCHOR_BLEND = 0.04

PEPPER_ANCHOR_ALPHA = 0.02
PEPPER_BUY_PREMIUM_EARLY = 9.0
PEPPER_BUY_PREMIUM_LATE = 5.0
PEPPER_SIGNAL_ALPHA = 2.0
PEPPER_PEEL_EDGE = 3.0
PEPPER_REBUY_EDGE = 2.0
PEPPER_PEEL_CLIP = 4
PEPPER_PEEL_COOLDOWN = 6


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


def _imbalance(bids, asks):
    if bids and asks:
        bb = max(bids)
        ba = min(asks)
        bv = bids.get(bb, 0)
        av = asks.get(ba, 0)
        total = bv + av
        if total > 0:
            return (bv - av) / total
    return 0.0


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


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
                orders, td_part = self._trade_pepper(sym, bids, asks, mid, pos, state.timestamp, old)

            result[sym] = orders
            td.update(td_part)

        try:
            trader_data = json.dumps(td)
        except Exception:
            trader_data = ""

        return result, 0, trader_data

    def _trade_ash(self, sym, bids, asks, mid, position, old):
        orders = []
        imbalance = _imbalance(bids, asks)

        fair = old.get("a_fair")
        if fair is None:
            fair = mid if mid is not None else ASH_ANCHOR

        if mid is not None:
            fair = ASH_EWMA_ALPHA * mid + (1 - ASH_EWMA_ALPHA) * fair
            fair = (1 - ASH_ANCHOR_BLEND) * fair + ASH_ANCHOR_BLEND * ASH_ANCHOR

        td = {"a_fair": fair}
        if fair is None:
            return orders, td

        short_alpha = ASH_IMBALANCE_ALPHA * imbalance
        reservation = fair + short_alpha - position * ASH_INV_SKEW

        pos = position
        buy_room = POSITION_LIMIT - pos
        sell_room = POSITION_LIMIT + pos

        buy_threshold = reservation - ASH_TAKE_EDGE
        sell_threshold = reservation + ASH_TAKE_EDGE

        if asks:
            for ap in sorted(asks):
                if ap > buy_threshold or buy_room <= 0:
                    break
                vol = min(asks[ap], buy_room)
                if vol > 0:
                    orders.append(Order(sym, int(ap), int(vol)))
                    pos += vol
                    buy_room -= vol

        if bids:
            for bp in sorted(bids, reverse=True):
                if bp < sell_threshold or sell_room <= 0:
                    break
                vol = min(bids[bp], sell_room)
                if vol > 0:
                    orders.append(Order(sym, int(bp), int(-vol)))
                    pos -= vol
                    sell_room -= vol

        bid_quote = math.floor(reservation - ASH_QUOTE_EDGE)
        ask_quote = math.ceil(reservation + ASH_QUOTE_EDGE)

        if bids:
            bid_quote = min(bid_quote, max(bids) + 1)
        if asks:
            ask_quote = max(ask_quote, min(asks) - 1)
        if bid_quote >= ask_quote:
            bid_quote = ask_quote - 1

        signal_scale = _clamp(imbalance / 0.35, -1.0, 1.0)
        inv_ratio = pos / POSITION_LIMIT

        buy_scale = 1.0 + 0.70 * max(0.0, signal_scale) - 0.75 * max(0.0, inv_ratio)
        sell_scale = 1.0 + 0.70 * max(0.0, -signal_scale) - 0.75 * max(0.0, -inv_ratio)
        buy_size = min(buy_room, int(round(ASH_MAX_PASSIVE_PER_SIDE * _clamp(buy_scale, 0.25, 1.75))))
        sell_size = min(sell_room, int(round(ASH_MAX_PASSIVE_PER_SIDE * _clamp(sell_scale, 0.25, 1.75))))

        if buy_size > 0:
            orders.append(Order(sym, int(bid_quote), int(buy_size)))
        if sell_size > 0:
            orders.append(Order(sym, int(ask_quote), int(-sell_size)))

        return orders, td

    def _trade_pepper(self, sym, bids, asks, mid, position, timestamp, old):
        orders = []
        imbalance = _imbalance(bids, asks)
        raw_anchor = mid - timestamp / 1000.0 if mid is not None else None

        anchor = old.get("p_anchor")
        if anchor is None and raw_anchor is not None:
            anchor = round(raw_anchor)
        elif anchor is not None and raw_anchor is not None:
            anchor = (1 - PEPPER_ANCHOR_ALPHA) * anchor + PEPPER_ANCHOR_ALPHA * raw_anchor

        last_peel = old.get("p_last_peel", -10_000)
        td = {"p_anchor": anchor, "p_last_peel": last_peel}
        if anchor is None:
            return orders, td

        fair = anchor + timestamp / 1000.0 + PEPPER_SIGNAL_ALPHA * imbalance
        best_bid = max(bids) if bids else None
        best_ask = min(asks) if asks else None

        pos = position
        buy_room = POSITION_LIMIT - pos

        if buy_room > 0 and asks:
            premium = PEPPER_BUY_PREMIUM_EARLY if pos < POSITION_LIMIT * 0.75 else PEPPER_BUY_PREMIUM_LATE
            buy_ceiling = fair + premium
            for ap in sorted(asks):
                if ap > buy_ceiling or buy_room <= 0:
                    break
                vol = min(asks[ap], buy_room)
                if vol > 0:
                    orders.append(Order(sym, int(ap), int(vol)))
                    pos += vol
                    buy_room -= vol

        if buy_room > 0:
            bid_px = math.floor(fair + PEPPER_REBUY_EDGE)
            if best_bid is not None:
                bid_px = max(bid_px, best_bid + 1)
            orders.append(Order(sym, int(bid_px), int(buy_room)))

        if pos >= POSITION_LIMIT and best_bid is not None:
            rich_bid = best_bid >= fair + PEPPER_PEEL_EDGE
            adverse_queue = imbalance < -0.18
            cooled_down = timestamp - last_peel >= PEPPER_PEEL_COOLDOWN * 100
            if rich_bid and adverse_queue and cooled_down:
                clip = min(PEPPER_PEEL_CLIP, best_bid and bids.get(best_bid, 0), pos)
                if clip > 0:
                    orders.append(Order(sym, int(best_bid), int(-clip)))
                    td["p_last_peel"] = timestamp

        return orders, td
