"""
Base strategy interface for the Round 3 backtester.
"""
from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    @abstractmethod
    def on_tick(self, timestamp, product, bids, asks, mid_price, position, trades):
        """Returns list of (price, signed_size) tuples. Positive=buy, negative=sell."""
        pass

    @abstractmethod
    def reset(self):
        pass
