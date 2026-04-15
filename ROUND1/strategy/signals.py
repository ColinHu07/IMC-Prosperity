"""
Signal computation utilities: book imbalance, z-scores, etc.
"""

def book_imbalance(bids, asks):
    """Top-of-book volume imbalance in [-1, 1]. Positive = more bids."""
    if not bids or not asks:
        return 0.0
    best_bid_vol = max(bids.values()) if bids else 0
    best_ask_vol = max(asks.values()) if asks else 0
    # Use actual top-of-book volumes
    bb = max(bids.keys())
    ba = min(asks.keys())
    bv = bids.get(bb, 0)
    av = asks.get(ba, 0)
    total = bv + av
    if total == 0:
        return 0.0
    return (bv - av) / total


def total_depth(book_side):
    return sum(book_side.values()) if book_side else 0


class RunningZScore:
    """Online z-score tracker using exponential moving variance."""

    def __init__(self, alpha=0.01):
        self.alpha = alpha
        self.mean = 0.0
        self.var = 1.0
        self.initialized = False

    def update(self, value):
        if not self.initialized:
            self.mean = value
            self.var = 1.0
            self.initialized = True
            return 0.0
        self.mean = self.alpha * value + (1 - self.alpha) * self.mean
        diff = value - self.mean
        self.var = self.alpha * diff * diff + (1 - self.alpha) * self.var
        std = max(self.var ** 0.5, 0.01)
        return diff / std
