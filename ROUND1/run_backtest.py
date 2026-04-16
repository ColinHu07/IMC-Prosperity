"""
Main backtest runner: evaluates baseline, improved, and optimized strategies.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategy.config import get_default_params
from strategy.ash_osmium import AshOsmiumStrategy
from strategy.pepper_root import PepperRootStrategy
from strategy.pepper_buyhold import PepperBuyHoldStrategy
from backtest.replay_engine import ReplayEngine
from backtest.metrics import compute_composite_score, save_metrics
from utils.constants import PRODUCTS, DAYS, OUTPUT_DIR


def run_with_params(ash_params, pepper_params, label="strategy", verbose=True):
    strategies = {
        "ASH_COATED_OSMIUM": AshOsmiumStrategy(ash_params),
        "INTARIAN_PEPPER_ROOT": PepperRootStrategy(pepper_params),
    }
    engine = ReplayEngine(strategies)

    print(f"\n{'='*60}")
    print(f"  BACKTEST: {label}")
    print(f"{'='*60}")

    day_summaries = []
    for day in DAYS:
        m = engine.run_day(day, verbose=False)
        s = m.get_summary()
        day_summaries.append(s)
        if verbose:
            print(f"\n  Day {day:+d}:")
            for prod, ps in s.items():
                print(f"    {prod}: {ps}")

    score_info = compute_composite_score(day_summaries)
    score_info["day_details"] = day_summaries
    print(f"\n  COMPOSITE: score={score_info['score']:.2f}, "
          f"total_pnl={score_info['total_pnl']:.2f}, "
          f"days={score_info['day_pnls']}, "
          f"max_dd={score_info['max_drawdown']:.2f}, "
          f"avg_inv={score_info['avg_inventory']:.2f}")

    save_metrics(
        {"label": label, "score_info": score_info,
         "ash_params": ash_params, "pepper_params": pepper_params},
        os.path.join(OUTPUT_DIR, "metrics", f"{label}_results.json")
    )

    return score_info


def run_buyhold_backtest(ash_params, pepper_buyhold_params, label="buyhold", verbose=True):
    """Ash market-making + pepper buy-and-hold (accumulate to limit, no sells)."""
    strategies = {
        "ASH_COATED_OSMIUM": AshOsmiumStrategy(ash_params),
        "INTARIAN_PEPPER_ROOT": PepperBuyHoldStrategy(pepper_buyhold_params),
    }
    engine = ReplayEngine(strategies)

    print(f"\n{'='*60}")
    print(f"  BACKTEST: {label}")
    print(f"{'='*60}")

    day_summaries = []
    for day in DAYS:
        m = engine.run_day(day, verbose=False)
        s = m.get_summary()
        day_summaries.append(s)
        if verbose:
            print(f"\n  Day {day:+d}:")
            for prod, ps in s.items():
                print(f"    {prod}: {ps}")

    score_info = compute_composite_score(day_summaries)
    score_info["day_details"] = day_summaries
    print(f"\n  COMPOSITE: score={score_info['score']:.2f}, "
          f"total_pnl={score_info['total_pnl']:.2f}, "
          f"days={score_info['day_pnls']}, "
          f"max_dd={score_info['max_drawdown']:.2f}, "
          f"avg_inv={score_info['avg_inventory']:.2f}")

    save_metrics(
        {"label": label, "score_info": score_info,
         "ash_params": ash_params, "pepper_buyhold_params": pepper_buyhold_params},
        os.path.join(OUTPUT_DIR, "metrics", f"{label}_results.json")
    )

    return score_info


def main():
    # 1. Baseline
    base = get_default_params()
    baseline_info = run_with_params(
        base["ASH_COATED_OSMIUM"],
        base["INTARIAN_PEPPER_ROOT"],
        label="baseline"
    )

    # 2. Improved (manual tuning based on analysis)
    improved_ash = {
        "ewma_alpha": 0.04,
        "take_threshold": 1.0,
        "make_width": 4,
        "inventory_skew_factor": 0.6,
        "max_passive_size": 25,
        "max_take_size": 20,
        "position_limit": 50,
        "imbalance_edge": 1.0,
    }
    improved_pepper = {
        "trend_rate": 0.1002,
        "ewma_alpha_base": 0.003,
        "take_threshold": 1.0,
        "make_width": 3,
        "inventory_skew_factor": 0.5,
        "max_passive_size": 20,
        "max_take_size": 15,
        "position_limit": 50,
        "directional_skew": 1.0,
        "residual_zscore_threshold": 1.5,
        "trend_ewma_alpha": 0.001,
    }
    improved_info = run_with_params(improved_ash, improved_pepper, label="improved")

    # Buy-and-hold pepper (same ash as baseline for fair comparison)
    buyhold_pepper_params = {
        "position_limit": 50,
        "max_take_size": 50,
        "max_passive_size": 20,
    }
    buyhold_info = run_buyhold_backtest(
        base["ASH_COATED_OSMIUM"],
        buyhold_pepper_params,
        label="buyhold_pepper",
    )

    # 3. Load optimized if available
    opt_path = os.path.join(OUTPUT_DIR, "best_params", "best_params.json")
    if os.path.exists(opt_path):
        with open(opt_path) as f:
            opt = json.load(f)
        opt_info = run_with_params(opt["ash_params"], opt["pepper_params"], label="optimized")
    else:
        print("\n  [No optimized params found. Run optimize.py first.]")

    # Comparison
    print(f"\n{'='*60}")
    print("  COMPARISON")
    print(f"{'='*60}")
    print(f"  Baseline:  score={baseline_info['score']:.2f}, pnl={baseline_info['total_pnl']:.2f}")
    print(f"  Improved:  score={improved_info['score']:.2f}, pnl={improved_info['total_pnl']:.2f}")
    print(f"  Buyhold:   score={buyhold_info['score']:.2f}, pnl={buyhold_info['total_pnl']:.2f}")
    if os.path.exists(opt_path):
        print(f"  Optimized: score={opt_info['score']:.2f}, pnl={opt_info['total_pnl']:.2f}")


if __name__ == "__main__":
    main()
