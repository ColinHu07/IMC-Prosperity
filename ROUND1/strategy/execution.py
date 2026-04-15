"""
Execution logic: converting signals into concrete orders.
Handles taking, making, inventory skew, and sizing.
"""
import math


def compute_take_orders(fair_value, bids, asks, take_threshold, max_take_size,
                        position, position_limit, inventory_skew_factor=0.0):
    """
    Generate aggressive (taking) orders when market prices cross our fair value
    by more than the threshold.
    Returns list of (price, size) tuples. Positive size = buy, negative = sell.
    """
    orders = []
    if fair_value is None:
        return orders

    skew = -position * inventory_skew_factor

    # Take asks that are cheap
    if asks:
        for ap in sorted(asks.keys()):
            if ap < fair_value - take_threshold + skew:
                max_buy = position_limit - position
                vol = min(asks[ap], max_take_size, max_buy)
                if vol > 0:
                    orders.append((ap, vol))
                    position += vol

    # Take bids that are rich
    if bids:
        for bp in sorted(bids.keys(), reverse=True):
            if bp > fair_value + take_threshold + skew:
                max_sell = position_limit + position
                vol = min(bids[bp], max_take_size, max_sell)
                if vol > 0:
                    orders.append((bp, -vol))
                    position -= vol

    return orders


def compute_make_orders(fair_value, make_width, max_passive_size,
                        position, position_limit, inventory_skew_factor=0.0,
                        bids=None, asks=None):
    """
    Generate passive (making) orders around fair value with inventory skew.
    Returns list of (price, size) tuples.
    """
    orders = []
    if fair_value is None:
        return orders

    # Inventory skew shifts our quotes away from building more inventory
    skew = -position * inventory_skew_factor

    bid_price = math.floor(fair_value - make_width + skew)
    ask_price = math.ceil(fair_value + make_width + skew)

    # Penny the best bid/ask if it improves our quote
    if bids:
        best_bid = max(bids.keys())
        penny_bid = best_bid + 1
        if penny_bid < fair_value + skew and penny_bid > bid_price:
            bid_price = penny_bid

    if asks:
        best_ask = min(asks.keys())
        penny_ask = best_ask - 1
        if penny_ask > fair_value + skew and penny_ask < ask_price:
            ask_price = penny_ask

    max_buy = position_limit - position
    max_sell = position_limit + position

    bid_size = min(max_passive_size, max_buy)
    ask_size = min(max_passive_size, max_sell)

    if bid_size > 0:
        orders.append((bid_price, bid_size))
    if ask_size > 0:
        orders.append((ask_price, -ask_size))

    return orders
