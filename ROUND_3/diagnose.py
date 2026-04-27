"""
Pre-V1 data diagnostics. Answers:
1. How often does a VEV_4000/VEV_4500 ask trade below (S_mid - K) at the current tick?
   i.e. how often is the deep-ITM arb signal real?
2. What is the cross-sectional IV dispersion range we actually see?
3. What is the realised mid-volatility per product per day?

No parameters, no strategy; just tape statistics.
"""
import csv
import math
import os
import sys
from collections import defaultdict

_R3 = os.path.dirname(os.path.abspath(__file__))
if _R3 not in sys.path:
    sys.path.insert(0, _R3)

from utils.io import load_prices, build_order_books
from utils.constants import DAYS, VOUCHER_STRIKES


def _bs_call(S, K, T, sigma):
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    v = sigma * math.sqrt(T)
    d1 = (math.log(S/K) + 0.5*sigma*sigma*T) / v
    ncdf = lambda x: 0.5 * (1 + math.erf(x/math.sqrt(2)))
    return S*ncdf(d1) - K*ncdf(d1 - v)


def _iv(mid, S, K, T):
    intr = max(S-K, 0.0)
    if mid <= intr + 1e-6:
        return None
    lo, hi = 1e-4, 5.0
    flo = _bs_call(S, K, T, lo) - mid
    fhi = _bs_call(S, K, T, hi) - mid
    if flo * fhi > 0:
        return None
    for _ in range(40):
        m = 0.5*(lo+hi)
        f = _bs_call(S, K, T, m) - mid
        if f*flo < 0: hi = m
        else:         lo = m
    return 0.5*(lo+hi)


def _micro(bids, asks):
    if bids and asks:
        bb, ba = max(bids), min(asks)
        bv, av = bids[bb], asks[ba]
        if bv+av > 0: return (bb*av + ba*bv)/(bv+av)
        return 0.5*(bb+ba)
    return None


def run():
    for day in DAYS:
        prices = load_prices(day)
        books = build_order_books(prices)
        ts_sorted = sorted(books.keys())
        print(f"\n=== Day {day} ===")

        arb_ask_hits = {"VEV_4000": 0, "VEV_4500": 0}
        arb_bid_hits = {"VEV_4000": 0, "VEV_4500": 0}
        arb_ask_edge = {"VEV_4000": [], "VEV_4500": []}
        arb_bid_edge = {"VEV_4000": [], "VEV_4500": []}
        iv_disp = []
        hp_mids = []
        vex_mids = []

        for i, ts in enumerate(ts_sorted):
            snap = books[ts]
            if "VELVETFRUIT_EXTRACT" not in snap:
                continue
            vex = snap["VELVETFRUIT_EXTRACT"]
            S = _micro(vex["bids"], vex["asks"])
            if S is None:
                continue
            vex_mids.append(S)
            if "HYDROGEL_PACK" in snap:
                hp = snap["HYDROGEL_PACK"]
                hp_mid = _micro(hp["bids"], hp["asks"])
                if hp_mid is not None:
                    hp_mids.append(hp_mid)

            # Deep-ITM arb detection
            for sym in ("VEV_4000", "VEV_4500"):
                if sym not in snap:
                    continue
                K = VOUCHER_STRIKES[sym]
                fair = max(S - K, 0.0)
                asks_ = snap[sym]["asks"]
                bids_ = snap[sym]["bids"]
                if asks_:
                    best_ask = min(asks_.keys())
                    edge = fair - best_ask   # positive = ask is cheap
                    if edge > 0:
                        arb_ask_hits[sym] += 1
                        arb_ask_edge[sym].append(edge)
                if bids_:
                    best_bid = max(bids_.keys())
                    edge = best_bid - fair   # positive = bid is rich
                    if edge > 0:
                        arb_bid_hits[sym] += 1
                        arb_bid_edge[sym].append(edge)

            # IV dispersion for near-ATM
            TTE_days = (8 - day) - ts/1_000_000.0
            T = TTE_days / 365.0
            ivs = []
            for sym in ("VEV_5000", "VEV_5100", "VEV_5200",
                        "VEV_5300", "VEV_5400", "VEV_5500"):
                if sym not in snap:
                    continue
                m = _micro(snap[sym]["bids"], snap[sym]["asks"])
                if m is None:
                    continue
                iv = _iv(m, S, VOUCHER_STRIKES[sym], T)
                if iv is not None:
                    ivs.append(iv)
            if len(ivs) >= 3:
                iv_disp.append(max(ivs) - min(ivs))

        n = len(ts_sorted)
        def _stats(lst):
            if not lst: return "n=0"
            return f"n={len(lst)} mean={sum(lst)/len(lst):.3f} max={max(lst):.3f}"

        print(f"  ticks: {n}")
        for sym in ("VEV_4000", "VEV_4500"):
            print(f"  {sym} ask<fair ticks: {arb_ask_hits[sym]} ({100*arb_ask_hits[sym]/n:.1f}%)  {_stats(arb_ask_edge[sym])}")
            print(f"  {sym} bid>fair ticks: {arb_bid_hits[sym]} ({100*arb_bid_hits[sym]/n:.1f}%)  {_stats(arb_bid_edge[sym])}")
        if iv_disp:
            print(f"  near-ATM IV dispersion (max-min per tick): mean={sum(iv_disp)/len(iv_disp):.4f}  max={max(iv_disp):.4f}")
        if hp_mids:
            diffs = [hp_mids[i]-hp_mids[i-1] for i in range(1, len(hp_mids))]
            std = (sum(x*x for x in diffs)/len(diffs))**0.5
            print(f"  HYDROGEL first-diff std: {std:.3f}")
        if vex_mids:
            diffs = [vex_mids[i]-vex_mids[i-1] for i in range(1, len(vex_mids))]
            std = (sum(x*x for x in diffs)/len(diffs))**0.5
            print(f"  VEX first-diff std: {std:.3f}")


if __name__ == "__main__":
    run()
