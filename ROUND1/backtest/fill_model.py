"""
Fill simulation model.

Fill assumptions (conservative):
- Aggressive orders (crossing the book): filled immediately against visible liquidity,
  up to the depth at that level. Partial fills are allowed.
- Passive orders: filled if subsequent market trades occur at or through our price.
  We use a simple heuristic: passive buys fill if next-step best ask <= our bid price,
  passive sells fill if next-step best bid >= our ask price.
  Fill quantity is min(our_size, traded_volume_at_that_price_or_better).
"""


def simulate_aggressive_fills(orders, bids, asks):
    """
    Simulate fills for aggressive orders that cross the current book.
    Returns: list of (price, filled_size), remaining_passive_orders
    """
    fills = []
    passive = []

    for price, size in orders:
        if size > 0:  # buy order
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
        elif size < 0:  # sell order
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
    """
    Simulate passive fill against next-tick data.
    A passive buy at price P fills if next best_ask <= P or a trade occurs at <= P.
    A passive sell at price P fills if next best_bid >= P or a trade occurs at >= P.
    """
    fills = []

    next_best_ask = min(next_asks.keys()) if next_asks else float("inf")
    next_best_bid = max(next_bids.keys()) if next_bids else 0

    for price, size in passive_orders:
        if size > 0:  # passive buy
            # Fill if market comes to us
            if next_best_ask <= price:
                fill_vol = min(abs(size), next_asks.get(next_best_ask, abs(size)))
                fills.append((price, fill_vol))
            else:
                # Check if trades occurred at our level
                for t in next_trades:
                    if t["price"] <= price:
                        fill_vol = min(abs(size), t["quantity"])
                        fills.append((price, fill_vol))
                        break
        elif size < 0:  # passive sell
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
