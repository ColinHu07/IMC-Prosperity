"""
Final side-by-side comparison: baseline vs phase1 vs phase2 vs phase3+4.

Loads the saved benchmark JSONs and prints a single table.
"""
import json
import os

_ROUND1 = os.path.dirname(os.path.abspath(__file__))
METRICS = os.path.join(_ROUND1, "output", "metrics")

LABELS = [
    ("phase0_baseline", "Phase 0 (baseline)"),
    ("phase1", "Phase 1 (safety)"),
    ("phase2_final", "Phase 2 (signals)"),
    ("phase3", "Phase 3 (adaptive)"),
]


def _load(label):
    with open(os.path.join(METRICS, f"benchmark_{label}.json")) as f:
        return json.load(f)


def main():
    print(f"\n{'='*92}")
    print(f"  {'Configuration':<22}{'Total (opt)':>13}{'Total (pes)':>13}"
          f"{'Worst-day (pes)':>18}{'Max DD':>10}{'Avg Inv':>10}")
    print(f"  {'-'*22}{'-'*13}{'-'*13}{'-'*18}{'-'*10}{'-'*10}")

    first_worst = None
    for label, name in LABELS:
        try:
            data = _load(label)
        except FileNotFoundError:
            continue
        opt = data["optimistic"]
        pes = data["pessimistic"]
        worst = pes["min_day_pnl"]
        if first_worst is None:
            first_worst = worst
            delta = ""
        else:
            diff = worst - first_worst
            pct = diff / first_worst * 100
            delta = f"  ({diff:+,.0f}/{pct:+.1f}%)"
        print(f"  {name:<22}{opt['total_pnl']:>13,.0f}{pes['total_pnl']:>13,.0f}"
              f"{worst:>13,.2f}{delta:<20}{pes['max_drawdown']:>10,.0f}"
              f"{pes['avg_inventory']:>10.2f}")

    print(f"{'='*92}\n")


if __name__ == "__main__":
    main()
