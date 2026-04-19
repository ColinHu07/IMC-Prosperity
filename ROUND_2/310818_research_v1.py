try:
    from datamodel import OrderDepth, TradingState, Order
except ImportError:
    from prosperity4bt.datamodel import OrderDepth, TradingState, Order

import json
import math


POSITION_LIMIT = 80
MAX_TIMESTAMP = 999_900

# Calibrated later via the research harness. Start from the known-good fallback.
MAF_BID = 1250

# ASH research parameters
ASH_GAMMA = 0.08
ASH_KAPPA = 0.35
ASH_VOL_ALPHA = 0.97
ASH_EMA_ALPHA = 0.08
ASH_PASSIVE_SIZE = 16
ASH_MIN_DELTA = 1.5
ASH_MAX_DELTA = 2.8
ASH_INV_SKEW = 0.08
ASH_IMBALANCE_WEIGHT = 0.20

# PEPPER research parameters
PEPPER_Q_LEVEL = 1.0
PEPPER_Q_DRIFT = 0.0025
PEPPER_R = 1.0
PEPPER_INIT_DRIFT = 0.10
PEPPER_HAZARD = 1.0 / 600.0
PEPPER_RESET_VARIANCE = 25.0
PEPPER_CP_TRIGGER = 0.55
PEPPER_CP_GOOD = 0.30
PEPPER_CLIP = 8


def _best_bid_ask(bids, asks):
    best_bid = max(bids) if bids else None
    best_ask = min(asks) if asks else None
    return best_bid, best_ask


def _microprice(bids, asks):
    if bids and asks:
        best_bid = max(bids)
        best_ask = min(asks)
        bid_vol = bids.get(best_bid, 0)
        ask_vol = asks.get(best_ask, 0)
        total = bid_vol + ask_vol
        if total > 0:
            return (best_ask * bid_vol + best_bid * ask_vol) / total
        return (best_bid + best_ask) / 2.0
    if bids:
        return float(max(bids))
    if asks:
        return float(min(asks))
    return None


def _imbalance(bids, asks):
    best_bid, best_ask = _best_bid_ask(bids, asks)
    if best_bid is None or best_ask is None:
        return 0.0

    bid_vol = bids.get(best_bid, 0)
    ask_vol = asks.get(best_ask, 0)
    total = bid_vol + ask_vol
    if total <= 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _gaussian_logpdf(residual, variance):
    variance = max(variance, 1e-9)
    return -0.5 * (math.log(2.0 * math.pi * variance) + (residual * residual) / variance)


class Trader:
    def bid(self):
        return MAF_BID

    def run(self, state: TradingState):
        try:
            old = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            old = {}

        ash_state = old.get("ash", {})
        pepper_state = old.get("pepper", {})

        orders = {}
        new_state = {"ash": ash_state, "pepper": pepper_state, "meta": {"timestamp": state.timestamp}}

        for sym in ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]:
            if sym not in state.order_depths:
                continue

            depth = state.order_depths[sym]
            bids = {price: abs(volume) for price, volume in depth.buy_orders.items()} if depth.buy_orders else {}
            asks = {price: abs(volume) for price, volume in depth.sell_orders.items()} if depth.sell_orders else {}
            mid = _microprice(bids, asks)
            position = state.position.get(sym, 0)

            if sym == "ASH_COATED_OSMIUM":
                sym_orders, ash_state = self._trade_ash(
                    sym=sym,
                    bids=bids,
                    asks=asks,
                    mid=mid,
                    position=position,
                    timestamp=state.timestamp,
                    old_state=ash_state,
                )
                new_state["ash"] = ash_state
            else:
                sym_orders, pepper_state = self._trade_pepper(
                    sym=sym,
                    bids=bids,
                    asks=asks,
                    mid=mid,
                    position=position,
                    old_state=pepper_state,
                )
                new_state["pepper"] = pepper_state

            orders[sym] = sym_orders

        try:
            trader_data = json.dumps(new_state)
        except Exception:
            trader_data = ""

        return orders, 0, trader_data

    def _trade_ash(self, sym, bids, asks, mid, position, timestamp, old_state):
        orders = []
        best_bid, best_ask = _best_bid_ask(bids, asks)
        micro = _microprice(bids, asks)
        imbalance = _imbalance(bids, asks)

        ema_mid = old_state.get("ema_mid")
        if ema_mid is None:
            ema_mid = mid
        elif mid is not None:
            ema_mid = ASH_EMA_ALPHA * mid + (1.0 - ASH_EMA_ALPHA) * ema_mid

        sigma2 = float(old_state.get("sigma2", 1.0))
        last_micro = old_state.get("last_micro")
        if micro is not None and last_micro is not None:
            d_micro = micro - last_micro
            sigma2 = ASH_VOL_ALPHA * sigma2 + (1.0 - ASH_VOL_ALPHA) * d_micro * d_micro
        sigma2 = _clamp(sigma2, 0.25, 16.0)

        state = {
            "ema_mid": ema_mid,
            "last_micro": micro,
            "sigma2": sigma2,
            "kappa": ASH_KAPPA,
        }

        if micro is None and ema_mid is None:
            return orders, state

        signal = micro if micro is not None else ema_mid
        if ema_mid is not None and micro is not None:
            signal = 0.7 * micro + 0.3 * ema_mid

        spread = float(best_ask - best_bid) if best_bid is not None and best_ask is not None else 6.0
        tau = max(0.1, (MAX_TIMESTAMP - timestamp) / MAX_TIMESTAMP)

        reservation = signal + ASH_IMBALANCE_WEIGHT * spread * imbalance - position * ASH_INV_SKEW
        delta = 0.5 * ASH_GAMMA * sigma2 * tau + math.log1p(ASH_GAMMA / ASH_KAPPA) / ASH_GAMMA
        delta = _clamp(delta, ASH_MIN_DELTA, ASH_MAX_DELTA)
        take_edge = max(1.0, 0.8 * delta)

        remaining_buy = POSITION_LIMIT - position
        remaining_sell = POSITION_LIMIT + position

        if asks:
            threshold = reservation - take_edge
            for ask_price in sorted(asks):
                if ask_price > threshold or remaining_buy <= 0:
                    break
                volume = min(asks[ask_price], remaining_buy)
                if volume > 0:
                    orders.append(Order(sym, int(ask_price), int(volume)))
                    remaining_buy -= volume

        if bids:
            threshold = reservation + take_edge
            for bid_price in sorted(bids, reverse=True):
                if bid_price < threshold or remaining_sell <= 0:
                    break
                volume = min(bids[bid_price], remaining_sell)
                if volume > 0:
                    orders.append(Order(sym, int(bid_price), int(-volume)))
                    remaining_sell -= volume

        bid_quote = math.floor(reservation - delta)
        ask_quote = math.ceil(reservation + delta)

        if best_bid is not None:
            bid_quote = min(bid_quote, best_bid + 1)
        if best_ask is not None:
            ask_quote = max(ask_quote, best_ask - 1)
        if bid_quote >= ask_quote:
            bid_quote = ask_quote - 1

        inv_ratio = position / POSITION_LIMIT
        bid_scale = max(0.35, 1.0 - max(0.0, inv_ratio) * 0.75)
        ask_scale = max(0.35, 1.0 + min(0.0, inv_ratio) * 0.75)
        passive_buy = min(remaining_buy, int(round(ASH_PASSIVE_SIZE * bid_scale)))
        passive_sell = min(remaining_sell, int(round(ASH_PASSIVE_SIZE * ask_scale)))

        if passive_buy > 0:
            orders.append(Order(sym, int(bid_quote), int(passive_buy)))
        if passive_sell > 0:
            orders.append(Order(sym, int(ask_quote), int(-passive_sell)))

        return orders, state

    def _trade_pepper(self, sym, bids, asks, mid, position, old_state):
        orders = []
        best_bid, best_ask = _best_bid_ask(bids, asks)
        observation = _microprice(bids, asks)
        if observation is None:
            observation = mid
        if observation is None:
            return orders, old_state

        level = float(old_state.get("level", observation))
        drift = float(old_state.get("drift", PEPPER_INIT_DRIFT))
        p00 = float(old_state.get("p00", 4.0))
        p01 = float(old_state.get("p01", 0.0))
        p11 = float(old_state.get("p11", 0.05))
        cp_prob = float(old_state.get("cp_prob", PEPPER_HAZARD))
        cp_probs = list(old_state.get("cp_probs", []))[-7:]

        level_pred = level + drift
        drift_pred = drift
        p00_pred = p00 + 2.0 * p01 + p11 + PEPPER_Q_LEVEL
        p01_pred = p01 + p11
        p11_pred = p11 + PEPPER_Q_DRIFT

        innovation = observation - level_pred
        obs_var = max(p00_pred + PEPPER_R, 1e-6)

        same_log = math.log(max(1.0 - PEPPER_HAZARD, 1e-9)) + _gaussian_logpdf(innovation, obs_var)
        new_log = math.log(max(PEPPER_HAZARD, 1e-9)) + _gaussian_logpdf(innovation, PEPPER_RESET_VARIANCE)
        max_log = max(same_log, new_log)
        same_weight = math.exp(same_log - max_log)
        new_weight = math.exp(new_log - max_log)
        posterior_new = new_weight / (same_weight + new_weight)

        if posterior_new > PEPPER_CP_TRIGGER:
            level = observation
            drift = max(0.04, min(0.15, 0.8 * max(drift_pred, 0.04)))
            p00, p01, p11 = 4.0, 0.0, 0.05
        else:
            k0 = p00_pred / obs_var
            k1 = p01_pred / obs_var
            level = level_pred + k0 * innovation
            drift = drift_pred + k1 * innovation
            p00 = (1.0 - k0) * p00_pred
            p01 = (1.0 - k0) * p01_pred
            p11 = p11_pred - k1 * p01_pred

        drift = _clamp(drift, -0.15, 0.20)
        cp_probs.append(posterior_new)
        cp_probs = cp_probs[-8:]
        cp_smoothed = sum(cp_probs[-4:]) / max(1, len(cp_probs[-4:]))

        if drift < -0.02 and cp_smoothed > 0.40:
            target = 0
        else:
            target = POSITION_LIMIT
        target = _clamp(target, 0, POSITION_LIMIT)

        fair = level + 2.0 * max(0.0, drift)

        remaining_buy = max(0, target - position)
        remaining_sell = position if target == 0 else 0

        if remaining_buy > 0 and asks:
            max_premium = 10.0 if position < POSITION_LIMIT * 0.8 else 5.0
            buy_ceiling = fair + max_premium
            for ask_price in sorted(asks):
                if ask_price > buy_ceiling or remaining_buy <= 0:
                    break
                volume = min(asks[ask_price], remaining_buy)
                if volume > 0:
                    orders.append(Order(sym, int(ask_price), int(volume)))
                    remaining_buy -= volume

        if remaining_buy > 0:
            replenishment_bid = math.floor(fair + 2.0)
            if best_bid is not None:
                replenishment_bid = max(replenishment_bid, best_bid + 1)
            orders.append(Order(sym, int(replenishment_bid), int(remaining_buy)))

        if position >= POSITION_LIMIT and target >= POSITION_LIMIT:
            if drift > 0.12 and cp_smoothed < 0.02:
                clip_size = min(PEPPER_CLIP, position)
                if clip_size > 0:
                    clip_ask = math.ceil(fair + 14.0)
                    orders.append(Order(sym, int(clip_ask), int(-clip_size)))

        if remaining_sell > 0 and bids:
            unwind_floor = fair - 2.0
            for bid_price in sorted(bids, reverse=True):
                if bid_price < unwind_floor or remaining_sell <= 0:
                    break
                volume = min(bids[bid_price], remaining_sell)
                if volume > 0:
                    orders.append(Order(sym, int(bid_price), int(-volume)))
                    remaining_sell -= volume

        if remaining_sell > 0:
            passive_ask = math.ceil(fair + 1.0)
            if best_ask is not None:
                passive_ask = max(passive_ask, best_ask - 1)
            orders.append(Order(sym, int(passive_ask), int(-remaining_sell)))

        state = {
            "level": level,
            "drift": drift,
            "p00": p00,
            "p01": p01,
            "p11": p11,
            "cp_prob": posterior_new,
            "cp_probs": cp_probs,
            "regime": "trend" if target > 0 else "flat",
        }
        return orders, state
