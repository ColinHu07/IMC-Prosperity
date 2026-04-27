from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ImportError:
    from prosperity4bt.datamodel import Order, OrderDepth, TradingState


# ======================================================================
# Mark-led R4 trader (full structural overhaul of the prior CCPM).
#
# Replaces the previous "generic passive maker + small cp_sig modulation"
# architecture with an explicit Mark-regime-driven engine:
#
#   1. Update per-product Mark telemetry (aggressor + maker flow, decayed).
#   2. Classify each product into one of:
#        MAKER_DOMINANT, BUY_AGGRESSOR, SELL_AGGRESSOR, QUIET.
#   3. Look up a per-product policy that selects sides/levels/size.
#   4. Apply an inventory-state overlay for HP/VEX/VEV_4000 only.
#   5. Emit orders with strict guardrails (int px/qty, dedupe, caps).
#
# Only HP, VEX and VEV_4000 are policy-managed. The rest of the voucher
# strikes keep the same (passive-only / disabled / flatten-only) treatment
# as the +909.96 frozen branch -- they are intentionally outside the
# new mark-led logic.
# ======================================================================


HP = "HYDROGEL_PACK"
VEX = "VELVETFRUIT_EXTRACT"
VEV4000 = "VEV_4000"
VEV4500 = "VEV_4500"
VEV5000 = "VEV_5000"
VEV5100 = "VEV_5100"
VEV5200 = "VEV_5200"
VEV5300 = "VEV_5300"
VEV5400 = "VEV_5400"
VEV5500 = "VEV_5500"
VEV6000 = "VEV_6000"
VEV6500 = "VEV_6500"

VOUCHERS = [
    VEV4000, VEV4500, VEV5000, VEV5100, VEV5200,
    VEV5300, VEV5400, VEV5500, VEV6000, VEV6500,
]
# VEV_4000 is policy-managed below; the others are pure-passive baseline.
PASSIVE_VOUCHERS = [VEV5200, VEV5300, VEV5400, VEV5500]
DISABLED_VOUCHERS = [VEV4500, VEV5000, VEV5100, VEV6000, VEV6500]

POS_LIMITS: Dict[str, int] = {
    HP: 200,
    VEX: 200,
    VEV4000: 300,
    VEV4500: 300,
    VEV5000: 300,
    VEV5100: 300,
    VEV5200: 300,
    VEV5300: 300,
    VEV5400: 300,
    VEV5500: 300,
    VEV6000: 300,
    VEV6500: 300,
}

# Risk caps unchanged from frozen +909.96 branch.
SOFT_CAPS: Dict[str, int] = {
    HP: 180,
    VEX: 180,
    VEV4000: 100,
    VEV4500: 0,
    VEV5000: 0,
    VEV5100: 0,
    VEV5200: 60,
    VEV5300: 60,
    VEV5400: 60,
    VEV5500: 60,
    VEV6000: 0,
    VEV6500: 0,
}

GROSS_VOUCHER_CAP = 320
DD_KILL_SWITCH = -20_000.0

# Counterparty classes (extended per plan: Mark 01 also acts as a maker).
NOISE_TAKERS = {"Mark 38", "Mark 55", "Mark 22"}
STRONG_MAKERS = {"Mark 14", "Mark 01"}
ALL_TAGGED_MARKS = NOISE_TAKERS | STRONG_MAKERS

# Regime constants.
REG_MAKER = "MAKER_DOMINANT"
REG_BUYAGG = "BUY_AGGRESSOR"
REG_SELLAGG = "SELL_AGGRESSOR"
REG_QUIET = "QUIET"

# Inventory states.
INV_NEUTRAL = "NEUTRAL"
INV_STRETCHED = "STRETCHED"
INV_EXTREME = "EXTREME"

# Inventory thresholds (fraction of soft cap).
INV_NEUTRAL_FRAC = 0.40
INV_STRETCHED_FRAC = 0.75

# Telemetry tunables.
# EWMA half-life is governed by alpha. We use one alpha per channel so
# aggressor signal reacts faster than maker presence (which is steady).
ALPHA_AGG = 0.30
ALPHA_MAKER = 0.15
# Trades older than this many timestamps are ignored entirely when
# scoring telemetry from cumulative market_trades.
MAX_TRADE_AGE = 30_000
# Mark scores below this are treated as "no signal" by the classifier.
AGG_THRESHOLD = 2.0
MAKER_THRESHOLD = 1.0

# Per-product two-level ladder ratios. VEV_4000 has no L2 (plan: L1-only).
HP_L2_RATIO = 0.45
VEX_L2_RATIO = 0.35


def _book(od: OrderDepth) -> Tuple[Dict[float, int], Dict[float, int]]:
    bids = {float(p): abs(v) for p, v in (od.buy_orders or {}).items()}
    asks = {float(p): abs(v) for p, v in (od.sell_orders or {}).items()}
    return bids, asks


def _microprice(bids: Dict[float, int], asks: Dict[float, int]) -> Optional[float]:
    if bids and asks:
        bb = max(bids)
        ba = min(asks)
        bv = bids[bb]
        av = asks[ba]
        if bv + av > 0:
            return (bb * av + ba * bv) / (bv + av)
        return 0.5 * (bb + ba)
    if bids:
        return float(max(bids))
    if asks:
        return float(min(asks))
    return None


def _ewma(prev: Optional[float], x: float, alpha: float) -> float:
    if prev is None:
        return x
    return alpha * x + (1.0 - alpha) * prev


def _decay(prev: float, age: int, scale: float) -> float:
    if age <= 0 or prev == 0.0:
        return prev
    return prev * math.exp(-float(age) / float(scale))


class Trader:
    """
    Round-4 Mark-led market-maker.

    Decision flow per tick:
        decode -> record mid -> update mark telemetry
        -> per product: classify regime, get inventory state,
           look up policy, emit orders
        -> apply voucher cap / disabled-strike flatten
        -> encode + return
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # State serialization
    # ------------------------------------------------------------------
    def _decode(self, raw: str) -> dict:
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def _encode(self, d: dict) -> str:
        try:
            return json.dumps(d, separators=(",", ":"))
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Mark telemetry (replaces _update_mark_scores).
    # Tracks per-product, per-direction aggressor and maker flow with
    # incremental dedupe to keep complexity O(new trades) per tick.
    # ------------------------------------------------------------------
    def _update_mark_telemetry(self, state: TradingState, td: dict) -> None:
        agg_buy = td.setdefault("agg_buy", {})    # {sym: float}
        agg_sell = td.setdefault("agg_sell", {})  # {sym: float}
        maker = td.setdefault("maker", {})        # {sym: float}
        last_agg_ts = td.setdefault("last_agg_ts", {})    # {sym: int}
        last_maker_ts = td.setdefault("last_maker_ts", {})  # {sym: int}
        last_trade_ts = td.setdefault("last_trade_ts", {})
        last_trade_sig = td.setdefault("last_trade_sig", {})

        ts_now = int(state.timestamp)

        # First, decay existing scores by elapsed time since last update.
        # We store a "last decay timestamp" so consecutive ticks with no
        # trades still bleed signal toward zero.
        last_decay = td.setdefault("last_decay_ts", ts_now)
        elapsed = max(0, ts_now - int(last_decay))
        if elapsed > 0:
            # 12_000-step half-life-ish for aggressor, slower for maker.
            for sym in list(agg_buy.keys()):
                agg_buy[sym] = _decay(agg_buy[sym], elapsed, 8_000.0)
                if agg_buy[sym] < 1e-3:
                    agg_buy.pop(sym, None)
            for sym in list(agg_sell.keys()):
                agg_sell[sym] = _decay(agg_sell[sym], elapsed, 8_000.0)
                if agg_sell[sym] < 1e-3:
                    agg_sell.pop(sym, None)
            for sym in list(maker.keys()):
                maker[sym] = _decay(maker[sym], elapsed, 15_000.0)
                if maker[sym] < 1e-3:
                    maker.pop(sym, None)
        td["last_decay_ts"] = ts_now

        # Process only new trades since the last seen (ts, sig) per symbol.
        for sym, trades in (state.market_trades or {}).items():
            if not trades:
                continue
            prev_ts = int(last_trade_ts.get(sym, -1))
            prev_sig = str(last_trade_sig.get(sym, ""))
            max_ts_for_sym = prev_ts
            max_sig_for_sym = prev_sig

            for tr in trades:
                tts = int(getattr(tr, "timestamp", ts_now) or ts_now)
                # Discard ancient trades (cumulative buffer protection).
                if ts_now - tts > MAX_TRADE_AGE:
                    continue
                buyer = getattr(tr, "buyer", "") or ""
                seller = getattr(tr, "seller", "") or ""
                qty = int(getattr(tr, "quantity", 0) or 0)
                px = float(getattr(tr, "price", 0.0) or 0.0)
                if qty <= 0:
                    continue
                sig = f"{tts}|{buyer}|{seller}|{qty}|{px:.3f}"
                # Dedupe rule: if same ts and sig <= prev_sig, skip.
                if tts < prev_ts:
                    continue
                if tts == prev_ts and sig <= prev_sig:
                    continue
                if tts > max_ts_for_sym or (tts == max_ts_for_sym and sig > max_sig_for_sym):
                    max_ts_for_sym = tts
                    max_sig_for_sym = sig

                # Direction tagging:
                #   buyer is taker => buy aggression at ask
                #   seller is taker => sell aggression at bid
                #   maker side present => maker activity
                if buyer in NOISE_TAKERS and seller not in NOISE_TAKERS:
                    agg_buy[sym] = _ewma(agg_buy.get(sym), float(qty), ALPHA_AGG)
                    last_agg_ts[sym] = ts_now
                if seller in NOISE_TAKERS and buyer not in NOISE_TAKERS:
                    agg_sell[sym] = _ewma(agg_sell.get(sym), float(qty), ALPHA_AGG)
                    last_agg_ts[sym] = ts_now
                if buyer in STRONG_MAKERS or seller in STRONG_MAKERS:
                    maker[sym] = _ewma(maker.get(sym), float(qty), ALPHA_MAKER)
                    last_maker_ts[sym] = ts_now

            last_trade_ts[sym] = max_ts_for_sym
            last_trade_sig[sym] = max_sig_for_sym

        # Bound traderData: prune symbols not seen recently.
        def _prune(d: dict, ts_map: dict, max_age: int = MAX_TRADE_AGE) -> None:
            stale = [k for k, v in ts_map.items() if ts_now - int(v) > max_age]
            for k in stale:
                d.pop(k, None)
                ts_map.pop(k, None)
        _prune(agg_buy, last_agg_ts)
        _prune(agg_sell, last_agg_ts)
        _prune(maker, last_maker_ts)
        # Hard cap on dictionary sizes as a safety net.
        for d in (agg_buy, agg_sell, maker, last_agg_ts, last_maker_ts,
                  last_trade_ts, last_trade_sig):
            if len(d) > 64:
                # Drop arbitrary excess keys; symbol space is small (<= 12).
                for k in list(d.keys())[64:]:
                    d.pop(k, None)

    # ------------------------------------------------------------------
    # Regime classification.
    # ------------------------------------------------------------------
    def _classify_regime(self, td: dict, sym: str, ts: int) -> str:
        agg_buy = float(td.get("agg_buy", {}).get(sym, 0.0))
        agg_sell = float(td.get("agg_sell", {}).get(sym, 0.0))
        maker_score = float(td.get("maker", {}).get(sym, 0.0))

        agg_active = (agg_buy >= AGG_THRESHOLD) or (agg_sell >= AGG_THRESHOLD)
        maker_active = maker_score >= MAKER_THRESHOLD

        if agg_active:
            # Pick the dominant direction; require a clear margin to flip.
            if agg_buy >= agg_sell * 1.25 and agg_buy >= AGG_THRESHOLD:
                return REG_BUYAGG
            if agg_sell >= agg_buy * 1.25 and agg_sell >= AGG_THRESHOLD:
                return REG_SELLAGG
            # Tie: treat as quiet so we don't pick a direction noisily.
            return REG_QUIET
        if maker_active:
            return REG_MAKER
        return REG_QUIET

    # ------------------------------------------------------------------
    # Inventory state.
    # ------------------------------------------------------------------
    def _inventory_state(self, pos: int, cap: int) -> str:
        if cap <= 0:
            return INV_EXTREME if pos != 0 else INV_NEUTRAL
        frac = abs(pos) / float(cap)
        if frac <= INV_NEUTRAL_FRAC:
            return INV_NEUTRAL
        if frac <= INV_STRETCHED_FRAC:
            return INV_STRETCHED
        return INV_EXTREME

    # ------------------------------------------------------------------
    # Per-product policy table.
    # Returns a dict the emitter understands:
    #   sides:      {"buy", "sell"} subset
    #   l2:         bool (allow second passive level)
    #   l2_ratio:   float (fraction of base size for L2)
    #   inside_buy/inside_sell: int (extra "inside" tick to take queue
    #                                priority, 0 = at-touch)
    #   skew_scale: float (multiplier on inv_skew)
    #   size_scale: float (multiplier on base size)
    # ------------------------------------------------------------------
    def _policy_for(self, sym: str, regime: str, inv_state: str,
                    pos: int) -> Optional[Dict]:
        if sym == HP:
            policy = self._policy_hp(regime)
        elif sym == VEX:
            policy = self._policy_vex(regime)
        elif sym == VEV4000:
            policy = self._policy_vev4000(regime, pos)
        else:
            return None

        if policy is None:
            return None

        # Inventory overlay (after regime decision).
        sides = set(policy.get("sides", {"buy", "sell"}))
        l2 = bool(policy.get("l2", False))
        if inv_state == INV_STRETCHED:
            # Suppress risk-increasing L2 only.
            if pos > 0 and "buy" in sides:
                # holding long, suppress further long-side L2
                policy = dict(policy)
                policy["l2_buy"] = False
            elif pos < 0 and "sell" in sides:
                policy = dict(policy)
                policy["l2_sell"] = False
        elif inv_state == INV_EXTREME:
            # Flatten-only L1; no other quotes for this product this tick.
            policy = dict(policy)
            if pos > 0:
                policy["sides"] = {"sell"}
            elif pos < 0:
                policy["sides"] = {"buy"}
            else:
                policy["sides"] = set()
            policy["l2"] = False
            policy["l2_buy"] = False
            policy["l2_sell"] = False

        return policy

    def _policy_hp(self, regime: str) -> Dict:
        base = {
            "sides": {"buy", "sell"},
            "l2": True,
            "l2_ratio": HP_L2_RATIO,
            "l2_buy": True,
            "l2_sell": True,
            "inside_buy": 0,
            "inside_sell": 0,
            "skew_scale": 1.0,
            "size_scale": 1.0,
        }
        if regime == REG_BUYAGG:
            # Quote inside best ask by 1 tick on sell side; do not raise bid.
            base["inside_sell"] = 1
            base["size_scale"] = 1.20
        elif regime == REG_SELLAGG:
            base["inside_buy"] = 1
            base["size_scale"] = 1.20
        elif regime == REG_MAKER:
            # Stay at touch with normal sizes; do not cross.
            base["size_scale"] = 1.05
        else:  # QUIET
            base["size_scale"] = 0.90
        return base

    def _policy_vex(self, regime: str) -> Dict:
        base = {
            "sides": {"buy", "sell"},
            "l2": True,
            "l2_ratio": VEX_L2_RATIO,
            "l2_buy": True,
            "l2_sell": True,
            "inside_buy": 0,
            "inside_sell": 0,
            "skew_scale": 1.15,  # slightly stronger skew (tighter spread)
            "size_scale": 1.0,
        }
        if regime == REG_BUYAGG:
            base["inside_sell"] = 1
            base["size_scale"] = 1.15
        elif regime == REG_SELLAGG:
            base["inside_buy"] = 1
            base["size_scale"] = 1.15
        elif regime == REG_MAKER:
            base["size_scale"] = 1.0
        else:  # QUIET
            base["size_scale"] = 0.85
        return base

    def _policy_vev4000(self, regime: str, pos: int) -> Optional[Dict]:
        # VEV_4000: L1 only, conservative.
        if regime == REG_MAKER or regime == REG_QUIET:
            return {
                "sides": {"buy", "sell"},
                "l2": False,
                "l2_ratio": 0.0,
                "l2_buy": False,
                "l2_sell": False,
                "inside_buy": 0,
                "inside_sell": 0,
                "skew_scale": 1.0,
                "size_scale": 1.0,
            }
        if regime == REG_BUYAGG:
            # One-sided opposite to aggressor: only quote sell side.
            return {
                "sides": {"sell"},
                "l2": False,
                "l2_ratio": 0.0,
                "l2_buy": False,
                "l2_sell": False,
                "inside_buy": 0,
                "inside_sell": 0,
                "skew_scale": 1.0,
                "size_scale": 1.0,
            }
        if regime == REG_SELLAGG:
            return {
                "sides": {"buy"},
                "l2": False,
                "l2_ratio": 0.0,
                "l2_buy": False,
                "l2_sell": False,
                "inside_buy": 0,
                "inside_sell": 0,
                "skew_scale": 1.0,
                "size_scale": 1.0,
            }
        return None

    # ------------------------------------------------------------------
    # Order emission. No hidden gating: if a side is allowed by policy
    # and there is room, we quote it. Skipping is only via empty `sides`.
    # ------------------------------------------------------------------
    def _emit_orders(
        self,
        sym: str,
        policy: Dict,
        bids: Dict[float, int],
        asks: Dict[float, int],
        fair: float,
        pos: int,
        cap: int,
        base_qty: int,
    ) -> List[Order]:
        if not policy:
            return []
        sides = set(policy.get("sides", set()))
        if not sides or (not bids and not asks):
            return []

        bb = max(bids) if bids else fair - 2
        ba = min(asks) if asks else fair + 2
        spread = ba - bb
        if spread <= 0:
            return []

        size_scale = float(policy.get("size_scale", 1.0))
        q = max(1, int(round(base_qty * size_scale)))
        buy_room = max(0, cap - pos)
        sell_room = max(0, cap + pos)
        if buy_room <= 0 and sell_room <= 0:
            return []

        # Inventory tilt: encourage mean-reversion toward 0.
        # 0.035 was the empirical sweet spot on the +909.96 branch.
        inv_skew = 0.035 * float(policy.get("skew_scale", 1.0)) * pos

        # At-touch baseline; +1 tick "inside" if regime requests queue
        # priority on a side. Inside means more aggressive (closer to fair).
        inside_buy = int(policy.get("inside_buy", 0))
        inside_sell = int(policy.get("inside_sell", 0))
        bid_px = min(bb + 1 + inside_buy, ba - 1)
        ask_px = max(ba - 1 - inside_sell, bb + 1)
        if bid_px >= ask_px:
            bid_px = bb
            ask_px = ba
        # Apply skew: if long, shade quotes down (lower bid, lower ask
        # equivalent => actually want to shed -> raise willingness to sell).
        # Net effect we want: long pos -> nudge sell more aggressive.
        # Implementation matches the +909.96 branch.
        if bid_px + 1e-9 >= fair - inv_skew:
            bid_px = min(bb, fair - inv_skew - 0.5)
        if ask_px - 1e-9 <= fair - inv_skew:
            ask_px = max(ba, fair - inv_skew + 0.5)

        orders: List[Order] = []

        first_buy = min(q, buy_room) if "buy" in sides else 0
        first_sell = min(q, sell_room) if "sell" in sides else 0
        if first_buy > 0:
            orders.append(Order(sym, int(round(bid_px)), int(first_buy)))
        if first_sell > 0:
            orders.append(Order(sym, int(round(ask_px)), -int(first_sell)))

        if policy.get("l2", False):
            l2_ratio = float(policy.get("l2_ratio", 0.0))
            if l2_ratio > 0.0:
                q2 = max(1, int(round(q * l2_ratio)))
                rem_buy = max(0, buy_room - first_buy)
                rem_sell = max(0, sell_room - first_sell)
                bid2 = max(bb, bid_px - 1)
                ask2 = min(ba, ask_px + 1)
                allow_l2_buy = bool(policy.get("l2_buy", True)) and "buy" in sides
                allow_l2_sell = bool(policy.get("l2_sell", True)) and "sell" in sides
                if allow_l2_buy and rem_buy > 0 and bid2 < ask_px:
                    orders.append(Order(sym, int(round(bid2)), int(min(q2, rem_buy))))
                if allow_l2_sell and rem_sell > 0 and ask2 > bid_px:
                    orders.append(Order(sym, int(round(ask2)), -int(min(q2, rem_sell))))

        return orders

    # ------------------------------------------------------------------
    # Mid recording (mark fast/slow EWMA per symbol). Used as `fair`.
    # ------------------------------------------------------------------
    def _record_mark(self, td: dict, state: TradingState) -> None:
        mids = td.setdefault("mid", {})
        for sym, od in state.order_depths.items():
            bids, asks = _book(od)
            m = _microprice(bids, asks)
            if m is None:
                continue
            cur = mids.get(sym) or {}
            mids[sym] = {
                "fast": _ewma(cur.get("fast"), m, 0.10),
                "slow": _ewma(cur.get("slow"), m, 0.02),
            }

    def _gross_voucher_pos(self, pos: Dict[str, int]) -> int:
        return sum(abs(int(pos.get(v, 0))) for v in VOUCHERS)

    # ------------------------------------------------------------------
    # Voucher passive layer (kept identical in spirit to +909.96 branch).
    # No mark logic applied to VEV_5200..VEV_5500.
    # ------------------------------------------------------------------
    def _voucher_passive_orders(
        self,
        sym: str,
        bids: Dict[float, int],
        asks: Dict[float, int],
        fair: float,
        pos: int,
        cap: int,
    ) -> List[Order]:
        if cap <= 0 or (not bids and not asks):
            return []
        bb = max(bids) if bids else fair - 2
        ba = min(asks) if asks else fair + 2
        if ba - bb <= 0:
            return []
        base = 3
        q = max(1, base)
        buy_room = max(0, cap - pos)
        sell_room = max(0, cap + pos)
        if buy_room <= 0 and sell_room <= 0:
            return []
        inv_skew = 0.035 * pos
        bid_px = min(bb + 1, ba - 1)
        ask_px = max(ba - 1, bb + 1)
        if bid_px >= ask_px:
            bid_px = bb
            ask_px = ba
        if bid_px + 1e-9 >= fair - inv_skew:
            bid_px = min(bb, fair - inv_skew - 0.5)
        if ask_px - 1e-9 <= fair - inv_skew:
            ask_px = max(ba, fair - inv_skew + 0.5)
        orders: List[Order] = []
        if buy_room > 0:
            orders.append(Order(sym, int(round(bid_px)), int(min(q, buy_room))))
        if sell_room > 0:
            orders.append(Order(sym, int(round(ask_px)), -int(min(q, sell_room))))
        return orders

    # ------------------------------------------------------------------
    # Main entry.
    # ------------------------------------------------------------------
    def run(self, state: TradingState):
        td = self._decode(state.traderData)
        self._record_mark(td, state)
        self._update_mark_telemetry(state, td)

        pos = dict(state.position or {})
        result: Dict[str, List[Order]] = {}
        ts = int(state.timestamp)

        # Realized PnL tracking from own_trades for kill switch.
        realized = float(td.get("realized", 0.0))
        own_trades = state.own_trades or {}
        if own_trades:
            for _sym, trades in own_trades.items():
                for tr in trades or []:
                    if int(getattr(tr, "timestamp", ts) or ts) != ts:
                        continue
                    qty = int(getattr(tr, "quantity", 0) or 0)
                    px = float(getattr(tr, "price", 0.0) or 0.0)
                    realized -= qty * px
        td["realized"] = realized
        kill = realized <= DD_KILL_SWITCH

        gross_v = self._gross_voucher_pos(pos)
        voucher_frozen = gross_v >= GROSS_VOUCHER_CAP or kill

        # Optional: capture regimes for diagnostics in traderData.
        regimes_seen: Dict[str, str] = {}

        # ---------- HP / VEX core (regime-driven two-level) ----------
        for sym, base_qty in ((HP, 20), (VEX, 18)):
            od = state.order_depths.get(sym)
            if od is None:
                continue
            bids, asks = _book(od)
            mp = _microprice(bids, asks)
            if mp is None:
                continue
            mark = td.get("mid", {}).get(sym, {})
            fair = float(mark.get("slow", mp))
            trend = float(mark.get("fast", mp) - mark.get("slow", mp))
            # Strong trend => smaller size (do not chase).
            if abs(trend) > 10:
                base_qty = max(8, int(base_qty * 0.7))

            cap = SOFT_CAPS.get(sym, POS_LIMITS[sym])
            p = int(pos.get(sym, 0))
            regime = self._classify_regime(td, sym, ts)
            inv_state = self._inventory_state(p, cap)
            policy = self._policy_for(sym, regime, inv_state, p)
            regimes_seen[sym] = regime
            if policy is None:
                continue
            orders = self._emit_orders(
                sym=sym,
                policy=policy,
                bids=bids,
                asks=asks,
                fair=fair,
                pos=p,
                cap=cap,
                base_qty=base_qty,
            )
            if orders:
                result[sym] = orders

        # ---------- VEV_4000: regime-driven, L1 only ----------
        if not voucher_frozen:
            sym = VEV4000
            od = state.order_depths.get(sym)
            if od is not None:
                bids, asks = _book(od)
                mp = _microprice(bids, asks)
                if mp is not None:
                    cap = SOFT_CAPS.get(sym, 0)
                    p = int(pos.get(sym, 0))
                    regime = self._classify_regime(td, sym, ts)
                    inv_state = self._inventory_state(p, cap)
                    policy = self._policy_for(sym, regime, inv_state, p)
                    regimes_seen[sym] = regime
                    if policy is not None and cap > 0:
                        orders = self._emit_orders(
                            sym=sym,
                            policy=policy,
                            bids=bids,
                            asks=asks,
                            fair=mp,
                            pos=p,
                            cap=cap,
                            base_qty=5,
                        )
                        if orders:
                            result[sym] = orders
        else:
            # Frozen branch: gentle flatten if non-zero.
            sym = VEV4000
            od = state.order_depths.get(sym)
            if od is not None:
                p = int(pos.get(sym, 0))
                if p != 0:
                    bids, asks = _book(od)
                    if p > 0 and bids:
                        result[sym] = [Order(sym, int(max(bids)), -int(min(p, 20)))]
                    elif p < 0 and asks:
                        result[sym] = [Order(sym, int(min(asks)), int(min(-p, 20)))]

        # ---------- VEV_5200..VEV_5500 passive baseline (no mark logic) ----------
        for sym in PASSIVE_VOUCHERS:
            od = state.order_depths.get(sym)
            if od is None:
                continue
            cap = SOFT_CAPS.get(sym, 0)
            if cap <= 0:
                continue
            p = int(pos.get(sym, 0))
            if voucher_frozen:
                bids, asks = _book(od)
                orders: List[Order] = []
                if p > 0 and bids:
                    orders.append(Order(sym, int(max(bids)), -int(min(p, 20))))
                elif p < 0 and asks:
                    orders.append(Order(sym, int(min(asks)), int(min(-p, 20))))
                if orders:
                    result[sym] = orders
                continue
            bids, asks = _book(od)
            mp = _microprice(bids, asks)
            if mp is None:
                continue
            orders = self._voucher_passive_orders(
                sym=sym,
                bids=bids,
                asks=asks,
                fair=mp,
                pos=p,
                cap=cap,
            )
            if orders:
                result[sym] = orders

        # ---------- Disabled strikes: flatten only ----------
        for sym in DISABLED_VOUCHERS:
            od = state.order_depths.get(sym)
            if od is None:
                continue
            p = int(pos.get(sym, 0))
            if p == 0:
                continue
            bids, asks = _book(od)
            if p > 0 and bids:
                result[sym] = [Order(sym, int(max(bids)), -int(min(20, p)))]
            elif p < 0 and asks:
                result[sym] = [Order(sym, int(min(asks)), int(min(20, -p)))]

        # Light diagnostic state (small, capped).
        td["regimes"] = regimes_seen

        return result, 0, self._encode(td)