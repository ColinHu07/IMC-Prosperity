"""
Strategy for INTARIAN_PEPPER_ROOT.

Market structure summary:
- Deterministic linear drift: ~0.1 per step (~1000 per day), extremely consistent
- After detrending, residual is near white noise (autocorr ~0.01)
- Linear model MSE ~4-5, far superior to rolling/EWMA
- Spread ~12-14 ticks
- Book imbalance predictive (~3 avg return per direction)

Strategy: Online linear trend estimation with trend-aware quoting.
The structural drift provides directional edge; we can bias long to ride the trend
and capture spread around the trend fair value.
"""
from strategy.base import BaseStrategy
from strategy.fair_value import OnlineLinearTrend
from strategy.signals import book_imbalance, RunningZScore
from strategy.execution import compute_take_orders, compute_make_orders


class PepperRootStrategy(BaseStrategy):

    def __init__(self, params):
        self.params = params
        self.trend_model = OnlineLinearTrend(
            rate_prior=params["trend_rate"],
            ewma_alpha=params["ewma_alpha_base"],
            trend_ewma_alpha=params.get("trend_ewma_alpha", 0.001),
        )
        self.residual_zscore = RunningZScore(alpha=0.02)
        self.step = 0

    def reset(self):
        p = self.params
        self.trend_model = OnlineLinearTrend(
            rate_prior=p["trend_rate"],
            ewma_alpha=p["ewma_alpha_base"],
            trend_ewma_alpha=p.get("trend_ewma_alpha", 0.001),
        )
        self.residual_zscore = RunningZScore(alpha=0.02)
        self.step = 0

    def on_tick(self, timestamp, product, bids, asks, mid_price, position, trades):
        self.step += 1
        p = self.params

        fair = self.trend_model.update(mid_price, self.step)
        if fair is None:
            return []

        residual = self.trend_model.get_residual(mid_price, self.step)
        zscore = self.residual_zscore.update(residual)

        # Directional skew: positive = bias toward buying (riding the trend)
        dir_skew = p.get("directional_skew", 0.0)

        # Adjust take threshold based on residual z-score
        z_thr = p.get("residual_zscore_threshold", 1.5)
        take_adj = 0.0
        if abs(zscore) > z_thr:
            # Price is far from trend → more aggressive taking toward mean
            take_adj = -0.5 if zscore > 0 else 0.5

        adjusted_fair = fair + dir_skew + take_adj

        orders = []

        take_orders = compute_take_orders(
            adjusted_fair, bids, asks,
            take_threshold=p["take_threshold"],
            max_take_size=p["max_take_size"],
            position=position,
            position_limit=p["position_limit"],
            inventory_skew_factor=p["inventory_skew_factor"],
        )
        orders.extend(take_orders)

        pos_after_takes = position + sum(s for _, s in take_orders)

        make_orders = compute_make_orders(
            adjusted_fair,
            make_width=p["make_width"],
            max_passive_size=p["max_passive_size"],
            position=pos_after_takes,
            position_limit=p["position_limit"],
            inventory_skew_factor=p["inventory_skew_factor"],
            bids=bids,
            asks=asks,
        )
        orders.extend(make_orders)

        return orders
