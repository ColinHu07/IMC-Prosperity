"""
Round 3 Trader V9 — hybrid of 440729 (HP + VFE) and V5 (deep VEV).

Live PnL benchmarks (day 2, 100k ticks):
  V5  (current take-loop design):  $891   (min -$2962, max +$1655)
  V8  (V5 - ATM trading):          $1100  (min -$2200, max +$1100)
  440729 (passive-only + trend):   $1692  (min $0, max +$1692)  <-- never negative!

V9 keeps the wins of both:

  HP:  use 440729's PASSIVE-ONLY 3-level layered design with TREND FILTER.
       Live: $592, monotonically increasing, no drawdown.
       (V8 made $430 but had two huge -$1100 spikes.)

  VFE: use 440729's PASSIVE-ONLY design with trend filter.
       Live: $916 (vs V5's $259).  The take loop in V5 was bleeding into trends.

  Deep VEV (4000, 4500): KEEP V5 verbatim.
       V5 made $391 on these (vs 440729's $218).  Larger pos cap (150 vs 50)
       and the take loop on big mispricings work in our favor here because
       the deep-ITM market spread is huge (16-21 ticks) and we have 100%
       market share — there's no adverse selection risk.

  VEV_5000 / VEV_5100 / etc: SKIP.  ATM strikes lost net -$205 in V5 live.
       440729 also lost $35 on VEV_5000 + VEV_5100.  Not worth quoting.

  No closeout (V5/V8 worked fine without it; daily PnL is mark-to-mid).

Expected live (additive, optimistic):
  HP $592 + VFE $916 + DeepVEV $391 = ~$1900

The big structural improvement is the TREND FILTER:
  HP: stop quoting if |fast_ewma - slow_ewma| > 50 ticks (~3x touch width)
  VFE: stop quoting if |fast - slow| > 20 ticks (4x touch width)
This is what kills the -$2,200 drawdowns in V8 — the filter rules us out of
catching the falling knife when HP rolls 50+ ticks lower over a few seconds.
"""

try:
    from datamodel import OrderDepth, TradingState, Order
except ImportError:
    from prosperity4bt.datamodel import OrderDepth, TradingState, Order

import json
import math


# ─── Helpers ─────────────────────────────────────────────────────────────────

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


# ─── Position limits ─────────────────────────────────────────────────────────

POS_LIMIT = {
    "HYDROGEL_PACK": 200,
    "VELVETFRUIT_EXTRACT": 200,
    "VEV_4000": 300, "VEV_4500": 300, "VEV_5000": 300,
    "VEV_5100": 300, "VEV_5200": 300, "VEV_5300": 300,
    "VEV_5400": 300, "VEV_5500": 300, "VEV_6000": 300, "VEV_6500": 300,
}

HP_SYM  = "HYDROGEL_PACK"
VFE_SYM = "VELVETFRUIT_EXTRACT"


# ─── HP knobs (440729 verbatim — passive-only, layered, trend filter) ────────
# touch ~16 ticks; trend T=50 ≈ 3× touch
HP_SLOW_A = 0.0018          # ~550 tick horizon
HP_FAST_A = 0.04            # ~25 tick horizon
HP_TREND_T = 50.0           # stop quoting when |fast - slow| > 50
HP_MAX_POS = 100            # soft cap (50% of hard limit 200)
HP_QUOTE_EDGE = 3           # 3 ticks inside fair on a 16-tick touch
HP_INV_SKEW = 0.15
HP_LAYER_GAP = 2            # second passive level 2 ticks deeper
HP_LAYER_SPLIT = 0.6        # 60% of room on inner level
HP_LAYER_GAP_2 = 2          # third level (outer = inner - gap - gap_2)
HP_LAYER_SPLIT_3 = (0.5, 0.3)  # inner / mid; outer = 1 - sum


# ─── VFE knobs (440729 verbatim with delta hedge DISABLED) ───────────────────
# touch ~5 ticks; trend T=20 ≈ 4× touch
VX_SLOW_A = 0.006
VX_FAST_A = 0.05
VX_TREND_T = 20.0
VX_MAX_POS = 120
VX_QUOTE_EDGE = 2
VX_INV_SKEW = 0.05
# DISABLE delta hedge: V5's deep VEV runs at pos up to 150, which would
# swamp VFE quotes (150 * 0.05 = 7.5 tick shift on a 5-tick spread).
# 440729's small voucher pos (cap 50) made hedge effect mild; ours wouldn't.
VX_DELTA_HEDGE = 0.0


# ─── Deep-ITM voucher knobs (V5 verbatim — proven $391 in live) ──────────────
DEEP_STRIKES   = [4000, 4500]
DEEP_QUOTE_EDGE = 6
DEEP_TAKE_EDGE  = 9
DEEP_INV_SKEW   = 0.15
DEEP_PASSIVE    = 50
DEEP_MAX_POS    = 150

# V5 used a fast α=0.30 EWMA of VFE microprice as S for deep-VEV intrinsic.
# Using the slow VFE EWMA here causes adverse selection because intrinsic
# lags the underlying — we end up buying vouchers at stale-low prices.
VFE_SPOT_ALPHA = 0.30
VFE_ANCHOR     = 5_250.0


# ─── 440729's passive-only MM with optional layering ─────────────────────────

def _passive_mm(sym, bids, asks, pos, hard_limit, fair, trend,
                max_pos, quote_edge, inv_skew, trend_t,
                layer_gap=0, layer_split=0.6,
                layer_gap_2=0, layer_split_3=(0.5, 0.3)):
    """Emit passive bid + ask around `fair`. Never crosses the touch.

    Trend filter: if |trend| > trend_t, return [] (stay out of regime).
    Inventory skew: fair shifts against current position so we naturally flatten.
    Layering: when layer_gap > 0, emit additional passive levels deeper.
    """
    if fair is None:
        return []
    if trend is not None and abs(trend) > trend_t:
        return []

    adj_fair = fair - pos * inv_skew

    buy_room = max(0, max_pos - pos)
    sell_room = max(0, max_pos + pos)
    buy_room = min(buy_room, max(0, hard_limit - pos))
    sell_room = min(sell_room, max(0, hard_limit + pos))
    if buy_room == 0 and sell_room == 0:
        return []

    best_bid = max(bids) if bids else None
    best_ask = min(asks) if asks else None

    bid_px = int(math.floor(adj_fair - quote_edge))
    ask_px = int(math.ceil(adj_fair + quote_edge))

    # Try to improve the inside by 1 tick if still ≥ quote_edge from fair
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
        bid_outer = bid_mid - layer_gap_2
        ask_outer = ask_mid + layer_gap_2
        f_inner, f_mid = layer_split_3
        b_inner = int(buy_room * f_inner)
        b_mid   = int(buy_room * f_mid)
        b_outer = buy_room - b_inner - b_mid
        s_inner = int(sell_room * f_inner)
        s_mid   = int(sell_room * f_mid)
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

    # Two-level layering
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


# ─── V5's deep-ITM voucher MM (verbatim, with take loop) ─────────────────────

def _trade_vev_deep(sym, K, bids, asks, position, S):
    """V5's deep-ITM voucher market maker. Includes a take loop for
    big mispricings (DEEP_TAKE_EDGE = 9 on a 16-21 tick touch)."""
    orders = []
    intrinsic = max(S - K, 0.0)
    fair = intrinsic
    limit = POS_LIMIT[sym]
    soft = DEEP_MAX_POS

    pos = position
    buy_room  = max(0, min(limit - pos, soft - pos))
    sell_room = max(0, min(limit + pos, soft + pos))

    reservation = fair - pos * DEEP_INV_SKEW

    # Take loop
    if asks:
        for ap in sorted(asks):
            if float(ap) > reservation - DEEP_TAKE_EDGE or buy_room <= 0:
                break
            vol = min(asks[ap], buy_room)
            if vol > 0:
                orders.append(Order(sym, int(ap), int(vol)))
                pos += vol
                buy_room -= vol
    if bids:
        for bp in sorted(bids, reverse=True):
            if float(bp) < reservation + DEEP_TAKE_EDGE or sell_room <= 0:
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

    inv_ratio = pos / soft if soft else 0
    buy_scale  = _clamp(1.0 - 0.85 * max(0.0,  inv_ratio), 0.10, 1.5)
    sell_scale = _clamp(1.0 - 0.85 * max(0.0, -inv_ratio), 0.10, 1.5)
    bv = min(buy_room,  int(round(DEEP_PASSIVE * buy_scale)))
    sv = min(sell_room, int(round(DEEP_PASSIVE * sell_scale)))

    if bv > 0:
        orders.append(Order(sym, int(bid_q), int(bv)))
    if sv > 0:
        orders.append(Order(sym, int(ask_q), int(-sv)))
    return orders


# ─── Trader ───────────────────────────────────────────────────────────────────

class Trader:

    def run(self, state: TradingState):
        result = {}
        try:
            old = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            old = {}

        td = {"hp": {}, "vx": {}, "vfe_spot": old.get("vfe_spot", VFE_ANCHOR)}

        # ── HP EWMAs ─────────────────────────────────────────────────────────
        hp_state = dict(old.get("hp", {}))
        hp_bids = hp_asks = None
        if HP_SYM in state.order_depths:
            hp_bids, hp_asks = _book(state.order_depths[HP_SYM])
            hp_mid = _micro(hp_bids, hp_asks)
            if hp_mid is not None:
                hp_state["slow"] = _ewma(hp_state.get("slow"), hp_mid, HP_SLOW_A)
                hp_state["fast"] = _ewma(hp_state.get("fast"), hp_mid, HP_FAST_A)
        td["hp"] = hp_state

        # ── VFE EWMAs ────────────────────────────────────────────────────────
        vx_state = dict(old.get("vx", {}))
        vfe_spot = old.get("vfe_spot", VFE_ANCHOR)
        vx_bids = vx_asks = None
        if VFE_SYM in state.order_depths:
            vx_bids, vx_asks = _book(state.order_depths[VFE_SYM])
            vx_mid = _micro(vx_bids, vx_asks)
            if vx_mid is not None:
                vx_state["slow"] = _ewma(vx_state.get("slow"), vx_mid, VX_SLOW_A)
                vx_state["fast"] = _ewma(vx_state.get("fast"), vx_mid, VX_FAST_A)
                vfe_spot = VFE_SPOT_ALPHA * vx_mid + (1 - VFE_SPOT_ALPHA) * vfe_spot
        td["vx"] = vx_state
        td["vfe_spot"] = vfe_spot

        # ── HP: passive-only 3-level layered ─────────────────────────────────
        if hp_bids is not None or hp_asks is not None:
            pos = state.position.get(HP_SYM, 0)
            hard = POS_LIMIT[HP_SYM]
            if hp_state.get("slow") is not None and hp_state.get("fast") is not None:
                fair = hp_state["slow"]
                trend = hp_state["fast"] - hp_state["slow"]
                result[HP_SYM] = _passive_mm(
                    HP_SYM, hp_bids or {}, hp_asks or {}, pos, hard,
                    fair, trend, HP_MAX_POS, HP_QUOTE_EDGE,
                    HP_INV_SKEW, HP_TREND_T,
                    layer_gap=HP_LAYER_GAP, layer_split=HP_LAYER_SPLIT,
                    layer_gap_2=HP_LAYER_GAP_2, layer_split_3=HP_LAYER_SPLIT_3,
                )

        # ── VFE: passive-only with trend filter ──────────────────────────────
        # Optional delta hedge from voucher position (currently disabled — see
        # VX_DELTA_HEDGE comment above).
        if vx_bids is not None or vx_asks is not None:
            pos = state.position.get(VFE_SYM, 0)
            hard = POS_LIMIT[VFE_SYM]
            if vx_state.get("slow") is not None and vx_state.get("fast") is not None:
                fair = vx_state["slow"]
                trend = vx_state["fast"] - vx_state["slow"]
                if VX_DELTA_HEDGE:
                    voucher_delta = sum(state.position.get(f"VEV_{K}", 0)
                                        for K in DEEP_STRIKES)
                    fair = fair - VX_DELTA_HEDGE * voucher_delta * VX_INV_SKEW
                result[VFE_SYM] = _passive_mm(
                    VFE_SYM, vx_bids or {}, vx_asks or {}, pos, hard,
                    fair, trend, VX_MAX_POS, VX_QUOTE_EDGE,
                    VX_INV_SKEW, VX_TREND_T,
                )

        # ── Deep-ITM vouchers (V5 verbatim) ──────────────────────────────────
        if vfe_spot is not None:
            for K in DEEP_STRIKES:
                sym = f"VEV_{K}"
                if sym not in state.order_depths:
                    continue
                b, a = _book(state.order_depths[sym])
                pos = state.position.get(sym, 0)
                orders = _trade_vev_deep(sym, K, b, a, pos, vfe_spot)
                if orders:
                    result[sym] = orders

        # SKIP: VEV_5000/5100/5200/5300/5400/5500/6000/6500
        # ATM/OTM strikes proven loss in both V5 (-$205) and 440729 (-$35).

        return result, 0, json.dumps(td)