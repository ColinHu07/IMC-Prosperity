"""
Minimal shim of the IMC Prosperity datamodel so trader.py can be
exercised by our local backtester with the same code that gets shipped.

Only the fields trader.py actually touches are populated:
- OrderDepth.buy_orders, OrderDepth.sell_orders (dict[price] = volume)
- TradingState.order_depths, TradingState.position, TradingState.traderData,
  TradingState.timestamp
- Order(symbol, price, quantity)
"""
from typing import Dict


class Order:
    def __init__(self, symbol: str, price: int, quantity: int):
        self.symbol = symbol
        self.price = int(price)
        self.quantity = int(quantity)

    def __repr__(self):
        return f"Order({self.symbol}, px={self.price}, qty={self.quantity})"


class OrderDepth:
    def __init__(self):
        self.buy_orders: Dict[int, int] = {}
        self.sell_orders: Dict[int, int] = {}


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
