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
  9. Minimum-samples gate for premium-EWMA vouchers: don't quote until
     the EWMA has 1/alpha = 50 samples — the filter's own time constant.
 10. Delta-hedge. Deep-ITM voucher delta ≈ 1 vs VEX, so VEX effective
     inventory = vex_pos + sum(voucher_pos). VEX MM naturally flattens
     the combined exposure. Reduces tail risk; costs ~1% mean PnL.
 11. Three-level HP passive quoting (inner + mid + outer at sensitivity-
     driven gaps 0/2/4 ticks).
 12. Two-level VEX passive quoting (gap=1, split=0.7). Same rationale as
     HP layering: catches sweep flow past the inner quote.
 13. Correct VEV_5000 premium tier. Strike is only $260 ITM on observed
     VEX — observed avg premium $3.22. Pure intrinsic mis-prices bids
     below market (0 buys, 3 sells per 100k in live). Now uses
     intrinsic + EWMA(mid - intrinsic) like VEV_5100.
 14. Depth-weighted microprice (top 3 levels). Reduces to touch micro
     when book is symmetric — live-only signal for asymmetric depth.
 15. Book imbalance coefficient (HP=1.0, VEX=0.5). Conservative live-
     only nudge; sim has zero imbalance so zero effect in backtest.

Expected PnL (pessimistic-fills backtest, 3 historical days):
  mean +$6,140/day, worst day +$4,835 (vs original +$642 / +$503).
Live IMC replay (first 100k ticks, pessimistic harness):
  day 0 +$408, day 1 +$492, day 2 +$706.

Stress tests (optimistic fills baseline mean $27,049):
  anchor +200 min +$4,399, anchor -200 min +$4,194,
  vol-3x min -$13,196, drift min +$199,149.

All knobs sit on sensitivity plateaus. VEV_5200 was tested and rejected:
near-ATM premium too noisy — bled $1,861 on day 2 optimistic. Bounded
take on mispricing was tested and rejected: day-0 live swing of -$1,450.
Both failures preserved in the design log to prevent re-adding.
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
# Book imbalance coefficient: conservative since sim produces ~0 imbalance
# (symmetric bot quotes). Setting >0 is a bet on live IMC having asymmetric
# flow. Value picked structurally: fair shift at max |imb|=1 equals
# 1 tick on HP (touch=16, so 1/16 of touch).
HP_IMB_COEF = 1.0


# -------------------- VEX knobs --------------------
# Touch is ~5 ticks, so we quote at edge 1 (right at the inside). Trend
# filter T=5 = one full touch width worth of drift.
VX_SLOW_A = 0.006
VX_FAST_A = 0.05
VX_TREND_T = 20.0
VX_MAX_POS = 120
VX_QUOTE_EDGE = 2
VX_INV_SKEW = 0.05
# Two-level VEX quoting: inner at inside (edge 2) and outer 1 tick deeper.
# Structural rationale: VEX has 5-tick touch with ~38 avg depth per side.
# A 1-tick gap catches sweep flow that walks past our inner quote, same
# pattern that works for HP. Very conservative split to keep the majority
# of size at the touch.
VX_LAYER_GAP = 1
VX_LAYER_SPLIT = 0.7
# Same rationale as HP_IMB_COEF — conservative live-only signal.
VX_IMB_COEF = 0.5

# Phase 2a — delta hedging. Deep-ITM vouchers have ~1.0 delta versus VEX
# (since fair = VEX - K once intrinsic). Adding the voucher position to
# VEX's effective inventory makes VEX quoting naturally lean against
# voucher exposure without a separate hedging loop. VX_DELTA_HEDGE is a
# scalar multiplier so we can stress-test the effect continuously.
# Structural rationale (no day-fit): deep-ITM call delta ≡ 1 in our
# intrinsic-only pricing model.
VX_DELTA_HEDGE = 1.0


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
# Tier assignment: premium becomes meaningful as strike approaches underlying.
# VEV_4000/4500 are ≥$700 ITM: observed premium ≈ $0.01, pure intrinsic works.
# VEV_5000 is ~$260 ITM: observed premium ≈ $3.22 — must use EWMA.
# VEV_5100 is ~$160 ITM: observed premium ≈ $12.40 — EWMA.
# VEV_5200 NOT traded — on day 2 (calmest tape) both sides got hit at our
# quote, bleeding $1,861 in optimistic. The EWMA premium is too noisy for
# the 3-tick touch; the theoretical delta gamma risk swamps the spread.
VEV_DEEP = {"VEV_4000", "VEV_4500"}
VEV_MID = {"VEV_5000"}                             # premium-EWMA tier, 6-tick touch
VEV_NEAR = {"VEV_5100"}                            # premium-EWMA tier, 4-tick touch
VEV_MAX_POS = 50                              # deep cap
VEV_MID_MAX_POS = 30                          # mid: 6-tick touch, moderate cap
VEV_NEAR_MAX_POS = 20                         # near-ATM: smaller cap (more IV risk)
VEV_QUOTE_EDGE_DEEP = 5                       # deep-ITM (16-21 tick touch)
VEV_QUOTE_EDGE_MID = 2                        # VEV_5000 (~6-tick touch → edge 2 sits inside)
VEV_QUOTE_EDGE_NEAR = 1                       # near-ATM (~3-4 tick touch → edge 1 sits inside)
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

# Phase 1b — Minimum samples before quoting premium-EWMA vouchers. Set to
# the EWMA time-constant (1/alpha = 50) — that's when the filter is
# structurally "converged" in the standard sense. Higher values just cost
# cold-start expected value without adding statistical safety.
VEV_NEAR_MIN_SAMPLES = 50


# -------------------- helpers --------------------
def _micro(bids, asks, depth=3):
    """Depth-weighted microprice across top `depth` levels per side.
    If the book is symmetric (sim), this reduces to the touch microprice.
    If live books publish asymmetric sizes across levels, depth weighting
    pulls fair toward the heavier side — mild leading signal.
    """
    if bids and asks:
        bb_levels = sorted(bids, reverse=True)[:depth]
        ba_levels = sorted(asks)[:depth]
        bv = sum(bids[p] for p in bb_levels)
        av = sum(asks[p] for p in ba_levels)
        if bv + av > 0:
            # Weighted price of each side, then microprice-style combination.
            avg_bid = sum(p * bids[p] for p in bb_levels) / bv if bv else bb_levels[0]
            avg_ask = sum(p * asks[p] for p in ba_levels) / av if av else ba_levels[0]
            return (avg_bid * av + avg_ask * bv) / (bv + av)
        return 0.5 * (bb_levels[0] + ba_levels[0])
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

        td = {"hp": {}, "vx": {}, "vev": {}}

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
        vx_sym = "VELVETFRUIT_EXTRACT"
        vx_bids = vx_asks = None
        if vx_sym in state.order_depths:
            vx_bids, vx_asks = _book(state.order_depths[vx_sym])
            vx_mid = _micro(vx_bids, vx_asks)
            if vx_mid is not None:
                vx_state["slow"] = _ewma(vx_state.get("slow"), vx_mid, VX_SLOW_A)
                vx_state["fast"] = _ewma(vx_state.get("fast"), vx_mid, VX_FAST_A)
        td["vx"] = vx_state

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

        # ---- Vouchers (deep-ITM + near-ATM) ----
        # Deep/mid-ITM: fair = intrinsic (VEX_fast - K). The deep-ITM
        # premium is small and adding a premium EWMA noticeably hurts
        # quote placement, so keep the cleaner intrinsic-only fair.
        #
        # Near-ATM (VEV_5100): intrinsic alone is wrong because the
        # option has meaningful time value. Use a slow EWMA of
        # (mid - intrinsic) as the premium estimate.
        #
        # Phase 1a: track a per-strike streak based on position deltas
        # (backtest-harness-safe — doesn't require state.own_trades). If
        # our fills have piled up on one side AND we're already holding
        # sizable inventory in that direction, suppress that side until
        # the position comes back toward flat.
        vx_fast = vx_state.get("fast")
        vx_slow = vx_state.get("slow")
        vev_state = dict(old.get("vev", {}))
        streak_state = dict(old.get("vev_streak", {}))
        if vx_fast is not None and vx_slow is not None:
            trend_vx = vx_fast - vx_slow
            for sym, K in VEV_STRIKES.items():
                if sym not in state.order_depths:
                    continue
                b, a = _book(state.order_depths[sym])
                pos = state.position.get(sym, 0)
                hard = POS_LIMIT[sym]
                if closeout:
                    result[sym] = _closeout(sym, b, a, pos)
                    continue

                # Phase 1a: update streak from our own position delta.
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

                intrinsic = max(0.0, vx_fast - K)

                if sym in VEV_DEEP:
                    # Observed premium ≈ 0 at deep ITM — intrinsic is exact.
                    fair = intrinsic
                    edge = VEV_QUOTE_EDGE_DEEP
                    max_pos = VEV_MAX_POS
                else:
                    # MID or NEAR — premium is non-trivial, use EWMA.
                    mid = _micro(b, a)
                    prev_prem = vev_state.get(sym)
                    if mid is not None:
                        prem_obs = mid - intrinsic
                        vev_state[sym] = _ewma(prev_prem, prem_obs, VEV_PREMIUM_A)
                    nsym = sym + ":n"
                    vev_state[nsym] = vev_state.get(nsym, 0) + (1 if mid is not None else 0)
                    if vev_state.get(nsym, 0) < VEV_NEAR_MIN_SAMPLES:
                        continue
                    prem = vev_state.get(sym)
                    if prem is None:
                        continue
                    fair = max(0.0, intrinsic + prem)
                    if sym in VEV_MID:
                        edge = VEV_QUOTE_EDGE_MID
                        max_pos = VEV_MID_MAX_POS
                    else:  # VEV_NEAR
                        edge = VEV_QUOTE_EDGE_NEAR
                        max_pos = VEV_NEAR_MAX_POS

                # Phase 1a: apply one-sided suppression. If streak shows
                # accumulating long (buys), and we're already long >=
                # threshold, don't send another bid. Symmetric for sells.
                sup_buy = sup_sell = False
                if ss["vol"] >= VEV_STREAK_VOL:
                    thresh = VEV_STREAK_POS_FRAC * max_pos
                    if ss["side"] > 0 and pos >= thresh:
                        sup_buy = True
                    elif ss["side"] < 0 and pos <= -thresh:
                        sup_sell = True

                result[sym] = _passive_mm(
                    sym, b, a, pos, hard, fair, trend_vx,
                    max_pos, edge, VEV_INV_SKEW, VEV_TREND_T,
                    suppress_buy=sup_buy, suppress_sell=sup_sell)
        td["vev"] = vev_state
        td["vev_streak"] = streak_state

        return result, 0, json.dumps(td)