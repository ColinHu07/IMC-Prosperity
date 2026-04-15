"""
Optimization module: parameter search with composite scoring.
Supports grid search, random search, and local refinement.
"""
import sys, os, json, random, time, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy.config import get_default_params
from strategy.ash_osmium import AshOsmiumStrategy
from strategy.pepper_root import PepperRootStrategy
from backtest.replay_engine import ReplayEngine
from backtest.metrics import compute_composite_score, save_metrics
from utils.constants import PRODUCTS, DAYS, OUTPUT_DIR


# Parameter search spaces (min, max, step)
ASH_SEARCH_SPACE = {
    "ewma_alpha": (0.01, 0.15, 0.02),
    "take_threshold": (0.0, 4.0, 0.5),
    "make_width": (1, 6, 1),
    "inventory_skew_factor": (0.0, 1.5, 0.25),
    "max_passive_size": (10, 40, 5),
    "imbalance_edge": (0.0, 2.0, 0.5),
}

PEPPER_SEARCH_SPACE = {
    "trend_rate": (0.095, 0.105, 0.002),
    "ewma_alpha_base": (0.001, 0.02, 0.003),
    "take_threshold": (0.0, 4.0, 0.5),
    "make_width": (1, 5, 1),
    "inventory_skew_factor": (0.0, 1.5, 0.25),
    "directional_skew": (-1.0, 3.0, 0.5),
    "residual_zscore_threshold": (0.5, 3.0, 0.5),
}


def sample_params(search_space, base_params, mode="random"):
    """Generate a candidate parameter set."""
    params = dict(base_params)
    for key, (lo, hi, step) in search_space.items():
        if mode == "random":
            # Random from discrete grid
            n_steps = max(1, int((hi - lo) / step))
            idx = random.randint(0, n_steps)
            params[key] = round(lo + idx * step, 6)
        elif mode == "grid":
            pass  # handled externally
    return params


def local_refine(params, search_space, scale=0.5):
    """Small perturbation around current best for local search."""
    new_params = dict(params)
    # Perturb 1-2 random parameters
    keys = list(search_space.keys())
    n_perturb = random.randint(1, min(2, len(keys)))
    for key in random.sample(keys, n_perturb):
        lo, hi, step = search_space[key]
        delta = random.choice([-1, 1]) * step * scale
        new_val = params[key] + delta
        new_params[key] = round(max(lo, min(hi, new_val)), 6)
    return new_params


def evaluate_params(ash_params, pepper_params, days=None, verbose=False):
    """Run backtest with given params and return composite score dict."""
    if days is None:
        days = DAYS

    strategies = {
        "ASH_COATED_OSMIUM": AshOsmiumStrategy(ash_params),
        "INTARIAN_PEPPER_ROOT": PepperRootStrategy(pepper_params),
    }
    engine = ReplayEngine(strategies)
    day_summaries = engine.run_all_days(days=days, verbose=verbose)
    score_info = compute_composite_score(day_summaries)
    score_info["day_details"] = day_summaries
    return score_info


def run_optimization(max_trials=60, max_no_improve=15, verbose=True):
    """
    Main optimization loop.
    1. Evaluate baseline
    2. Random search phase
    3. Local refinement phase around best
    """
    base = get_default_params()
    ash_base = base["ASH_COATED_OSMIUM"]
    pepper_base = base["INTARIAN_PEPPER_ROOT"]

    results_log = []
    best_score = -float("inf")
    best_ash = dict(ash_base)
    best_pepper = dict(pepper_base)
    best_info = None
    no_improve_count = 0

    print("=" * 60)
    print("OPTIMIZATION START")
    print("=" * 60)

    # Phase 0: Baseline
    print("\n[Baseline evaluation]")
    info = evaluate_params(ash_base, pepper_base, verbose=verbose)
    best_score = info["score"]
    best_info = info
    results_log.append({"trial": 0, "type": "baseline", "score": info["score"],
                         "total_pnl": info["total_pnl"], "day_pnls": info["day_pnls"],
                         "ash_params": dict(ash_base), "pepper_params": dict(pepper_base)})
    print(f"  Baseline score: {info['score']:.2f}, PnL: {info['total_pnl']:.2f}, "
          f"days: {info['day_pnls']}")

    # Phase 1: Random search
    random_trials = max(max_trials // 2, 10)
    print(f"\n[Phase 1: Random search ({random_trials} trials)]")
    for trial in range(1, random_trials + 1):
        ash_cand = sample_params(ASH_SEARCH_SPACE, ash_base, mode="random")
        pepper_cand = sample_params(PEPPER_SEARCH_SPACE, pepper_base, mode="random")

        try:
            info = evaluate_params(ash_cand, pepper_cand)
        except Exception as e:
            print(f"  Trial {trial}: ERROR - {e}")
            continue

        results_log.append({"trial": trial, "type": "random", "score": info["score"],
                             "total_pnl": info["total_pnl"], "day_pnls": info["day_pnls"],
                             "ash_params": dict(ash_cand), "pepper_params": dict(pepper_cand)})

        if info["score"] > best_score:
            improvement = info["score"] - best_score
            best_score = info["score"]
            best_ash = dict(ash_cand)
            best_pepper = dict(pepper_cand)
            best_info = info
            no_improve_count = 0
            if verbose:
                print(f"  Trial {trial}: NEW BEST score={info['score']:.2f} (+{improvement:.2f}), "
                      f"PnL={info['total_pnl']:.2f}, days={info['day_pnls']}")
        else:
            no_improve_count += 1

        if no_improve_count >= max_no_improve:
            print(f"  Early stop after {no_improve_count} trials with no improvement.")
            break

    # Phase 2: Local refinement around best
    local_trials = max_trials - len(results_log)
    no_improve_count = 0
    print(f"\n[Phase 2: Local refinement ({local_trials} trials)]")
    for trial in range(1, local_trials + 1):
        ash_cand = local_refine(best_ash, ASH_SEARCH_SPACE, scale=0.5)
        pepper_cand = local_refine(best_pepper, PEPPER_SEARCH_SPACE, scale=0.5)

        try:
            info = evaluate_params(ash_cand, pepper_cand)
        except Exception as e:
            continue

        t_num = len(results_log)
        results_log.append({"trial": t_num, "type": "local", "score": info["score"],
                             "total_pnl": info["total_pnl"], "day_pnls": info["day_pnls"],
                             "ash_params": dict(ash_cand), "pepper_params": dict(pepper_cand)})

        if info["score"] > best_score:
            improvement = info["score"] - best_score
            best_score = info["score"]
            best_ash = dict(ash_cand)
            best_pepper = dict(pepper_cand)
            best_info = info
            no_improve_count = 0
            if verbose:
                print(f"  Local {trial}: NEW BEST score={info['score']:.2f} (+{improvement:.2f}), "
                      f"PnL={info['total_pnl']:.2f}, days={info['day_pnls']}")
        else:
            no_improve_count += 1

        if no_improve_count >= max_no_improve:
            print(f"  Early stop after {no_improve_count} local trials with no improvement.")
            break

    # Save results
    print(f"\n{'=' * 60}")
    print("OPTIMIZATION COMPLETE")
    print(f"{'=' * 60}")
    print(f"Best score: {best_score:.2f}")
    print(f"Best total PnL: {best_info['total_pnl']:.2f}")
    print(f"Day PnLs: {best_info['day_pnls']}")
    print(f"Max drawdown: {best_info['max_drawdown']:.2f}")
    print(f"\nBest ASH params: {json.dumps(best_ash, indent=2)}")
    print(f"\nBest PEPPER params: {json.dumps(best_pepper, indent=2)}")

    # Save to disk
    best_result = {
        "score": best_score,
        "info": best_info,
        "ash_params": best_ash,
        "pepper_params": best_pepper,
    }
    save_metrics(best_result, os.path.join(OUTPUT_DIR, "best_params", "best_params.json"))
    save_metrics(results_log, os.path.join(OUTPUT_DIR, "metrics", "optimization_log.json"))

    return best_ash, best_pepper, best_info, results_log


if __name__ == "__main__":
    run_optimization(max_trials=60, max_no_improve=12, verbose=True)
