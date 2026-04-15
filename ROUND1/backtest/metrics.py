"""
Backtest metrics computation.
"""
import json
import os


class BacktestMetrics:
    """Tracks PnL, position, drawdown, turnover, fill counts per product."""

    def __init__(self, products):
        self.products = products
        self.reset()

    def reset(self):
        self.cash = {p: 0.0 for p in self.products}
        self.position = {p: 0 for p in self.products}
        self.pnl_history = {p: [] for p in self.products}
        self.position_history = {p: [] for p in self.products}
        self.fill_count = {p: 0 for p in self.products}
        self.turnover = {p: 0.0 for p in self.products}
        self.aggressive_fills = {p: 0 for p in self.products}
        self.passive_fills = {p: 0 for p in self.products}
        self.max_position = {p: 0 for p in self.products}
        self.last_mid = {p: None for p in self.products}

    def record_fill(self, product, price, size, fill_type="aggressive"):
        self.cash[product] -= price * size
        self.position[product] += size
        self.fill_count[product] += 1
        self.turnover[product] += abs(price * size)
        self.max_position[product] = max(self.max_position[product], abs(self.position[product]))
        if fill_type == "aggressive":
            self.aggressive_fills[product] += 1
        else:
            self.passive_fills[product] += 1

    def record_tick(self, product, mid_price):
        self.last_mid[product] = mid_price
        mtm = self.cash[product] + self.position[product] * (mid_price if mid_price else 0)
        self.pnl_history[product].append(mtm)
        self.position_history[product].append(self.position[product])

    def get_product_summary(self, product):
        pnl = self.pnl_history[product]
        if not pnl:
            return {}

        final_pnl = pnl[-1]
        peak = pnl[0]
        max_dd = 0
        for v in pnl:
            if v > peak:
                peak = v
            dd = peak - v
            if dd > max_dd:
                max_dd = dd

        pos = self.position_history[product]
        avg_abs_pos = sum(abs(p) for p in pos) / len(pos) if pos else 0

        return {
            "final_pnl": round(final_pnl, 2),
            "max_drawdown": round(max_dd, 2),
            "fill_count": self.fill_count[product],
            "aggressive_fills": self.aggressive_fills[product],
            "passive_fills": self.passive_fills[product],
            "turnover": round(self.turnover[product], 2),
            "max_position": self.max_position[product],
            "avg_abs_position": round(avg_abs_pos, 2),
            "final_position": self.position[product],
        }

    def get_summary(self):
        summary = {}
        total_pnl = 0
        total_dd = 0
        for p in self.products:
            s = self.get_product_summary(p)
            summary[p] = s
            total_pnl += s.get("final_pnl", 0)
            total_dd = max(total_dd, s.get("max_drawdown", 0))

        summary["TOTAL"] = {
            "final_pnl": round(total_pnl, 2),
            "max_drawdown": round(total_dd, 2),
        }
        return summary


def compute_composite_score(day_summaries, lambda_dd=0.5, lambda_inv=0.1, lambda_consist=1.0):
    """
    Composite objective across multiple days.
    score = total_pnl + lambda_consist * min(day_pnl) - lambda_dd * max_dd - lambda_inv * avg_abs_pos
    """
    day_pnls = []
    total_pnl = 0
    max_dd = 0
    total_avg_abs_pos = 0
    n_days = len(day_summaries)

    for ds in day_summaries:
        dpnl = ds["TOTAL"]["final_pnl"]
        day_pnls.append(dpnl)
        total_pnl += dpnl
        max_dd = max(max_dd, ds["TOTAL"]["max_drawdown"])
        for prod in ds:
            if prod != "TOTAL":
                total_avg_abs_pos += ds[prod].get("avg_abs_position", 0)

    avg_inv = total_avg_abs_pos / max(n_days, 1)
    min_day_pnl = min(day_pnls) if day_pnls else 0

    score = (total_pnl
             + lambda_consist * min_day_pnl
             - lambda_dd * max_dd
             - lambda_inv * avg_inv)

    return {
        "score": round(score, 2),
        "total_pnl": round(total_pnl, 2),
        "min_day_pnl": round(min_day_pnl, 2),
        "max_drawdown": round(max_dd, 2),
        "avg_inventory": round(avg_inv, 2),
        "day_pnls": [round(d, 2) for d in day_pnls],
    }


def save_metrics(metrics_dict, filepath):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(metrics_dict, f, indent=2)
