"""
Second-round focused optimization around the best parameters found.
Tighter search with coordinate descent approach.
"""
import sys, os, json, random, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.optimize import evaluate_params
from backtest.metrics import save_metrics
from utils.constants import OUTPUT_DIR


def coordinate_search(base_ash, base_pepper, max_trials=80, verbose=True):
    """Optimize one parameter at a time, cycling through all."""

    # Fine-grained search ranges around best
    ash_tweaks = {
        "ewma_alpha": [0.03, 0.04, 0.05, 0.06, 0.07, 0.08],
        "take_threshold": [0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
        "make_width": [3, 3.5, 4, 4.5, 5, 5.5, 6],
        "inventory_skew_factor": [0.0, 0.05, 0.1, 0.125, 0.15, 0.2, 0.3],
        "max_passive_size": [25, 30, 35, 37, 40, 45, 50],
        "imbalance_edge": [0.0, 0.5, 1.0, 1.5, 2.0],
    }
    pepper_tweaks = {
        "trend_rate": [0.098, 0.099, 0.1, 0.1002, 0.101, 0.103, 0.105],
        "ewma_alpha_base": [0.001, 0.002, 0.0025, 0.003, 0.005, 0.008],
        "take_threshold": [2.0, 3.0, 3.5, 4.0, 4.5, 5.0],
        "make_width": [3, 4, 5, 6],
        "inventory_skew_factor": [0.0, 0.05, 0.1, 0.15],
        "directional_skew": [1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
        "residual_zscore_threshold": [0.5, 0.75, 1.0, 1.5, 2.0],
    }

    best_ash = dict(base_ash)
    best_pepper = dict(base_pepper)

    info = evaluate_params(best_ash, best_pepper)
    best_score = info["score"]
    print(f"Starting score: {best_score:.2f}, PnL: {info['total_pnl']:.2f}")

    trial_count = 0
    improved = True
    cycle = 0

    while improved and trial_count < max_trials:
        improved = False
        cycle += 1
        if verbose:
            print(f"\n--- Cycle {cycle} ---")

        # Optimize ASH parameters
        for param, values in ash_tweaks.items():
            for val in values:
                trial_count += 1
                if trial_count > max_trials:
                    break
                cand = dict(best_ash)
                cand[param] = val
                try:
                    info = evaluate_params(cand, best_pepper)
                except:
                    continue
                if info["score"] > best_score:
                    imp = info["score"] - best_score
                    best_score = info["score"]
                    best_ash = dict(cand)
                    improved = True
                    if verbose:
                        print(f"  ASH {param}={val}: score={info['score']:.2f} (+{imp:.2f}), PnL={info['total_pnl']:.2f}")

        # Optimize PEPPER parameters
        for param, values in pepper_tweaks.items():
            for val in values:
                trial_count += 1
                if trial_count > max_trials:
                    break
                cand = dict(best_pepper)
                cand[param] = val
                try:
                    info = evaluate_params(best_ash, cand)
                except:
                    continue
                if info["score"] > best_score:
                    imp = info["score"] - best_score
                    best_score = info["score"]
                    best_pepper = dict(cand)
                    improved = True
                    if verbose:
                        print(f"  PEPPER {param}={val}: score={info['score']:.2f} (+{imp:.2f}), PnL={info['total_pnl']:.2f}")

    # Final eval
    final_info = evaluate_params(best_ash, best_pepper, verbose=verbose)
    print(f"\nFinal score: {final_info['score']:.2f}, PnL: {final_info['total_pnl']:.2f}")
    print(f"Day PnLs: {final_info['day_pnls']}")
    print(f"\nBest ASH: {json.dumps(best_ash, indent=2)}")
    print(f"Best PEPPER: {json.dumps(best_pepper, indent=2)}")

    # Save
    result = {
        "score": final_info["score"],
        "info": final_info,
        "ash_params": best_ash,
        "pepper_params": best_pepper,
    }
    save_metrics(result, os.path.join(OUTPUT_DIR, "best_params", "best_params.json"))
    return best_ash, best_pepper, final_info


if __name__ == "__main__":
    # Load current best
    bp_path = os.path.join(OUTPUT_DIR, "best_params", "best_params.json")
    with open(bp_path) as f:
        bp = json.load(f)

    coordinate_search(bp["ash_params"], bp["pepper_params"], max_trials=200, verbose=True)
