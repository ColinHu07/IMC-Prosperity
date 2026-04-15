"""
Validate the final trader.py parameters match what we backtested.
Run a backtest using the exact same parameters as trader.py to confirm the numbers.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategy.ash_osmium import AshOsmiumStrategy
from strategy.pepper_root import PepperRootStrategy
from backtest.replay_engine import ReplayEngine
from backtest.metrics import compute_composite_score
from utils.constants import DAYS

# These must exactly match trader.py
ASH_PARAMS = {
    "ewma_alpha": 0.06,
    "take_threshold": 0.5,
    "make_width": 7,
    "inventory_skew_factor": 0.02,
    "max_passive_size": 38,
    "max_take_size": 10,
    "position_limit": 50,
    "imbalance_edge": 1.0,
}

PEPPER_PARAMS = {
    "trend_rate": 0.1002,
    "ewma_alpha_base": 0.02,
    "take_threshold": 2.0,
    "make_width": 5,
    "inventory_skew_factor": 0.0,
    "max_passive_size": 20,
    "max_take_size": 15,
    "position_limit": 50,
    "directional_skew": 1.5,
    "residual_zscore_threshold": 1.0,
    "trend_ewma_alpha": 0.001,
}

def main():
    strategies = {
        "ASH_COATED_OSMIUM": AshOsmiumStrategy(ASH_PARAMS),
        "INTARIAN_PEPPER_ROOT": PepperRootStrategy(PEPPER_PARAMS),
    }
    engine = ReplayEngine(strategies)

    print("FINAL VALIDATION: trader.py parameters")
    print("=" * 60)

    day_summaries = []
    for day in DAYS:
        m = engine.run_day(day)
        s = m.get_summary()
        day_summaries.append(s)
        print(f"\nDay {day:+d}:")
        for prod, ps in s.items():
            if prod == "TOTAL":
                print(f"  {prod}: PnL={ps['final_pnl']:.2f}, MaxDD={ps['max_drawdown']:.2f}")
            else:
                print(f"  {prod}: PnL={ps['final_pnl']:.2f}, MaxDD={ps['max_drawdown']:.2f}, "
                      f"Fills={ps['fill_count']}, MaxPos={ps['max_position']}, "
                      f"AvgPos={ps['avg_abs_position']:.1f}, EndPos={ps['final_position']}")

    score_info = compute_composite_score(day_summaries)
    print(f"\n{'=' * 60}")
    print(f"FINAL SCORE: {score_info['score']:.2f}")
    print(f"TOTAL PnL:   {score_info['total_pnl']:.2f}")
    print(f"Day PnLs:    {score_info['day_pnls']}")
    print(f"Max DD:      {score_info['max_drawdown']:.2f}")
    print(f"Consistency: {score_info['min_day_pnl']:.2f}")

    print(f"\nASH params: {json.dumps(ASH_PARAMS, indent=2)}")
    print(f"\nPEPPER params: {json.dumps(PEPPER_PARAMS, indent=2)}")

if __name__ == "__main__":
    main()
