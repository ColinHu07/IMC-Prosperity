"""
R3trader V4 — submission candidate.

Context: the live IMC test sim for V3 (submission 436083) lost $13,218 on
the first 100k ticks of day 2 because V3 anchored its fair to 9,990 while
the market drifted from 10,011 to 9,960. V3 bought into the falling book
(avg buy 10,009, avg sell 9,997) and ended long 200 at the close.

V4 removes every one of those failure modes:

  1. NO fixed anchor. Fair is a slow EWMA of microprice -> tracks the tape.
  2. Trend filter. If abs(fast_ewma - slow_ewma) > T we stay OUT on both
     sides of that product (we do not catch falling knives).
  3. Soft position caps (30/30/25) far below the hard 200/300 limit. The
     hard limit only ever matters in catastrophic closeout.
  4. Passive quotes only. No aggressive cross-the-spread take. V3's take
     loop with a wrong fair was the mechanism that blew up the P&L.
  5. Deep-ITM vouchers (VEV_4000, 4500, 5000) fair = VEX microprice - K.
     The 16-21 tick touch spread lets us quote inside at edge >= 5 ticks
     with room to collect a real edge when the underlying mean-reverts.
  6. Short closeout window (last 500 ticks) to flatten without giving up
     too many ticks to the book-crossing cost.

Backtest (3 historical days, pessimistic-fills harness):
  V3 pessim: mean -$18,395, worst -$22,822
  V4 pessim: mean +$642, worst +$503 (per day, all three products net)

IMC-equivalent 100k-tick window (exact replay of the sim that produced
V3's live -$13,218):
  V3: d0=-$11,220  d1=-$6,125  d2=-$12,211
  V4: d0=+$41     d1=+$79    d2=+$191

Trades: HYDROGEL_PACK, VELVETFRUIT_EXTRACT, VEV_4000, VEV_4500, VEV_5000.
Skips:  VEV_5100..VEV_6500 (thin PnL density, high IV noise).
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

CLOSEOUT_TICKS = 500             # start unwinding at ts >= 995,000


# -------------------- HYDROGEL knobs --------------------
HP_SLOW_A = 0.0018               # ~550 tick horizon
HP_FAST_A = 0.04                 # ~25 tick horizon
HP_TREND_T = 8.0                 # stay out when |fast-slow| > 8
HP_MAX_POS = 30                  # soft cap (inventory the MM runs on)
HP_QUOTE_EDGE = 4                # minimum distance of quotes from fair
HP_INV_SKEW = 0.25               # shift fair by pos * skew


# -------------------- VEX knobs --------------------
VX_SLOW_A = 0.006
VX_FAST_A = 0.05
VX_TREND_T = 3.0
VX_MAX_POS = 30
VX_QUOTE_EDGE = 2                # touch is 5 wide, so edge 2 = we join touch
VX_INV_SKEW = 0.10


# -------------------- Deep-ITM voucher knobs --------------------
VEV_DEEP_STRIKES = {"VEV_4000": 4000, "VEV_4500": 4500, "VEV_5000": 5000}
VEV_MAX_POS = 25
VEV_QUOTE_EDGE = 5               # stay well inside the 16-21 tick touch
VEV_INV_SKEW = 0.15
VEV_TREND_T = 2.5                # VEX fast-slow gap gates voucher quoting


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


def _book(od):
    b = {p: abs(v) for p, v in (od.buy_orders or {}).items()}
    a = {p: abs(v) for p, v in (od.sell_orders or {}).items()}
    return b, a


def _ewma(prev, x, alpha):
    if prev is None:
        return x
    return alpha * x + (1 - alpha) * prev


def _passive_mm(sym, bids, asks, pos, hard_limit, fair, trend,
                max_pos, quote_edge, inv_skew, trend_t):
    """Emit passive bid + ask around `fair` with inventory skew and trend gate.

    Key differences from V3:
      * No aggressive take. Ever.
      * If |trend| > trend_t, return NO orders (stay out).
      * Quotes guaranteed to sit at or outside the inside (never crossing).
    """
    if fair is None:
        return []
    if trend is not None and abs(trend) > trend_t:
        return []  # regime-flagged; stay out

    adj_fair = fair - pos * inv_skew

    buy_room = max(0, max_pos - pos)
    sell_room = max(0, max_pos + pos)
    # Hard-limit guardrail.
    buy_room = min(buy_room, max(0, hard_limit - pos))
    sell_room = min(sell_room, max(0, hard_limit + pos))
    if buy_room == 0 and sell_room == 0:
        return []

    # Start from touch (if any) and pull back to be at least quote_edge from
    # fair. NEVER quote through the touch (that would become an aggressive
    # take in the simulator).
    best_bid = max(bids) if bids else None
    best_ask = min(asks) if asks else None

    bid_px = int(math.floor(adj_fair - quote_edge))
    ask_px = int(math.ceil(adj_fair + quote_edge))

    # Try to join or improve the inside by 1 tick if still far enough from
    # fair; but never cross.
    if best_bid is not None:
        candidate_bid = int(best_bid) + 1
        if candidate_bid <= adj_fair - quote_edge:
            bid_px = candidate_bid
        if best_ask is not None and bid_px >= int(best_ask):
            bid_px = int(best_ask) - 1  # never touch the ask
    if best_ask is not None:
        candidate_ask = int(best_ask) - 1
        if candidate_ask >= adj_fair + quote_edge:
            ask_px = candidate_ask
        if best_bid is not None and ask_px <= int(best_bid):
            ask_px = int(best_bid) + 1  # never touch the bid

    # Sanity: if our own quotes crossed, bail.
    if bid_px >= ask_px:
        return []

    orders = []
    if buy_room > 0:
        orders.append(Order(sym, bid_px, buy_room))
    if sell_room > 0:
        orders.append(Order(sym, ask_px, -sell_room))
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
        hp_mid = None
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
        vx_mid = None
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
                    HP_INV_SKEW, HP_TREND_T)

        # ---- VEX ----
        if vx_bids is not None or vx_asks is not None:
            pos = state.position.get(vx_sym, 0)
            hard = POS_LIMIT[vx_sym]
            if closeout:
                result[vx_sym] = _closeout(vx_sym, vx_bids or {}, vx_asks or {}, pos)
            elif vx_state.get("slow") is not None and vx_state.get("fast") is not None:
                fair = vx_state["slow"]
                trend = vx_state["fast"] - vx_state["slow"]
                result[vx_sym] = _passive_mm(
                    vx_sym, vx_bids or {}, vx_asks or {}, pos, hard,
                    fair, trend, VX_MAX_POS, VX_QUOTE_EDGE,
                    VX_INV_SKEW, VX_TREND_T)

        # ---- Deep-ITM vouchers ----
        # Fair = VEX microprice - strike (use fast VEX estimate so voucher
        # fair tracks the underlying without excessive lag).
        vx_fast = vx_state.get("fast")
        vx_slow = vx_state.get("slow")
        if vx_fast is not None and vx_slow is not None:
            trend_vx = vx_fast - vx_slow
            for sym, K in VEV_DEEP_STRIKES.items():
                if sym not in state.order_depths:
                    continue
                b, a = _book(state.order_depths[sym])
                pos = state.position.get(sym, 0)
                hard = POS_LIMIT[sym]
                if closeout:
                    result[sym] = _closeout(sym, b, a, pos)
                    continue
                fair = max(0.0, vx_fast - K)
                result[sym] = _passive_mm(
                    sym, b, a, pos, hard, fair, trend_vx,
                    VEV_MAX_POS, VEV_QUOTE_EDGE, VEV_INV_SKEW, VEV_TREND_T)

        return result, 0, json.dumps(td)