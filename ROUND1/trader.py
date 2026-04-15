"""
IMC Prosperity Round 1 - Final Submission
==========================================
Self-contained Trader class for ASH_COATED_OSMIUM and INTARIAN_PEPPER_ROOT.

ASH_COATED_OSMIUM: EWMA-based market maker with aggressive taking.
  - Near-static fair value (~10000), wide spread (~16), slow mean reversion.
  - EWMA(alpha) tracks fair value. Book imbalance adjusts quotes.
  - Aggressive takes when market crosses fair by threshold.
  - Wide passive quotes capture spread. Light inventory skew.

INTARIAN_PEPPER_ROOT: Online linear trend follower.
  - Deterministic drift ~0.1/step (~1000/day), remarkably stable.
  - Online OLS estimates intercept and slope in real time.
  - Strong long bias rides the structural upward drift.
  - Passive quotes around trend-adjusted fair value.
"""
from datamodel import OrderDepth, TradingState, Order
import json
import math


# ===== PARAMETERS (optimized via 3-round coordinate descent) =====

ASH_PARAMS = {
    "ewma_alpha": 0.06,
    "take_threshold": 0.5,
    "make_width": 7,
    "inventory_skew_factor": 0.02,
    "max_passive_size": 38,
    "max_take_size": 10,
    "position_limit": 50,
    "imbalance_edge": 1.0,
}

PEPPER_PARAMS = {
    "trend_rate": 0.1002,
    "ewma_alpha_base": 0.02,
    "take_threshold": 2.0,
    "make_width": 5,
    "inventory_skew_factor": 0.0,
    "max_passive_size": 20,
    "max_take_size": 15,
    "position_limit": 50,
    "directional_skew": 1.5,
    "residual_zscore_threshold": 1.0,
    "trend_ewma_alpha": 0.001,
}

POS_LIMITS = {
    "ASH_COATED_OSMIUM": 50,
    "INTARIAN_PEPPER_ROOT": 50,
}


class Trader:

    def run(self, state: TradingState):
        result = {}
        new_td = {}

        try:
            old_td = json.loads(state.traderData) if state.traderData else {}
        except:
            old_td = {}

        # ASH_COATED_OSMIUM
        sym = "ASH_COATED_OSMIUM"
        if sym in state.order_depths:
            orders, td_update = self._trade_ash(state, sym, old_td)
            result[sym] = orders
            new_td.update(td_update)

        # INTARIAN_PEPPER_ROOT
        sym = "INTARIAN_PEPPER_ROOT"
        if sym in state.order_depths:
            orders, td_update = self._trade_pepper(state, sym, old_td)
            result[sym] = orders
            new_td.update(td_update)

        try:
            trader_data = json.dumps(new_td)
        except:
            trader_data = ""

        return result, 0, trader_data

    def _get_book(self, state, sym):
        od = state.order_depths.get(sym, OrderDepth())
        bids = {p: abs(v) for p, v in od.buy_orders.items()} if od.buy_orders else {}
        asks = {p: abs(v) for p, v in od.sell_orders.items()} if od.sell_orders else {}
        mid = None
        if bids and asks:
            mid = (max(bids.keys()) + min(asks.keys())) / 2
        elif bids:
            mid = max(bids.keys())
        elif asks:
            mid = min(asks.keys())
        return bids, asks, mid

    def _trade_ash(self, state, sym, old_td):
        p = ASH_PARAMS
        bids, asks, mid = self._get_book(state, sym)
        position = state.position.get(sym, 0)
        limit = p["position_limit"]

        # Restore EWMA fair
        ewma_fair = old_td.get("ash_ewma", None)
        if mid is not None:
            if ewma_fair is None:
                ewma_fair = mid
            else:
                ewma_fair = p["ewma_alpha"] * mid + (1 - p["ewma_alpha"]) * ewma_fair

        td_out = {"ash_ewma": ewma_fair}
        orders = []

        if ewma_fair is None:
            return orders, td_out

        # Book imbalance adjustment
        imb = 0.0
        if bids and asks:
            bb = max(bids.keys())
            ba = min(asks.keys())
            bv = bids.get(bb, 0)
            av = asks.get(ba, 0)
            total = bv + av
            if total > 0:
                imb = (bv - av) / total
        fair = ewma_fair + imb * p["imbalance_edge"]

        skew = -position * p["inventory_skew_factor"]
        pos = position

        # Aggressive taking
        if asks:
            for ap in sorted(asks.keys()):
                if ap < fair - p["take_threshold"] + skew:
                    max_buy = limit - pos
                    vol = min(asks[ap], p["max_take_size"], max_buy)
                    if vol > 0:
                        orders.append(Order(sym, int(ap), vol))
                        pos += vol

        if bids:
            for bp in sorted(bids.keys(), reverse=True):
                if bp > fair + p["take_threshold"] + skew:
                    max_sell = limit + pos
                    vol = min(bids[bp], p["max_take_size"], max_sell)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol

        # Passive making
        bid_price = math.floor(fair - p["make_width"] + skew)
        ask_price = math.ceil(fair + p["make_width"] + skew)

        if bids:
            best_bid = max(bids.keys())
            penny = int(best_bid) + 1
            if penny < fair + skew and penny > bid_price:
                bid_price = penny
        if asks:
            best_ask = min(asks.keys())
            penny = int(best_ask) - 1
            if penny > fair + skew and penny < ask_price:
                ask_price = penny

        bid_vol = min(p["max_passive_size"], limit - pos)
        ask_vol = min(p["max_passive_size"], limit + pos)

        if bid_vol > 0:
            orders.append(Order(sym, int(bid_price), int(bid_vol)))
        if ask_vol > 0:
            orders.append(Order(sym, int(ask_price), -int(ask_vol)))

        return orders, td_out

    def _trade_pepper(self, state, sym, old_td):
        p = PEPPER_PARAMS
        bids, asks, mid = self._get_book(state, sym)
        position = state.position.get(sym, 0)
        limit = p["position_limit"]

        # Restore online trend state
        n = old_td.get("pep_n", 0)
        sx = old_td.get("pep_sx", 0.0)
        sy = old_td.get("pep_sy", 0.0)
        sxy = old_td.get("pep_sxy", 0.0)
        sxx = old_td.get("pep_sxx", 0.0)
        ewma_base = old_td.get("pep_ewma_base", None)
        rate = old_td.get("pep_rate", p["trend_rate"])
        step = old_td.get("pep_step", 0)
        z_mean = old_td.get("pep_z_mean", 0.0)
        z_var = old_td.get("pep_z_var", 1.0)

        step += 1
        base = ewma_base

        if mid is not None and mid > 100:
            n += 1
            sx += step
            sy += mid
            sxy += step * mid
            sxx += step * step

            denom = n * sxx - sx * sx
            if n >= 20 and denom != 0:
                rate = (n * sxy - sx * sy) / denom
                base_ols = (sy - rate * sx) / n
            else:
                base_ols = mid if base is None else base

            current_base = mid - rate * step
            if ewma_base is None:
                ewma_base = current_base
            else:
                ewma_base = p["ewma_alpha_base"] * current_base + (1 - p["ewma_alpha_base"]) * ewma_base

        fair = (ewma_base + rate * step) if ewma_base is not None else mid

        td_out = {
            "pep_n": n, "pep_sx": sx, "pep_sy": sy,
            "pep_sxy": sxy, "pep_sxx": sxx,
            "pep_ewma_base": ewma_base, "pep_rate": rate,
            "pep_step": step, "pep_z_mean": z_mean, "pep_z_var": z_var,
        }

        orders = []
        if fair is None:
            return orders, td_out

        # Residual z-score
        if mid is not None and mid > 100:
            residual = mid - fair
            alpha_z = 0.02
            z_mean = alpha_z * residual + (1 - alpha_z) * z_mean
            diff = residual - z_mean
            z_var = alpha_z * diff * diff + (1 - alpha_z) * z_var
            std = max(z_var ** 0.5, 0.01)
            zscore = diff / std
            td_out["pep_z_mean"] = z_mean
            td_out["pep_z_var"] = z_var
        else:
            zscore = 0.0

        dir_skew = p["directional_skew"]
        adjusted_fair = fair + dir_skew

        pos = position

        # Aggressive taking
        if asks:
            for ap in sorted(asks.keys()):
                if ap < adjusted_fair - p["take_threshold"]:
                    max_buy = limit - pos
                    vol = min(asks[ap], p["max_take_size"], max_buy)
                    if vol > 0:
                        orders.append(Order(sym, int(ap), vol))
                        pos += vol

        if bids:
            for bp in sorted(bids.keys(), reverse=True):
                if bp > adjusted_fair + p["take_threshold"]:
                    max_sell = limit + pos
                    vol = min(bids[bp], p["max_take_size"], max_sell)
                    if vol > 0:
                        orders.append(Order(sym, int(bp), -vol))
                        pos -= vol

        # Passive making (skew toward long)
        inv_skew = -pos * p["inventory_skew_factor"]
        bid_price = math.floor(adjusted_fair - p["make_width"] + inv_skew)
        ask_price = math.ceil(adjusted_fair + p["make_width"] + inv_skew)

        if bids:
            best_bid = max(bids.keys())
            penny = int(best_bid) + 1
            if penny < adjusted_fair + inv_skew and penny > bid_price:
                bid_price = penny
        if asks:
            best_ask = min(asks.keys())
            penny = int(best_ask) - 1
            if penny > adjusted_fair + inv_skew and penny < ask_price:
                ask_price = penny

        bid_vol = min(p["max_passive_size"], limit - pos)
        ask_vol = min(p["max_passive_size"], limit + pos)

        if bid_vol > 0:
            orders.append(Order(sym, int(bid_price), int(bid_vol)))
        if ask_vol > 0:
            orders.append(Order(sym, int(ask_price), -int(ask_vol)))

        return orders, td_out
