"""
Minimal IMC Prosperity datamodel shim for Round 3.

Unlike Round 1's shim, voucher prices can be half-integers (e.g. 0.5, 8.5),
so Order.price is stored as float. Integer-only products (HYDROGEL_PACK,
VELVETFRUIT_EXTRACT) still receive integer prices from the trader.
"""
from typing import Dict


class Order:
    def __init__(self, symbol: str, price, quantity: int):
        self.symbol = symbol
        self.price = float(price)
        self.quantity = int(quantity)

    def __repr__(self):
        return f"Order({self.symbol}, px={self.price}, qty={self.quantity})"


class OrderDepth:
    def __init__(self):
        self.buy_orders: Dict[float, int] = {}
        self.sell_orders: Dict[float, int] = {}


class Listing:
    def __init__(self, symbol: str, product: str, denomination: str = "SEASHELLS"):
        self.symbol = symbol
        self.product = product
        self.denomination = denomination


class Trade:
    def __init__(self, symbol, price, quantity, buyer="", seller="", timestamp=0):
        self.symbol = symbol
        self.price = price
        self.quantity = quantity
        self.buyer = buyer
        self.seller = seller
        self.timestamp = timestamp


class Observation:
    def __init__(self):
        self.plainValueObservations = {}
        self.conversionObservations = {}


class TradingState:
    def __init__(
        self,
        traderData: str = "",
        timestamp: int = 0,
        listings=None,
        order_depths=None,
        own_trades=None,
        market_trades=None,
        position=None,
        observations=None,
    ):
        self.traderData = traderData
        self.timestamp = timestamp
        self.listings = listings or {}
        self.order_depths: Dict[str, OrderDepth] = order_depths or {}
        self.own_trades = own_trades or {}
        self.market_trades = market_trades or {}
        self.position: Dict[str, int] = position or {}
        self.observations = observations or Observation()
