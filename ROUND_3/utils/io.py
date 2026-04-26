"""
Data loading utilities for Round 3 (10 vouchers + 2 delta-1 products).

CSV format is identical to Round 1 (';'-separated, 3 levels per side).
Voucher prices can be half-integers; we preserve floats throughout.
"""
import csv
import os
from collections import defaultdict

from utils.constants import DATA_DIR


def load_prices(day):
    path = os.path.join(DATA_DIR, f"prices_round_3_day_{day}.csv")
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
    path = os.path.join(DATA_DIR, f"trades_round_3_day_{day}.csv")
    rows = []
    if not os.path.exists(path):
        return rows
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
            "bids": bids,
            "asks": asks,
            "mid_price": row["mid_price"],
        }
    return dict(sorted(books.items()))


def build_trade_index(trade_rows):
    idx = defaultdict(list)
    for t in trade_rows:
        idx[t["timestamp"]].append(t)
    return dict(idx)
