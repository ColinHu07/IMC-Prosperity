"""
Data loading utilities for IMC Prosperity Round 1.
"""
import csv
import os
from collections import defaultdict
from utils.constants import DATA_DIR, DAYS, PRODUCTS


def load_prices(day):
    """Load price/order book CSV for a given day. Returns list of dicts."""
    path = os.path.join(DATA_DIR, f"prices_round_1_day_{day}.csv")
    rows = []
    with open(path, "r") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            parsed = {
                "day": int(row["day"]),
                "timestamp": int(row["timestamp"]),
                "product": row["product"],
                "mid_price": float(row["mid_price"]) if row["mid_price"] else None,
                "profit_and_loss": float(row["profit_and_loss"]) if row["profit_and_loss"] else 0.0,
            }
            for level in range(1, 4):
                bp = row.get(f"bid_price_{level}", "")
                bv = row.get(f"bid_volume_{level}", "")
                ap = row.get(f"ask_price_{level}", "")
                av = row.get(f"ask_volume_{level}", "")
                parsed[f"bid_price_{level}"] = float(bp) if bp else None
                parsed[f"bid_volume_{level}"] = int(bv) if bv else 0
                parsed[f"ask_price_{level}"] = float(ap) if ap else None
                parsed[f"ask_volume_{level}"] = int(av) if av else 0
            rows.append(parsed)
    return rows


def load_trades(day):
    """Load trades CSV for a given day."""
    path = os.path.join(DATA_DIR, f"trades_round_1_day_{day}.csv")
    rows = []
    with open(path, "r") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            rows.append({
                "timestamp": int(row["timestamp"]),
                "buyer": row.get("buyer", ""),
                "seller": row.get("seller", ""),
                "symbol": row["symbol"],
                "currency": row.get("currency", ""),
                "price": float(row["price"]),
                "quantity": int(row["quantity"]),
            })
    return rows


def build_order_books(price_rows):
    """
    Build chronological order book snapshots from price rows.
    Returns dict: {timestamp: {product: OrderBookSnapshot}}
    """
    books = defaultdict(dict)
    for row in price_rows:
        ts = row["timestamp"]
        product = row["product"]
        bids = {}
        asks = {}
        for level in range(1, 4):
            bp = row[f"bid_price_{level}"]
            bv = row[f"bid_volume_{level}"]
            ap = row[f"ask_price_{level}"]
            av = row[f"ask_volume_{level}"]
            if bp is not None and bv > 0:
                bids[bp] = bv
            if ap is not None and av > 0:
                asks[ap] = av
        books[ts][product] = {
            "bids": bids,  # {price: volume} descending
            "asks": asks,  # {price: volume} ascending
            "mid_price": row["mid_price"],
        }
    return dict(sorted(books.items()))


def build_trade_index(trade_rows):
    """Index trades by timestamp for quick lookup."""
    idx = defaultdict(list)
    for t in trade_rows:
        idx[t["timestamp"]].append(t)
    return dict(idx)


def load_all_data():
    """Load all days of data. Returns {day: (order_books, trade_index)}."""
    all_data = {}
    for day in DAYS:
        prices = load_prices(day)
        trades = load_trades(day)
        books = build_order_books(prices)
        trade_idx = build_trade_index(trades)
        all_data[day] = (books, trade_idx)
    return all_data
