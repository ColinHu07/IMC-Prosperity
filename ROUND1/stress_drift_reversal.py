"""
Synthetic drift-reversal stress test for PEPPER.

Takes a real day's order-book data and injects a mid-day drift reversal, then
runs trader.py against the reversed dataset to measure the worst-case loss.
This is a tail-risk check — the strategy assumes +0.1/tick drift; we want to
know what happens if that breaks.

The reversal injection: starting at reversal_ts, every subsequent tick's
prices (mids, bids, asks) have an accumulating offset subtracted.  This
mimics a clean drift reversal without destroying book micro-structure.
"""
import os
import sys
import copy

_ROUND1 = os.path.dirname(os.path.abspath(__file__))
if _ROUND1 not in sys.path:
    sys.path.insert(0, _ROUND1)

from utils.io import load_prices, load_trades, build_order_books, build_trade_index
from backtest.fill_model import (
    simulate_aggressive_fills,
    simulate_passive_fills_pessimistic,
)
from backtest.metrics import BacktestMetrics
from backtest.trader_runner import TraderRunner
from utils.constants import POSITION_LIMITS
from trader import Trader


def apply_reversal(books, product, reversal_ts, rate_swap):
    """In-place modify a books dict so that after reversal_ts each price is
    offset by -(t - reversal_ts)/100 * rate_swap relative to normal."""
    out = {}
    for ts, snap_by_prod in books.items():
        new_snap_by_prod = dict(snap_by_prod)
        if product in new_snap_by_prod and ts >= reversal_ts:
            offset = -(ts - reversal_ts) / 100.0 * rate_swap
            ps = new_snap_by_prod[product]
            new_bids = {p + offset: v for p, v in ps["bids"].items()}
            new_asks = {p + offset: v for p, v in ps["asks"].items()}
            new_mid = (ps["mid_price"] + offset) if ps["mid_price"] is not None else None
            new_snap_by_prod[product] = {
                "bids": new_bids,
                "asks": new_asks,
                "mid_price": new_mid,
            }
        out[ts] = new_snap_by_prod
    return out


def run_one(trader, books, trade_idx, products):
    runner = TraderRunner(trader, products, books)
    runner.reset()
    metrics = BacktestMetrics(products)
    timestamps = sorted(books.keys())
    pending = {p: [] for p in products}

    for ts in timestamps:
        for product in products:
            if product not in books[ts]:
                metrics.record_tick(product, metrics.last_mid.get(product))
                continue
            snap = books[ts][product]
            bids = snap["bids"]
            asks = snap["asks"]
            mid = snap["mid_price"]
            if mid is not None and mid < 100:
                mid = metrics.last_mid.get(product)
            ts_trades = [t for t in trade_idx.get(ts, []) if t["symbol"] == product]
            limit = POSITION_LIMITS.get(product, 50)

            if pending[product] and (bids or asks):
                fills = simulate_passive_fills_pessimistic(pending[product], bids, asks, ts_trades)
                for fp, fs in fills:
                    pos = metrics.position[product]
                    if fs > 0 and pos + fs > limit:
                        fs = max(0, limit - pos)
                    elif fs < 0 and pos + fs < -limit:
                        fs = min(0, -(limit + pos))
                    if fs != 0:
                        metrics.record_fill(product, fp, fs, "passive")
                pending[product] = []

            orders = runner.on_tick(ts, product, bids, asks, mid, metrics.position[product], ts_trades)
            agg_fills, passive = simulate_aggressive_fills(orders, bids, asks)
            for fp, fs in agg_fills:
                pos = metrics.position[product]
                if fs > 0 and pos + fs > limit:
                    fs = max(0, limit - pos)
                elif fs < 0 and pos + fs < -limit:
                    fs = min(0, -(limit + pos))
                if fs != 0:
                    metrics.record_fill(product, fp, fs, "aggressive")
            pending[product] = passive
            metrics.record_tick(product, mid)

    return metrics


def main():
    PRODUCT = "INTARIAN_PEPPER_ROOT"
    day = -1
    reversal_ts = 500_000
    rate_swap = 0.20

    print(f"\nStress test — drift reversal at ts={reversal_ts}, rate_swap={rate_swap}")
    print(f"(normal drift +0.1/step -> reversed drift -0.1/step on {PRODUCT})")
    print("=" * 70)

    prices = load_prices(day)
    trades = load_trades(day)
    books_normal = build_order_books(prices)
    trade_idx = build_trade_index(trades)
    books_reversed = apply_reversal(copy.deepcopy(books_normal), PRODUCT, reversal_ts, rate_swap)

    m_normal = run_one(Trader(), books_normal, trade_idx, [PRODUCT])
    s_normal = m_normal.get_product_summary(PRODUCT)
    m_stress = run_one(Trader(), books_reversed, trade_idx, [PRODUCT])
    s_stress = m_stress.get_product_summary(PRODUCT)

    loss = s_normal["final_pnl"] - s_stress["final_pnl"]
    print(f"\n  Normal day   PEPPER PnL = {s_normal['final_pnl']:>12,.2f}"
          f"   (max DD {s_normal['max_drawdown']:,.0f})")
    print(f"  Reversed     PEPPER PnL = {s_stress['final_pnl']:>12,.2f}"
          f"   (max DD {s_stress['max_drawdown']:,.0f})")
    print(f"  Damage from reversal    = {loss:>12,.2f}")
    print()
    print("  Pass criterion: total loss bounded to at most a few times one")
    print("  normal day's PnL.  Unbounded runaway loss would indicate the")
    print("  online OLS failed to adapt to the new regime.")
    print("=" * 70)


if __name__ == "__main__":
    main()
