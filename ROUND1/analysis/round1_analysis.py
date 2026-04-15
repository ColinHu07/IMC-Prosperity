"""
Round 1 Quantitative Analysis
=============================
Deep analysis of ASH_COATED_OSMIUM and INTARIAN_PEPPER_ROOT.
Outputs a quantitative summary to console and saves key metrics.
"""
import sys, os, json, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict
from utils.io import load_prices, load_trades
from utils.constants import DAYS, PRODUCTS

def compute_stats(values):
    if not values:
        return {}
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / max(n - 1, 1)
    std = var ** 0.5
    sorted_v = sorted(values)
    return {
        "count": n, "mean": round(mean, 4), "std": round(std, 4),
        "min": round(sorted_v[0], 4), "max": round(sorted_v[-1], 4),
        "median": round(sorted_v[n // 2], 4),
        "p5": round(sorted_v[max(0, int(n * 0.05))], 4),
        "p95": round(sorted_v[min(n - 1, int(n * 0.95))], 4),
    }

def autocorrelation(series, lag=1):
    n = len(series)
    if n < lag + 2:
        return 0
    mean = sum(series) / n
    var = sum((v - mean) ** 2 for v in series) / n
    if var == 0:
        return 0
    cov = sum((series[i] - mean) * (series[i + lag] - mean) for i in range(n - lag)) / (n - lag)
    return round(cov / var, 6)

def ewma(series, alpha):
    result = []
    val = series[0]
    for v in series:
        val = alpha * v + (1 - alpha) * val
        result.append(val)
    return result

def analyze_product(product, all_prices, all_trades):
    print(f"\n{'=' * 70}")
    print(f"  ANALYSIS: {product}")
    print(f"{'=' * 70}")

    for day in DAYS:
        prices = [r for r in all_prices[day] if r["product"] == product]
        trades = [t for t in all_trades[day] if t["symbol"] == product]

        mids = [r["mid_price"] for r in prices if r["mid_price"] is not None and r["mid_price"] > 100]
        spreads = []
        best_bids = []
        best_asks = []
        bid_depths = []
        ask_depths = []

        for r in prices:
            bb = r["bid_price_1"]
            ba = r["ask_price_1"]
            if bb is not None and ba is not None:
                spreads.append(ba - bb)
                best_bids.append(bb)
                best_asks.append(ba)

            bd = sum(r[f"bid_volume_{l}"] for l in range(1, 4))
            ad = sum(r[f"ask_volume_{l}"] for l in range(1, 4))
            if bd > 0:
                bid_depths.append(bd)
            if ad > 0:
                ask_depths.append(ad)

        print(f"\n--- Day {day} ---")
        print(f"  Rows: {len(prices)}, Trades: {len(trades)}")
        print(f"  Mid: start={mids[0] if mids else 'N/A'}, end={mids[-1] if mids else 'N/A'}, "
              f"range=[{min(mids):.1f}, {max(mids):.1f}], total_move={mids[-1]-mids[0]:.1f}")
        print(f"  Spread stats: {compute_stats(spreads)}")
        print(f"  Bid depth stats: {compute_stats(bid_depths)}")
        print(f"  Ask depth stats: {compute_stats(ask_depths)}")

        # Returns analysis
        returns = [mids[i] - mids[i-1] for i in range(1, len(mids))]
        print(f"  Returns stats: {compute_stats(returns)}")
        print(f"  Return autocorrelation: lag1={autocorrelation(returns,1)}, "
              f"lag2={autocorrelation(returns,2)}, lag5={autocorrelation(returns,5)}")

        # Trade flow
        trade_prices = [t["price"] for t in trades]
        trade_sizes = [t["quantity"] for t in trades]
        if trade_prices:
            print(f"  Trade prices: {compute_stats(trade_prices)}")
            print(f"  Trade sizes: {compute_stats(trade_sizes)}")

        # Trend analysis for dynamic products
        if len(mids) > 100:
            # Linear regression: mid = a + b*t
            n = len(mids)
            t_vals = list(range(n))
            t_mean = (n - 1) / 2
            m_mean = sum(mids) / n
            num = sum((t - t_mean) * (m - m_mean) for t, m in zip(t_vals, mids))
            den = sum((t - t_mean) ** 2 for t in t_vals)
            slope = num / den if den > 0 else 0
            intercept = m_mean - slope * t_mean
            residuals = [m - (intercept + slope * t) for t, m in zip(t_vals, mids)]
            print(f"  Linear trend: slope={slope:.6f}/step ({slope*10000:.1f} over day), "
                  f"intercept={intercept:.2f}")
            print(f"  Residual stats: {compute_stats(residuals)}")
            print(f"  Residual autocorr: lag1={autocorrelation(residuals,1)}, "
                  f"lag5={autocorrelation(residuals,5)}, lag10={autocorrelation(residuals,10)}")

            # EWMA comparison
            ewma_fast = ewma(mids, 0.1)
            ewma_slow = ewma(mids, 0.01)
            ewma_deviations_fast = [m - e for m, e in zip(mids, ewma_fast)]
            ewma_deviations_slow = [m - e for m, e in zip(mids, ewma_slow)]
            print(f"  EWMA(0.1) deviation: {compute_stats(ewma_deviations_fast)}")
            print(f"  EWMA(0.01) deviation: {compute_stats(ewma_deviations_slow)}")

        # Book imbalance analysis
        imbalances = []
        for r in prices:
            bv1 = r["bid_volume_1"]
            av1 = r["ask_volume_1"]
            total = bv1 + av1
            if total > 0:
                imbalances.append((bv1 - av1) / total)

        if imbalances and len(returns) > 0:
            # Does imbalance predict next return?
            min_len = min(len(imbalances) - 1, len(returns))
            hi_imb_rets = [returns[i] for i in range(min_len) if imbalances[i] > 0.3]
            lo_imb_rets = [returns[i] for i in range(min_len) if imbalances[i] < -0.3]
            if hi_imb_rets:
                print(f"  Book imbalance > 0.3 -> avg next return: {sum(hi_imb_rets)/len(hi_imb_rets):.4f} (n={len(hi_imb_rets)})")
            if lo_imb_rets:
                print(f"  Book imbalance < -0.3 -> avg next return: {sum(lo_imb_rets)/len(lo_imb_rets):.4f} (n={len(lo_imb_rets)})")

    return {}


def main():
    print("Loading data...")
    all_prices = {}
    all_trades = {}
    for day in DAYS:
        all_prices[day] = load_prices(day)
        all_trades[day] = load_trades(day)
    print("Data loaded.")

    results = {}
    for product in PRODUCTS:
        results[product] = analyze_product(product, all_prices, all_trades)

    # Cross-day summary
    print(f"\n{'=' * 70}")
    print("  CROSS-DAY SUMMARY")
    print(f"{'=' * 70}")

    for product in PRODUCTS:
        print(f"\n{product}:")
        for day in DAYS:
            prices = [r for r in all_prices[day] if r["product"] == product]
            mids = [r["mid_price"] for r in prices if r["mid_price"] is not None and r["mid_price"] > 100]
            if mids:
                print(f"  Day {day:+d}: start={mids[0]:.1f}, end={mids[-1]:.1f}, "
                      f"move={mids[-1]-mids[0]:.1f}, mean={sum(mids)/len(mids):.1f}")

    # Fair value model comparison
    print(f"\n{'=' * 70}")
    print("  FAIR VALUE MODEL COMPARISON")
    print(f"{'=' * 70}")

    for product in PRODUCTS:
        print(f"\n{product}:")
        for day in DAYS:
            prices = [r for r in all_prices[day] if r["product"] == product]
            mids = [r["mid_price"] for r in prices if r["mid_price"] is not None and r["mid_price"] > 100]
            if not mids:
                continue

            n = len(mids)
            t_vals = list(range(n))

            # Static fair (overall mean)
            static_fair = sum(mids) / n
            static_err = sum((m - static_fair) ** 2 for m in mids) / n

            # Rolling fair (window=50)
            roll_errs = []
            for i in range(50, n):
                rf = sum(mids[i-50:i]) / 50
                roll_errs.append((mids[i] - rf) ** 2)
            roll_err = sum(roll_errs) / len(roll_errs) if roll_errs else float("inf")

            # EWMA fair (alpha=0.05)
            ewma_vals = ewma(mids, 0.05)
            ewma_err = sum((m - e) ** 2 for m, e in zip(mids, ewma_vals)) / n

            # Linear trend + residual
            t_mean = (n - 1) / 2
            m_mean = sum(mids) / n
            num = sum((t - t_mean) * (m - m_mean) for t, m in zip(t_vals, mids))
            den = sum((t - t_mean) ** 2 for t in t_vals)
            slope = num / den if den > 0 else 0
            intercept = m_mean - slope * t_mean
            trend_err = sum((m - (intercept + slope * t)) ** 2 for t, m in zip(t_vals, mids)) / n

            # Online linear estimate (expanding window)
            online_errs = []
            sx = sy = sxy = sxx = 0.0
            for i in range(n):
                sx += i
                sy += mids[i]
                sxy += i * mids[i]
                sxx += i * i
                nn = i + 1
                if nn >= 20:
                    b = (nn * sxy - sx * sy) / (nn * sxx - sx * sx) if (nn * sxx - sx * sx) != 0 else 0
                    a = (sy - b * sx) / nn
                    pred = a + b * i
                    online_errs.append((mids[i] - pred) ** 2)
            online_err = sum(online_errs) / len(online_errs) if online_errs else float("inf")

            print(f"  Day {day:+d} MSE: static={static_err:.2f}, roll50={roll_err:.2f}, "
                  f"ewma05={ewma_err:.2f}, linear={trend_err:.2f}, online_linear={online_err:.2f}")

    print("\nAnalysis complete.")


if __name__ == "__main__":
    main()
