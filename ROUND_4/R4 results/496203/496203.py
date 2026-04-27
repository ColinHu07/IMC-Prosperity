from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Tuple

try:
    from datamodel import Order, OrderDepth, TradingState
except ImportError:
    from prosperity4bt.datamodel import Order, OrderDepth, TradingState


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
    VEV4000,
    VEV4500,
    VEV5000,
    VEV5100,
    VEV5200,
    VEV5300,
    VEV5400,
    VEV5500,
    VEV6000,
    VEV6500,
]
PASSIVE_VOUCHERS = [VEV4000, VEV5200, VEV5300, VEV5400, VEV5500]

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

# Risk caps tuned to avoid R3-style directional voucher concentration.
SOFT_CAPS: Dict[str, int] = {
    HP: 180,
    VEX: 180,
    VEV4000: 100,
    VEV4500: 0,   # disabled
    VEV5000: 0,   # disabled
    VEV5100: 0,   # disabled
    VEV5200: 60,
    VEV5300: 60,
    VEV5400: 60,
    VEV5500: 60,
    VEV6000: 0,   # disabled
    VEV6500: 0,   # disabled
}

GROSS_VOUCHER_CAP = 320
DD_KILL_SWITCH = -20_000.0

# Counterparty classes discovered from R4 data profiling.
NOISE_TAKERS = {"Mark 38", "Mark 55", "Mark 22"}
STRONG_MAKERS = {"Mark 14"}

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


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


class Trader:
    """
    Round-4 Counterparty-Conditioned Passive Maker (CCPM).

    Design intent:
    - Capture spread passively in HP/VEX where aggressive flow is stable.
    - Keep vouchers strictly capped and mostly passive.
    - Disable thin strikes that caused oversized losses in R3.
    - Use market-trade counterparty tags to modulate quote size and skew.
    """

    def __init__(self) -> None:
        # nothing required here; state persists via traderData
        pass

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

    def _update_mark_scores(self, state: TradingState, td: dict) -> None:
        scores = td.setdefault("mark_scores", {})
        last_seen = td.setdefault("last_seen", {})
        last_trade_ts = td.setdefault("last_trade_ts", {})
        last_trade_sig = td.setdefault("last_trade_sig", {})
        alpha = 0.20

        for sym, trades in (state.market_trades or {}).items():
            if not trades:
                continue
            prev_ts = int(last_trade_ts.get(sym, -1))
            prev_sig = str(last_trade_sig.get(sym, ""))
            max_ts_for_sym = prev_ts
            max_sig_for_sym = prev_sig
            for tr in trades:
                tts = int(getattr(tr, "timestamp", state.timestamp) or state.timestamp)
                buyer = getattr(tr, "buyer", "") or ""
                seller = getattr(tr, "seller", "") or ""
                qty = int(getattr(tr, "quantity", 0) or 0)
                px = float(getattr(tr, "price", 0.0) or 0.0)
                if qty <= 0:
                    continue
                sig = f"{tts}|{buyer}|{seller}|{qty}|{px:.3f}"
                # Prevent O(T^2) reprocessing when market_trades is cumulative.
                if tts < prev_ts:
                    continue
                if tts == prev_ts and sig <= prev_sig:
                    continue
                if tts > max_ts_for_sym or (tts == max_ts_for_sym and sig > max_sig_for_sym):
                    max_ts_for_sym = tts
                    max_sig_for_sym = sig
                # Aggressive players are consistently spread-payers in our data.
                if buyer in NOISE_TAKERS:
                    k = f"{buyer}|{sym}"
                    scores[k] = _ewma(scores.get(k), float(qty), alpha)
                    last_seen[k] = state.timestamp
                if seller in NOISE_TAKERS:
                    k = f"{seller}|{sym}"
                    scores[k] = _ewma(scores.get(k), float(qty), alpha)
                    last_seen[k] = state.timestamp
                # Strong maker activity -> avoid crossing through these books.
                if buyer in STRONG_MAKERS:
                    k = f"{buyer}|{sym}"
                    scores[k] = _ewma(scores.get(k), -float(qty), alpha)
                    last_seen[k] = state.timestamp
                if seller in STRONG_MAKERS:
                    k = f"{seller}|{sym}"
                    scores[k] = _ewma(scores.get(k), -float(qty), alpha)
                    last_seen[k] = state.timestamp
            last_trade_ts[sym] = max_ts_for_sym
            last_trade_sig[sym] = max_sig_for_sym

        # Keep traderData bounded to avoid serialization overhead.
        if len(scores) > 256:
            # Drop oldest entries by last_seen timestamp.
            oldest = sorted(last_seen.items(), key=lambda kv: kv[1])[: len(scores) - 200]
            for k, _ in oldest:
                scores.pop(k, None)
                last_seen.pop(k, None)

    def _cp_signal(self, td: dict, sym: str, ts: int) -> float:
        """
        Positive => more noise-taker activity recently (good for passive capture).
        Negative => strong-maker activity (avoid aggressive behavior).
        """
        scores = td.get("mark_scores", {})
        last_seen = td.get("last_seen", {})
        s = 0.0
        for m in NOISE_TAKERS.union(STRONG_MAKERS):
            k = f"{m}|{sym}"
            if k not in scores:
                continue
            age = ts - int(last_seen.get(k, ts))
            if age > 30_000:
                continue
            decay = math.exp(-max(0, age) / 12_000.0)
            s += float(scores.get(k, 0.0)) * decay
        return s

    def _record_mark(self, td: dict, state: TradingState) -> None:
        mids = td.setdefault("mid", {})
        for sym, od in state.order_depths.items():
            bids, asks = _book(od)
            m = _microprice(bids, asks)
            if m is None:
                continue
            mids[sym] = {
                "fast": _ewma((mids.get(sym) or {}).get("fast"), m, 0.10),
                "slow": _ewma((mids.get(sym) or {}).get("slow"), m, 0.02),
            }

    def _gross_voucher_pos(self, pos: Dict[str, int]) -> int:
        return sum(abs(int(pos.get(v, 0))) for v in VOUCHERS)

    def _mm_orders(
        self,
        sym: str,
        bids: Dict[float, int],
        asks: Dict[float, int],
        fair: float,
        pos: int,
        max_pos: int,
        base_qty: int,
        cp_sig: float,
    ) -> List[Order]:
        if not bids and not asks:
            return []

        bb = max(bids) if bids else fair - 2
        ba = min(asks) if asks else fair + 2
        spread = ba - bb
        if spread <= 0:
            return []

        # Passive by default. When taker noise is active, quote a bit larger.
        size_boost = 1.0
        if cp_sig > 35:
            size_boost = 1.45
        elif cp_sig > 15:
            size_boost = 1.20
        elif cp_sig < -15:
            size_boost = 0.75

        q = max(1, int(round(base_qty * size_boost)))
        buy_room = max(0, max_pos - pos)
        sell_room = max(0, max_pos + pos)
        if buy_room <= 0 and sell_room <= 0:
            return []

        orders: List[Order] = []

        # Inside-touch passive quotes with tiny inventory tilt.
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

        first_buy = min(q, buy_room)
        first_sell = min(q, sell_room)
        if first_buy > 0:
            orders.append(Order(sym, int(round(bid_px)), int(first_buy)))
        if first_sell > 0:
            orders.append(Order(sym, int(round(ask_px)), -int(first_sell)))

        # Second passive level: slightly less aggressive to capture slower flow.
        q2 = max(1, int(round(q * 0.45)))
        rem_buy = max(0, buy_room - first_buy)
        rem_sell = max(0, sell_room - first_sell)
        bid2 = max(bb, bid_px - 1)
        ask2 = min(ba, ask_px + 1)
        if rem_buy > 0 and bid2 < ask_px:
            orders.append(Order(sym, int(round(bid2)), int(min(q2, rem_buy))))
        if rem_sell > 0 and ask2 > bid_px:
            orders.append(Order(sym, int(round(ask2)), -int(min(q2, rem_sell))))
        return orders

    def run(self, state: TradingState):
        td = self._decode(state.traderData)
        self._record_mark(td, state)
        self._update_mark_scores(state, td)

        pos = dict(state.position or {})
        result: Dict[str, List[Order]] = {}
        ts = int(state.timestamp)

        # Approximate realized PnL from own fills when own_trades are available.
        # If unavailable, keep previous value so kill-switch behavior is stable.
        realized = float(td.get("realized", 0.0))
        own_trades = state.own_trades or {}
        if own_trades:
            for _sym, trades in own_trades.items():
                for tr in trades or []:
                    if int(getattr(tr, "timestamp", ts) or ts) != ts:
                        continue
                    qty = int(getattr(tr, "quantity", 0) or 0)
                    px = float(getattr(tr, "price", 0.0) or 0.0)
                    # Buy decreases realized cash, sell increases it.
                    realized -= qty * px
        td["realized"] = realized
        kill = realized <= DD_KILL_SWITCH

        gross_v = self._gross_voucher_pos(pos)
        voucher_frozen = gross_v >= GROSS_VOUCHER_CAP or kill

        # ---------- Core books: HP + VEX ----------
        for sym, base_qty in ((HP, 20), (VEX, 18)):
            od = state.order_depths.get(sym)
            if od is None:
                continue
            bids, asks = _book(od)
            mp = _microprice(bids, asks)
            mark = td.get("mid", {}).get(sym, {})
            fair = float(mark.get("slow", mp if mp is not None else 0.0))
            if mp is None:
                continue
            trend = float(mark.get("fast", mp) - mark.get("slow", mp))
            # In strong trend, quote smaller and avoid crossing entirely.
            if abs(trend) > 10:
                base_qty = max(8, int(base_qty * 0.7))
            cp_sig = self._cp_signal(td, sym, ts)
            cap = SOFT_CAPS.get(sym, POS_LIMITS[sym])
            orders = self._mm_orders(
                sym=sym,
                bids=bids,
                asks=asks,
                fair=fair,
                pos=int(pos.get(sym, 0)),
                max_pos=cap,
                base_qty=base_qty,
                cp_sig=cp_sig,
            )
            if orders:
                result[sym] = orders

        # ---------- Voucher layer (strictly passive / capped) ----------
        for sym in PASSIVE_VOUCHERS:
            od = state.order_depths.get(sym)
            if od is None:
                continue
            cap = SOFT_CAPS.get(sym, 0)
            if cap <= 0:
                continue
            p = int(pos.get(sym, 0))
            if voucher_frozen:
                # flatten gently if risk gate is hit
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
            cp_sig = self._cp_signal(td, sym, ts)
            base = 5 if sym == VEV4000 else 3
            # More taker-flow -> widen size slightly, but keep hard caps.
            if cp_sig > 20:
                base += 1
            fair = mp
            orders = self._mm_orders(
                sym=sym,
                bids=bids,
                asks=asks,
                fair=fair,
                pos=p,
                max_pos=cap,
                base_qty=base,
                cp_sig=cp_sig,
            )
            if orders:
                result[sym] = orders

        # Explicitly disable known weak/thin strikes.
        for sym in (VEV4500, VEV5000, VEV5100, VEV6000, VEV6500):
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

        return result, 0, self._encode(td)