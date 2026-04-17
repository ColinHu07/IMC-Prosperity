from datamodel import OrderDepth, TradingState, Order
import json


POSITION_LIMIT = 50
TAKE_CLIP = 20
PASSIVE_CLIP = 20
NEG_SLOPE_THRESHOLD = -0.02
NEG_SLOPE_STREAK = 30
DE_RISK_CLIP = 10


class Trader:
    """
    Low-overfit PEPPER-only strategy:
    - Ignore ASH entirely.
    - Accumulate long exposure using cheap fills (best-ask take + penny bid).
    - Only de-risk when observed slope is persistently negative.
    """

    def run(self, state: TradingState):
        result = {}

        try:
            old = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            old = {}

        pepper = "INTARIAN_PEPPER_ROOT"
        if pepper in state.order_depths:
            od = state.order_depths[pepper]
            bids = {p: abs(v) for p, v in od.buy_orders.items()} if od.buy_orders else {}
            asks = {p: abs(v) for p, v in od.sell_orders.items()} if od.sell_orders else {}

            if bids and asks:
                mid = (max(bids) + min(asks)) / 2.0
            elif bids:
                mid = float(max(bids))
            elif asks:
                mid = float(min(asks))
            else:
                mid = None

            pos = state.position.get(pepper, 0)
            result[pepper], td = self._trade_pepper(pepper, bids, asks, mid, pos, old)
        else:
            td = old

        # Explicitly ignore ASH
        if "ASH_COATED_OSMIUM" in state.order_depths:
            result["ASH_COATED_OSMIUM"] = []

        try:
            trader_data = json.dumps(td)
        except Exception:
            trader_data = ""

        return result, 0, trader_data

    def _trade_pepper(self, sym, bids, asks, mid, position, old):
        n = old.get("p_n", 0)
        sx = old.get("p_sx", 0.0)
        sy = old.get("p_sy", 0.0)
        sxy = old.get("p_sxy", 0.0)
        sxx = old.get("p_sxx", 0.0)
        slope = old.get("p_slope", 0.1)
        neg_streak = old.get("p_neg_streak", 0)

        step = n
        n += 1

        if mid is not None:
            sx += step
            sy += mid
            sxy += step * mid
            sxx += step * step
            denom = n * sxx - sx * sx
            if n >= 2 and denom != 0:
                slope = (n * sxy - sx * sy) / denom

        if slope < NEG_SLOPE_THRESHOLD:
            neg_streak += 1
        else:
            neg_streak = 0

        td = {
            "p_n": n,
            "p_sx": sx,
            "p_sy": sy,
            "p_sxy": sxy,
            "p_sxx": sxx,
            "p_slope": slope,
            "p_neg_streak": neg_streak,
        }

        orders = []
        pos = position

        # Aggressive accumulation from top-of-book only.
        if asks and pos < POSITION_LIMIT:
            best_ask = min(asks)
            take_vol = min(asks[best_ask], TAKE_CLIP, POSITION_LIMIT - pos)
            if take_vol > 0:
                orders.append(Order(sym, int(best_ask), int(take_vol)))
                pos += take_vol

        # Passive accumulation at best_bid + 1.
        if bids and pos < POSITION_LIMIT:
            passive_vol = min(PASSIVE_CLIP, POSITION_LIMIT - pos)
            if passive_vol > 0:
                bid_px = max(bids) + 1
                orders.append(Order(sym, int(bid_px), int(passive_vol)))

        # Emergency de-risk only if trend appears persistently negative.
        if bids and pos > 0 and neg_streak >= NEG_SLOPE_STREAK:
            best_bid = max(bids)
            sell_vol = min(DE_RISK_CLIP, bids[best_bid], pos)
            if sell_vol > 0:
                orders.append(Order(sym, int(best_bid), -int(sell_vol)))

        return orders, td