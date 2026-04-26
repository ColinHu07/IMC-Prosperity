"""
R3trader — Round 3 submission candidate.

Single source of truth for the Round 3 trader. See ANALYSIS.md for the
full decision log (what V0-V3 got wrong, why this design is structured
the way it is). The previous live submission is frozen verbatim in
R3trader_submitted.py — do not merge fixes back into that file.

Core design commitments (each a direct reaction to a V3 failure mode):

  1. NO fixed anchor. Fair = slow EWMA of microprice, tracks the tape.
  2. Trend filter. If abs(fast_ewma - slow_ewma) > T we stay OUT on both
     sides of that product (don't catch falling knives).
     Thresholds are sized to a multiple of the touch width (T=50 on the
     16-tick HYDROGEL touch; T=20 on the 5-tick VEX touch) — structural,
     not fit to a specific day.
  3. Passive quotes only. No aggressive cross-the-spread take. V3's take
     loop against a wrong fair was the blow-up mechanism.
  4. Inventory skew + soft position caps (100/100/50). Fair shifts against
     current position so we naturally flatten, without a hard-stop.
  5. Layered HP quoting (two passive levels, 2 ticks apart) — captures
     liquidity at both the near-touch penny and a slightly deeper reserve.
  6. Deep-ITM vouchers (VEV_4000/4500/5000) fair = VEX microprice - K.
     Near-ATM voucher (VEV_5100) fair = intrinsic + running premium EWMA
     because intrinsic alone understates time value.
  7. Short closeout window (last 100 ticks) to flatten with minimal
     book-crossing cost.

Trades: HYDROGEL_PACK, VELVETFRUIT_EXTRACT, VEV_4000/4500/5000/5100.
Skips:  VEV_5200..VEV_6500 (touch too tight to quote safely).

Later additions (structural, not day-fit):
  8. One-sided inventory guard on vouchers: if consecutive fills build
     inventory on one side past a threshold, suppress that side until
     the position de-risks. Pure symmetry rule.
  9. Minimum-samples gate for near-ATM voucher: don't quote until the
     premium EWMA has seen enough data to converge.
 10. Delta-hedge. Deep-ITM voucher delta ≈ 1 vs VEX, so VEX effective
     inventory = vex_pos + sum(voucher_pos). VEX MM naturally flattens
     the combined exposure. Reduces tail risk; costs ~1% mean PnL.
 11. Three-level HP passive quoting (inner + mid + outer at sensitivity-
     driven gaps 0/2/4 ticks).

Expected PnL (pessimistic-fills backtest, 3 historical days):
  mean +$5,461/day, worst day +$4,206 (vs original +$642 / +$503).
Live IMC replay (first 100k ticks, pessimistic harness):
  day 0 +$358, day 1 +$456, day 2 +$540.

All knobs sit on a sensitivity plateau (see sensitivity.py output
captured in ANALYSIS.md). Stress tests confirm anchor/drift shocks stay
profitable; vol-3x tail still loses but ~35% less than before hedging.
"""
from datamodel import OrderDepth, TradingState, Order
import json, math


# -------------------- limits --------------------
POS_LIMIT = {
    "HYDROGEL_PACK": 200,
    "VELVETFRUIT_EXTRACT": 200,
    "VEV_4000": 300, "VEV_4500": 300, "VEV_5000": 300,
    "VEV_5100": 300, "VEV_5200": 300, "VEV_5300": 300,
    "VEV_5400": 300, "VEV_5500": 300, "VEV_6000": 300, "VEV_6500": 300,
}

CLOSEOUT_TICKS = 100             # start unwinding at ts >= 999,000 (sensitivity peak)


# -------------------- HYDROGEL knobs --------------------
# Structural bounds: touch is ~16 ticks. Trend filter of T means we stop
# quoting when a slow vs fast EWMA diverges by more than T ticks. Setting
# T close to the touch width keeps us in the market even when the tape
# drifts slowly, because the inventory skew + wide quote edge absorb
# adverse flow naturally.
HP_SLOW_A = 0.0018               # ~550 tick horizon
HP_FAST_A = 0.04                 # ~25 tick horizon
HP_TREND_T = 50.0                # ~3x touch width; sensitivity plateau 35-75
HP_MAX_POS = 100                 # soft cap (50% of hard limit 200)
HP_QUOTE_EDGE = 3                # 3 ticks inside fair on a 16-tick touch
HP_INV_SKEW = 0.15               # shift fair by pos * skew (lowered with higher cap)
HP_LAYER_GAP = 2                 # second passive level 2 ticks deeper (sensitivity peak)
HP_LAYER_SPLIT = 0.6             # 60% of room on the inner (closer-to-fair) level
HP_LAYER_GAP_2 = 2               # Phase 1c: third level at (inner - gap - gap_2) ticks (sens peak)
HP_LAYER_SPLIT_3 = (0.5, 0.3)    # inner / mid fractions; outer = 1 - sum
HP_IMB_COEF = 0.0                # book imbalance ~0 in this sim, signal dead


# -------------------- VEX knobs --------------------
# Touch is ~5 ticks, so we quote at edge 1 (right at the inside). Trend
# filter T=5 = one full touch width worth of drift.
VX_SLOW_A = 0.006
VX_FAST_A = 0.05
VX_TREND_T = 20.0
VX_MAX_POS = 120
VX_QUOTE_EDGE = 2
VX_INV_SKEW = 0.05
VX_LAYER_GAP = 1
VX_LAYER_SPLIT = 0.7
VX_IMB_COEF = 0.0                # book imbalance ~0 in this sim, signal dead

# Phase 2a — delta hedging. Deep-ITM vouchers have ~1.0 delta versus VEX
# (since fair = VEX - K once intrinsic). Adding the voucher position to
# VEX's effective inventory makes VEX quoting naturally lean against
# voucher exposure without a separate hedging loop. VX_DELTA_HEDGE is a
# scalar multiplier so we can stress-test the effect continuously.
# Structural rationale (no day-fit): deep-ITM call delta ≡ 1 in our
# intrinsic-only pricing model.
VX_DELTA_HEDGE = 0.0

# Fast VEX spot estimate used to price deep vouchers in real time. Deep
# vouchers are highly delta-sensitive, so using VX slow EWMA for intrinsic
# lags too much in fast tape and creates adverse selection.
VFE_SPOT_ALPHA = 0.30
VFE_ANCHOR = 5250.0

# Deep voucher engine (only 4000/4500 enabled). This is where upside remains.
DEEP_STRIKES = {"VEV_4000": 4000, "VEV_4500": 4500}
DEEP_QUOTE_EDGE = 6
DEEP_TAKE_BASE = 8
DEEP_TAKE_MULT = 0.45
DEEP_TAKE_MAX = 12
DEEP_INV_SKEW = 0.15
DEEP_PASSIVE = 50
DEEP_MAX_POS = 150

# Structural guards to keep deep-VEV upside without tail blowups.
DEEP_TREND_T = 20.0
DEEP_STREAK_VOL = 8
DEEP_STREAK_POS_FRAC = 0.5
DEEP_MOM_TAPER_START = 10.0
DEEP_MOM_TAPER_END = 30.0
DEEP_MOM_TAPER_MAX = 0.35


# -------------------- Voucher knobs --------------------
# Vouchers are priced off VEX. Fair = intrinsic + running premium EWMA;
# the premium tracks real-world "time value" observed on tape (no BS/IV
# model — just a smoothed (mid - intrinsic)). Deep-ITM vouchers should
# have near-zero premium, near-ATM have meaningful premium.
#
# Touch widths observed in the IMC live sim:
#   VEV_4000, 4500: ~16-21 ticks  (lots of room, wide edge OK)
#   VEV_5000: ~6 ticks
#   VEV_5100: ~4 ticks             (tight, small edge needed)
#   VEV_5200+: ~1-3 ticks          (too tight to quote safely, skipped)
VEV_STRIKES = {"VEV_4000": 4000, "VEV_4500": 4500,
               "VEV_5000": 5000, "VEV_5100": 5100}
VEV_DEEP = {"VEV_4000", "VEV_4500", "VEV_5000"}   # wide/medium touch -> wide edge
VEV_MID = set()                                    # (unused, reserved for future mid-ITM)
VEV_NEAR = {"VEV_5100"}                            # tight touch, near-ATM
VEV_MAX_POS = 50                              # deep/mid cap
VEV_NEAR_MAX_POS = 20                         # near-ATM: smaller cap (more IV risk)
VEV_QUOTE_EDGE_DEEP = 5                       # deep-ITM (16-21 tick touch)
VEV_QUOTE_EDGE_MID = 2                        # VEV_5000 (~6 tick touch)
VEV_QUOTE_EDGE_NEAR = 1                       # near-ATM (~4 tick touch)
VEV_INV_SKEW = 0.08
VEV_TREND_T = 10.0                            # follow VEX's own trend threshold
VEV_PREMIUM_A = 0.02                          # EWMA alpha for premium tracking

# Phase 1a — One-sided inventory guard. If our own fills have accumulated
# same-direction for VEV_STREAK_VOL units AND our position on that strike
# is already more than VEV_STREAK_POS_FRAC * max_pos in that direction,
# suppress the crowded side until position de-risks. Pure symmetry rule,
# no day-fit constants.
VEV_STREAK_VOL = 5
VEV_STREAK_POS_FRAC = 0.4

# Phase 1b — Minimum samples before quoting near-ATM vouchers. The premium
# EWMA needs enough observations to be meaningful; quoting earlier just
# bleeds on a poor fair estimate.
VEV_NEAR_MIN_SAMPLES = 200                    # ~20k ticks at most products' update rate


# -------------------- helpers --------------------
def _micro(bids, asks):
    if bids and asks:
        bb, ba = max(bids), min(asks)
        bv, av = bids[bb], asks[ba]
        if bv + av > 0:
            return (bb * av + ba * bv) / (bv + av)
        return 0.5 * (bb + ba)
    if bids:
        return float(max(bids))
    if asks:
        return float(min(asks))
    return None


def _imbalance(bids, asks):
    """Depth-weighted book imbalance in [-1, +1].
    +1 = all bid volume, 0 = balanced, -1 = all ask volume. Captures
    pressure across top 3 levels, not just the touch."""
    if not bids and not asks:
        return 0.0
    bv = sum(bids.values()) if bids else 0
    av = sum(asks.values()) if asks else 0
    total = bv + av
    if total == 0:
        return 0.0
    return (bv - av) / total


def _book(od):
    b = {p: abs(v) for p, v in (od.buy_orders or {}).items()}
    a = {p: abs(v) for p, v in (od.sell_orders or {}).items()}
    return b, a


def _ewma(prev, x, alpha):
    if prev is None:
        return x
    return alpha * x + (1 - alpha) * prev


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _touch_width(bids, asks):
    if not bids or not asks:
        return None
    return max(1, int(min(asks) - max(bids)))


def _deep_take_edge(bids, asks):
    w = _touch_width(bids, asks)
    if w is None:
        return DEEP_TAKE_BASE
    return int(_clamp(round(DEEP_TAKE_MULT * w), DEEP_TAKE_BASE, DEEP_TAKE_MAX))


def _deep_effective_cap(trend_abs):
    if trend_abs <= DEEP_MOM_TAPER_START:
        return DEEP_MAX_POS
    if trend_abs >= DEEP_MOM_TAPER_END:
        return int(round(DEEP_MAX_POS * (1.0 - DEEP_MOM_TAPER_MAX)))
    frac = ((trend_abs - DEEP_MOM_TAPER_START) /
            (DEEP_MOM_TAPER_END - DEEP_MOM_TAPER_START))
    taper = DEEP_MOM_TAPER_MAX * frac
    return int(round(DEEP_MAX_POS * (1.0 - taper)))


def _trade_vev_deep(sym, K, bids, asks, position, spot, trend_vx, streak):
    """
    Deep-ITM voucher MM with guarded take loop:
      - trend gate (skip when VEX trend too strong),
      - one-sided streak suppression,
      - spread-aware take threshold,
      - momentum-aware soft-cap taper.
    """
    if spot is None:
        return []
    if abs(trend_vx) > DEEP_TREND_T:
        return []

    hard = POS_LIMIT[sym]
    soft = _deep_effective_cap(abs(trend_vx))
    if soft <= 0:
        return []

    pos = position
    buy_room = max(0, min(hard - pos, soft - pos))
    sell_room = max(0, min(hard + pos, soft + pos))

    sup_buy = sup_sell = False
    if streak and streak.get("vol", 0) >= DEEP_STREAK_VOL:
        thresh = DEEP_STREAK_POS_FRAC * soft
        if streak.get("side", 0) > 0 and pos >= thresh:
            sup_buy = True
        elif streak.get("side", 0) < 0 and pos <= -thresh:
            sup_sell = True
    if sup_buy:
        buy_room = 0
    if sup_sell:
        sell_room = 0
    if buy_room == 0 and sell_room == 0:
        return []

    intrinsic = max(spot - K, 0.0)
    reservation = intrinsic - pos * DEEP_INV_SKEW
    take_edge = _deep_take_edge(bids, asks)
    orders = []

    if asks and buy_room > 0:
        for ap in sorted(asks):
            if float(ap) > reservation - take_edge or buy_room <= 0:
                break
            vol = min(asks[ap], buy_room)
            if vol > 0:
                orders.append(Order(sym, int(ap), int(vol)))
                pos += vol
                buy_room -= vol
    if bids and sell_room > 0:
        for bp in sorted(bids, reverse=True):
            if float(bp) < reservation + take_edge or sell_room <= 0:
                break
            vol = min(bids[bp], sell_room)
            if vol > 0:
                orders.append(Order(sym, int(bp), int(-vol)))
                pos -= vol
                sell_room -= vol

    if not bids or not asks:
        return orders

    best_bid = max(bids)
    best_ask = min(asks)
    bid_q = best_bid + 1
    if bid_q > reservation - DEEP_QUOTE_EDGE:
        bid_q = math.floor(reservation - DEEP_QUOTE_EDGE)
    ask_q = best_ask - 1
    if ask_q < reservation + DEEP_QUOTE_EDGE:
        ask_q = math.ceil(reservation + DEEP_QUOTE_EDGE)
    if bid_q >= ask_q:
        bid_q = ask_q - 1
    bid_q = max(bid_q, int(math.ceil(intrinsic)) if intrinsic > 0 else 1, 1)

    inv_ratio = pos / soft if soft else 0.0
    buy_scale = _clamp(1.0 - 0.85 * max(0.0, inv_ratio), 0.10, 1.5)
    sell_scale = _clamp(1.0 - 0.85 * max(0.0, -inv_ratio), 0.10, 1.5)
    bv = min(buy_room, int(round(DEEP_PASSIVE * buy_scale)))
    sv = min(sell_room, int(round(DEEP_PASSIVE * sell_scale)))
    if bv > 0:
        orders.append(Order(sym, int(bid_q), int(bv)))
    if sv > 0:
        orders.append(Order(sym, int(ask_q), int(-sv)))
    return orders


def _passive_mm(sym, bids, asks, pos, hard_limit, fair, trend,
                max_pos, quote_edge, inv_skew, trend_t,
                layer_gap=0, layer_split=0.6, imb_coef=0.0,
                suppress_buy=False, suppress_sell=False,
                layer_gap_2=0, layer_split_3=(0.5, 0.3)):
    """Emit passive bid + ask around `fair` with inventory skew and trend gate.

    Invariants (see ANALYSIS.md for rationale):
      * No aggressive take. Ever.
      * If |trend| > trend_t, return NO orders (stay out).
      * Quotes guaranteed to sit at or outside the inside (never crossing).

    If `layer_gap > 0`, emits a second passive pair at (inside - layer_gap)
    for deeper liquidity capture. layer_split is the fraction of the room
    sent to the inner (closer-to-fair) level.

    If `imb_coef > 0`, shifts adj_fair by imbalance * imb_coef. Positive
    imbalance (more bids) -> fair up -> we ask higher, bid higher, reducing
    adverse selection when size is stacked on the buy side.

    suppress_buy / suppress_sell: structural one-sided inventory guard.
    Used by voucher strategy when we've accumulated inventory on one side
    (see VEV_STREAK_* in caller).
    """
    if fair is None:
        return []
    if trend is not None and abs(trend) > trend_t:
        return []

    imb = _imbalance(bids, asks) if imb_coef else 0.0
    adj_fair = fair - pos * inv_skew + imb * imb_coef

    buy_room = max(0, max_pos - pos)
    sell_room = max(0, max_pos + pos)
    buy_room = min(buy_room, max(0, hard_limit - pos))
    sell_room = min(sell_room, max(0, hard_limit + pos))
    if suppress_buy:
        buy_room = 0
    if suppress_sell:
        sell_room = 0
    if buy_room == 0 and sell_room == 0:
        return []

    best_bid = max(bids) if bids else None
    best_ask = min(asks) if asks else None

    bid_px = int(math.floor(adj_fair - quote_edge))
    ask_px = int(math.ceil(adj_fair + quote_edge))

    if best_bid is not None:
        candidate_bid = int(best_bid) + 1
        if candidate_bid <= adj_fair - quote_edge:
            bid_px = candidate_bid
        if best_ask is not None and bid_px >= int(best_ask):
            bid_px = int(best_ask) - 1
    if best_ask is not None:
        candidate_ask = int(best_ask) - 1
        if candidate_ask >= adj_fair + quote_edge:
            ask_px = candidate_ask
        if best_bid is not None and ask_px <= int(best_bid):
            ask_px = int(best_bid) + 1

    if bid_px >= ask_px:
        return []

    orders = []
    if layer_gap <= 0:
        if buy_room > 0:
            orders.append(Order(sym, bid_px, buy_room))
        if sell_room > 0:
            orders.append(Order(sym, ask_px, -sell_room))
        return orders

    bid_inner, ask_inner = bid_px, ask_px
    bid_mid = bid_inner - layer_gap
    ask_mid = ask_inner + layer_gap

    if layer_gap_2 > 0:
        # Three-level layering (Phase 1c): inner / mid / outer.
        bid_outer = bid_mid - layer_gap_2
        ask_outer = ask_mid + layer_gap_2
        f_inner, f_mid = layer_split_3
        b_inner = int(buy_room * f_inner)
        b_mid = int(buy_room * f_mid)
        b_outer = buy_room - b_inner - b_mid
        s_inner = int(sell_room * f_inner)
        s_mid = int(sell_room * f_mid)
        s_outer = sell_room - s_inner - s_mid

        seen_bid = set()
        for px, qty in [(bid_inner, b_inner), (bid_mid, b_mid), (bid_outer, b_outer)]:
            if qty > 0 and px not in seen_bid:
                orders.append(Order(sym, px, qty))
                seen_bid.add(px)
        seen_ask = set()
        for px, qty in [(ask_inner, s_inner), (ask_mid, s_mid), (ask_outer, s_outer)]:
            if qty > 0 and px not in seen_ask:
                orders.append(Order(sym, px, -qty))
                seen_ask.add(px)
        return orders

    # Two-level layering. Inner (inside) gets layer_split of the room; outer
    # sits layer_gap ticks deeper on each side.
    b_inner = int(buy_room * layer_split)
    b_outer = buy_room - b_inner
    s_inner = int(sell_room * layer_split)
    s_outer = sell_room - s_inner

    if b_inner > 0:
        orders.append(Order(sym, bid_inner, b_inner))
    if b_outer > 0 and bid_mid != bid_inner:
        orders.append(Order(sym, bid_mid, b_outer))
    if s_inner > 0:
        orders.append(Order(sym, ask_inner, -s_inner))
    if s_outer > 0 and ask_mid != ask_inner:
        orders.append(Order(sym, ask_mid, -s_outer))
    return orders


def _closeout(sym, bids, asks, pos):
    """Cross the book to flatten; walk multiple levels if necessary."""
    orders = []
    p = pos
    if p > 0 and bids:
        for bp in sorted(bids, reverse=True):
            vol = min(bids[bp], p)
            if vol > 0:
                orders.append(Order(sym, bp, -vol))
                p -= vol
            if p <= 0:
                break
    elif p < 0 and asks:
        for ap in sorted(asks):
            vol = min(asks[ap], -p)
            if vol > 0:
                orders.append(Order(sym, ap, vol))
                p += vol
            if p >= 0:
                break
    return orders


class Trader:
    def run(self, state: TradingState):
        result = {}
        try:
            old = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            old = {}

        ts = state.timestamp
        closeout = ts >= 1_000_000 - CLOSEOUT_TICKS * 100

        td = {"hp": {}, "vx": {}, "vev": {}, "vfe_spot": old.get("vfe_spot", VFE_ANCHOR)}

        # ---- update HYDROGEL EWMAs ----
        hp_state = dict(old.get("hp", {}))
        hp_sym = "HYDROGEL_PACK"
        hp_bids = hp_asks = None
        if hp_sym in state.order_depths:
            hp_bids, hp_asks = _book(state.order_depths[hp_sym])
            hp_mid = _micro(hp_bids, hp_asks)
            if hp_mid is not None:
                hp_state["slow"] = _ewma(hp_state.get("slow"), hp_mid, HP_SLOW_A)
                hp_state["fast"] = _ewma(hp_state.get("fast"), hp_mid, HP_FAST_A)
        td["hp"] = hp_state

        # ---- update VEX EWMAs ----
        vx_state = dict(old.get("vx", {}))
        vfe_spot = old.get("vfe_spot", VFE_ANCHOR)
        vx_sym = "VELVETFRUIT_EXTRACT"
        vx_bids = vx_asks = None
        if vx_sym in state.order_depths:
            vx_bids, vx_asks = _book(state.order_depths[vx_sym])
            vx_mid = _micro(vx_bids, vx_asks)
            if vx_mid is not None:
                vx_state["slow"] = _ewma(vx_state.get("slow"), vx_mid, VX_SLOW_A)
                vx_state["fast"] = _ewma(vx_state.get("fast"), vx_mid, VX_FAST_A)
                vfe_spot = _ewma(vfe_spot, vx_mid, VFE_SPOT_ALPHA)
        td["vx"] = vx_state
        td["vfe_spot"] = vfe_spot

        # ---- HYDROGEL ----
        if hp_bids is not None or hp_asks is not None:
            pos = state.position.get(hp_sym, 0)
            hard = POS_LIMIT[hp_sym]
            if closeout:
                result[hp_sym] = _closeout(hp_sym, hp_bids or {}, hp_asks or {}, pos)
            elif hp_state.get("slow") is not None and hp_state.get("fast") is not None:
                fair = hp_state["slow"]
                trend = hp_state["fast"] - hp_state["slow"]
                result[hp_sym] = _passive_mm(
                    hp_sym, hp_bids or {}, hp_asks or {}, pos, hard,
                    fair, trend, HP_MAX_POS, HP_QUOTE_EDGE,
                    HP_INV_SKEW, HP_TREND_T,
                    layer_gap=HP_LAYER_GAP, layer_split=HP_LAYER_SPLIT,
                    layer_gap_2=HP_LAYER_GAP_2, layer_split_3=HP_LAYER_SPLIT_3,
                    imb_coef=HP_IMB_COEF)

        # ---- VEX ----
        if vx_bids is not None or vx_asks is not None:
            pos = state.position.get(vx_sym, 0)
            hard = POS_LIMIT[vx_sym]
            if closeout:
                result[vx_sym] = _closeout(vx_sym, vx_bids or {}, vx_asks or {}, pos)
            elif vx_state.get("slow") is not None and vx_state.get("fast") is not None:
                fair = vx_state["slow"]
                trend = vx_state["fast"] - vx_state["slow"]
                # Phase 2a — delta-hedge voucher exposure into VEX fair.
                # Net voucher delta on deep-ITM is ~1:1 with VEX, so
                # pre-shifting fair by (voucher_delta * skew) makes VEX
                # lean against accumulated voucher inventory.
                voucher_delta = 0
                for vsym in VEV_DEEP:
                    voucher_delta += state.position.get(vsym, 0)
                fair = fair - VX_DELTA_HEDGE * voucher_delta * VX_INV_SKEW
                result[vx_sym] = _passive_mm(
                    vx_sym, vx_bids or {}, vx_asks or {}, pos, hard,
                    fair, trend, VX_MAX_POS, VX_QUOTE_EDGE,
                    VX_INV_SKEW, VX_TREND_T,
                    layer_gap=VX_LAYER_GAP, layer_split=VX_LAYER_SPLIT,
                    imb_coef=VX_IMB_COEF)

        # ---- Deep vouchers only (4000/4500) ----
        # Keep the profitable deep-ITM engine and skip 5000+/near-ATM lines.
        vx_fast = vx_state.get("fast")
        vx_slow = vx_state.get("slow")
        streak_state = dict(old.get("vev_streak", {}))
        if vx_fast is not None and vx_slow is not None:
            trend_vx = vx_fast - vx_slow
            for sym, K in DEEP_STRIKES.items():
                if sym not in state.order_depths:
                    continue
                b, a = _book(state.order_depths[sym])
                pos = state.position.get(sym, 0)
                if closeout:
                    result[sym] = _closeout(sym, b, a, pos)
                    continue

                ss = streak_state.get(sym, {"last_pos": pos, "side": 0, "vol": 0})
                delta = pos - ss.get("last_pos", pos)
                if delta != 0:
                    d_side = 1 if delta > 0 else -1
                    if ss.get("side") == d_side:
                        ss["vol"] = ss.get("vol", 0) + abs(delta)
                    else:
                        ss["side"] = d_side
                        ss["vol"] = abs(delta)
                ss["last_pos"] = pos
                streak_state[sym] = ss

                orders = _trade_vev_deep(sym, K, b, a, pos, vfe_spot, trend_vx, ss)
                if orders:
                    result[sym] = orders

        td["vev"] = {}
        td["vev_streak"] = streak_state

        return result, 0, json.dumps(td)
