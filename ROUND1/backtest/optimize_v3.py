"""
Third-round ultra-fine optimization. Tests individual parameter sensitivity
and explores the neighborhood of the current best more finely.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.optimize import evaluate_params
from backtest.metrics import save_metrics
from utils.constants import OUTPUT_DIR


def fine_tune(base_ash, base_pepper, verbose=True):
    """Ultra-fine parameter tuning with sensitivity analysis."""

    ash_fine = {
        "ewma_alpha": [0.04, 0.05, 0.06, 0.07, 0.08, 0.10],
        "take_threshold": [0.0, 0.25, 0.5, 0.75, 1.0],
        "make_width": [5, 5.5, 6, 6.5, 7, 7.5, 8],
        "inventory_skew_factor": [0.0, 0.02, 0.05, 0.08, 0.1],
        "max_passive_size": [30, 35, 37, 40, 45, 50],
        "max_take_size": [10, 12, 15, 18, 20, 25],
        "imbalance_edge": [0.0, 0.5, 0.75, 1.0, 1.5],
    }
    pepper_fine = {
        "trend_rate": [0.099, 0.1, 0.1002, 0.101, 0.102, 0.103, 0.105, 0.107],
        "ewma_alpha_base": [0.005, 0.006, 0.008, 0.01, 0.015, 0.02],
        "take_threshold": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
        "make_width": [3, 4, 5, 6, 7],
        "inventory_skew_factor": [0.0, 0.02, 0.05, 0.1],
        "max_passive_size": [15, 20, 25, 30],
        "max_take_size": [10, 12, 15, 18, 20],
        "directional_skew": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
        "residual_zscore_threshold": [0.5, 0.75, 1.0, 1.5, 2.0, 3.0],
    }

    best_ash = dict(base_ash)
    best_pepper = dict(base_pepper)
    info = evaluate_params(best_ash, best_pepper)
    best_score = info["score"]
    print(f"Starting: score={best_score:.2f}, PnL={info['total_pnl']:.2f}")

    # Sensitivity report
    sensitivity = {"ASH": {}, "PEPPER": {}}

    # ASH sensitivity
    print("\n=== ASH SENSITIVITY ===")
    for param, values in ash_fine.items():
        scores = []
        for val in values:
            cand = dict(best_ash)
            cand[param] = val
            try:
                info = evaluate_params(cand, best_pepper)
                scores.append((val, info["score"], info["total_pnl"]))
                if info["score"] > best_score:
                    imp = info["score"] - best_score
                    best_score = info["score"]
                    best_ash = dict(cand)
                    print(f"  IMPROVED: {param}={val}: +{imp:.2f}")
            except:
                pass
        sensitivity["ASH"][param] = scores
        best_for_param = max(scores, key=lambda x: x[1]) if scores else None
        worst_for_param = min(scores, key=lambda x: x[1]) if scores else None
        if best_for_param and worst_for_param:
            spread = best_for_param[1] - worst_for_param[1]
            print(f"  {param}: range={spread:.0f}, best={best_for_param[0]} (score={best_for_param[1]:.0f}), "
                  f"worst={worst_for_param[0]} (score={worst_for_param[1]:.0f})")

    # PEPPER sensitivity
    print("\n=== PEPPER SENSITIVITY ===")
    for param, values in pepper_fine.items():
        scores = []
        for val in values:
            cand = dict(best_pepper)
            cand[param] = val
            try:
                info = evaluate_params(best_ash, cand)
                scores.append((val, info["score"], info["total_pnl"]))
                if info["score"] > best_score:
                    imp = info["score"] - best_score
                    best_score = info["score"]
                    best_pepper = dict(cand)
                    print(f"  IMPROVED: {param}={val}: +{imp:.2f}")
            except:
                pass
        sensitivity["PEPPER"][param] = scores
        best_for_param = max(scores, key=lambda x: x[1]) if scores else None
        worst_for_param = min(scores, key=lambda x: x[1]) if scores else None
        if best_for_param and worst_for_param:
            spread = best_for_param[1] - worst_for_param[1]
            print(f"  {param}: range={spread:.0f}, best={best_for_param[0]} (score={best_for_param[1]:.0f}), "
                  f"worst={worst_for_param[0]} (score={worst_for_param[1]:.0f})")

    # Final
    final_info = evaluate_params(best_ash, best_pepper, verbose=verbose)
    print(f"\nFinal: score={final_info['score']:.2f}, PnL={final_info['total_pnl']:.2f}")
    print(f"Days: {final_info['day_pnls']}")
    print(f"\nBest ASH: {json.dumps(best_ash, indent=2)}")
    print(f"Best PEPPER: {json.dumps(best_pepper, indent=2)}")

    result = {
        "score": final_info["score"],
        "info": final_info,
        "ash_params": best_ash,
        "pepper_params": best_pepper,
        "sensitivity": {k: {p: [(v, s, pnl) for v, s, pnl in vals] for p, vals in prods.items()} for k, prods in sensitivity.items()},
    }
    save_metrics(result, os.path.join(OUTPUT_DIR, "best_params", "best_params.json"))
    save_metrics(sensitivity, os.path.join(OUTPUT_DIR, "metrics", "sensitivity.json"))
    return best_ash, best_pepper


if __name__ == "__main__":
    bp_path = os.path.join(OUTPUT_DIR, "best_params", "best_params.json")
    with open(bp_path) as f:
        bp = json.load(f)
    fine_tune(bp["ash_params"], bp["pepper_params"])
