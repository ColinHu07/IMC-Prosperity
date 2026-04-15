"""
Fair value estimation models for each product type.
"""


class EWMAFairValue:
    """Exponentially weighted moving average fair value tracker."""

    def __init__(self, alpha=0.05):
        self.alpha = alpha
        self.value = None

    def update(self, mid):
        if mid is None:
            return self.value
        if self.value is None:
            self.value = mid
        else:
            self.value = self.alpha * mid + (1 - self.alpha) * self.value
        return self.value

    def get(self):
        return self.value


class OnlineLinearTrend:
    """
    Online linear fair value: price = base + rate * step_count.
    Estimates both base and rate with expanding-window OLS,
    but warm-starts the rate with a known prior to converge fast.
    """

    def __init__(self, rate_prior=0.1, ewma_alpha=0.005, trend_ewma_alpha=0.001):
        self.rate_prior = rate_prior
        self.ewma_alpha = ewma_alpha
        self.trend_ewma_alpha = trend_ewma_alpha
        self.n = 0
        self.sx = 0.0
        self.sy = 0.0
        self.sxy = 0.0
        self.sxx = 0.0
        self.base = None
        self.rate = rate_prior
        self.ewma_base = None

    def update(self, mid, step):
        if mid is None:
            return self.get(step)
        self.n += 1
        self.sx += step
        self.sy += mid
        self.sxy += step * mid
        self.sxx += step * step

        denom = self.n * self.sxx - self.sx * self.sx
        if self.n >= 20 and denom != 0:
            self.rate = (self.n * self.sxy - self.sx * self.sy) / denom
            self.base = (self.sy - self.rate * self.sx) / self.n
        elif self.n == 1:
            self.base = mid
        else:
            if self.base is None:
                self.base = mid
            self.rate = self.rate_prior

        # EWMA smoothing on the base estimate for robustness
        current_base = mid - self.rate * step
        if self.ewma_base is None:
            self.ewma_base = current_base
        else:
            self.ewma_base = self.ewma_alpha * current_base + (1 - self.ewma_alpha) * self.ewma_base

        return self.get(step)

    def get(self, step):
        if self.ewma_base is not None:
            return self.ewma_base + self.rate * step
        if self.base is not None:
            return self.base + self.rate * step
        return None

    def get_residual(self, mid, step):
        fv = self.get(step)
        if fv is None or mid is None:
            return 0.0
        return mid - fv
