"""
Fill simulation model. Same rules as Round 1, but price comparisons work
for float prices (vouchers trade on 0.5-tick grid).
"""

PESSIMISTIC_FILL_RATIO = 0.3


def simulate_aggressive_fills(orders, bids, asks):
    fills = []
    passive = []
    for price, size in orders:
        if size > 0:
            if asks:
                best_ask = min(asks.keys())
                if price >= best_ask:
                    available = asks.get(best_ask, 0)
                    filled = min(abs(size), available)
                    if filled > 0:
                        fills.append((best_ask, filled))
                    remainder = abs(size) - filled
                    if remainder > 0:
                        passive.append((price, remainder))
                else:
                    passive.append((price, size))
            else:
                passive.append((price, size))
        elif size < 0:
            if bids:
                best_bid = max(bids.keys())
                if price <= best_bid:
                    available = bids.get(best_bid, 0)
                    filled = min(abs(size), available)
                    if filled > 0:
                        fills.append((best_bid, -filled))
                    remainder = abs(size) - filled
                    if remainder > 0:
                        passive.append((price, -remainder))
                else:
                    passive.append((price, size))
            else:
                passive.append((price, size))
    return fills, passive


def simulate_passive_fills(passive_orders, next_bids, next_asks, next_trades):
    fills = []
    next_best_ask = min(next_asks.keys()) if next_asks else float("inf")
    next_best_bid = max(next_bids.keys()) if next_bids else 0
    for price, size in passive_orders:
        if size > 0:
            if next_best_ask <= price:
                fill_vol = min(abs(size), next_asks.get(next_best_ask, abs(size)))
                fills.append((price, fill_vol))
            else:
                for t in next_trades:
                    if t["price"] <= price:
                        fill_vol = min(abs(size), t["quantity"])
                        fills.append((price, fill_vol))
                        break
        elif size < 0:
            if next_best_bid >= price:
                fill_vol = min(abs(size), next_bids.get(next_best_bid, abs(size)))
                fills.append((price, -fill_vol))
            else:
                for t in next_trades:
                    if t["price"] >= price:
                        fill_vol = min(abs(size), t["quantity"])
                        fills.append((price, -fill_vol))
                        break
    return fills


def simulate_passive_fills_pessimistic(passive_orders, next_bids, next_asks, next_trades,
                                       fill_ratio=PESSIMISTIC_FILL_RATIO):
    fills = []
    next_best_ask = min(next_asks.keys()) if next_asks else float("inf")
    next_best_bid = max(next_bids.keys()) if next_bids else 0
    for price, size in passive_orders:
        if size > 0:
            if next_best_ask < price:
                avail = next_asks.get(next_best_ask, 0)
                cap = max(1, int(avail * fill_ratio))
                fill_vol = min(abs(size), cap)
                if fill_vol > 0:
                    fills.append((price, fill_vol))
            else:
                for t in next_trades:
                    if t["price"] < price:
                        cap = max(1, int(t["quantity"] * fill_ratio))
                        fill_vol = min(abs(size), cap)
                        fills.append((price, fill_vol))
                        break
        elif size < 0:
            if next_best_bid > price:
                avail = next_bids.get(next_best_bid, 0)
                cap = max(1, int(avail * fill_ratio))
                fill_vol = min(abs(size), cap)
                if fill_vol > 0:
                    fills.append((price, -fill_vol))
            else:
                for t in next_trades:
                    if t["price"] > price:
                        cap = max(1, int(t["quantity"] * fill_ratio))
                        fill_vol = min(abs(size), cap)
                        fills.append((price, -fill_vol))
                        break
    return fills
