"""
Base strategy interface for the backtester.
"""
from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    """
    Abstract strategy that processes one timestamp per product.
    Returns a list of (price, size) orders. Positive size = buy, negative = sell.
    """

    @abstractmethod
    def on_tick(self, timestamp, product, bids, asks, mid_price, position, trades):
        """
        Called each timestamp with current order book and position.
        Returns: list of (price, signed_size) tuples
        """
        pass

    @abstractmethod
    def reset(self):
        """Reset internal state for a new day/backtest."""
        pass
