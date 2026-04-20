from __future__ import annotations

import argparse
import importlib.util
import math
import random
import shutil
import statistics
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import pandas as pd


ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / ".vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

import prosperity4bt.datamodel as backtest_datamodel

sys.modules["datamodel"] = backtest_datamodel

from prosperity4bt.data import LIMITS
from prosperity4bt.file_reader import FileSystemReader
from prosperity4bt.models import TradeMatchingMode
from prosperity4bt.runner import run_backtest


ASH = "ASH_COATED_OSMIUM"
PEPPER = "INTARIAN_PEPPER_ROOT"
ROUND_NUM = 2
DEFAULT_DAYS = (-1, 0, 1)
DEFAULT_ALGOS = ("310818.py", "310818_hybrid.py", "310818_research_v1.py")
BACKTEST_DATA_DIR = ROOT / "backtest_data"
ROUND_DATA_DIR = BACKTEST_DATA_DIR / f"round{ROUND_NUM}"

LIMITS.update({ASH: 80, PEPPER: 80})


def ensure_backtest_layout(days: Sequence[int]) -> None:
    ROUND_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for day in days:
        for prefix in ("prices", "trades"):
            source = ROOT / f"{prefix}_round_{ROUND_NUM}_day_{day}.csv"
            target = ROUND_DATA_DIR / source.name
            if source.exists() and not target.exists():
                shutil.copy2(source, target)


def load_algorithm(path: Path):
    module_name = path.stem + "_research_eval"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def final_rows(result):
    last_timestamp = result.activity_logs[-1].timestamp
    return [row for row in result.activity_logs if row.timestamp == last_timestamp]


def final_pnl(result) -> Dict[str, float]:
    return {row.columns[2]: float(row.columns[-1]) for row in final_rows(result)}


def total_pnl(result) -> float:
    return sum(final_pnl(result).values())


def average_pepper_position(result) -> float:
    own = [
        trade_row.trade
        for trade_row in result.trades
        if (trade_row.trade.buyer == "SUBMISSION" or trade_row.trade.seller == "SUBMISSION")
        and trade_row.trade.symbol == PEPPER
    ]

    position = 0
    carried = 0
    previous_timestamp = 0
    for trade in own:
        carried += position * (trade.timestamp - previous_timestamp)
        if trade.buyer == "SUBMISSION":
            position += trade.quantity
        else:
            position -= trade.quantity
        previous_timestamp = trade.timestamp

    carried += position * (999_900 - previous_timestamp)
    return carried / 999_900


def run_single(algo_path: Path, data_root: Path, days: Sequence[int]):
    reader = FileSystemReader(data_root)
    module = load_algorithm(algo_path)
    per_day = {}
    total = 0.0

    for day in days:
        result = run_backtest(
            module.Trader(),
            reader,
            ROUND_NUM,
            day,
            print_output=False,
            trade_matching_mode=TradeMatchingMode.all,
            no_names=True,
            show_progress_bar=False,
        )
        pnl = final_pnl(result)
        day_total = sum(pnl.values())
        per_day[day] = {
            "products": pnl,
            "total": day_total,
            "avg_pepper_pos": average_pepper_position(result),
        }
        total += day_total

    totals = [per_day[day]["total"] for day in days]
    return {
        "per_day": per_day,
        "total": total,
        "mean_holdout": statistics.mean(totals),
        "min_holdout": min(totals),
    }


def thin_quotes(days: Sequence[int], fraction: float, seed: int, output_root: Path) -> Path:
    rng = random.Random(seed)
    round_dir = output_root / f"round{ROUND_NUM}"
    round_dir.mkdir(parents=True, exist_ok=True)

    for day in days:
        prices = pd.read_csv(
            ROOT / f"prices_round_{ROUND_NUM}_day_{day}.csv",
            sep=";",
            dtype=str,
            keep_default_na=False,
        )
        trades = pd.read_csv(
            ROOT / f"trades_round_{ROUND_NUM}_day_{day}.csv",
            sep=";",
            dtype=str,
            keep_default_na=False,
        )

        price_timestamps = prices["timestamp"].astype(int)
        trade_timestamps = trades["timestamp"].astype(int)
        timestamps = sorted(price_timestamps.unique())
        keep = set()
        for idx, timestamp in enumerate(timestamps):
            if idx == 0 or rng.random() <= fraction:
                keep.add(timestamp)

        prices[price_timestamps.isin(keep)].to_csv(
            round_dir / f"prices_round_{ROUND_NUM}_day_{day}.csv",
            sep=";",
            index=False,
        )
        trades[trade_timestamps.isin(keep)].to_csv(
            round_dir / f"trades_round_{ROUND_NUM}_day_{day}.csv",
            sep=";",
            index=False,
        )

    return output_root


def quote_thinning_summary(algo_path: Path, days: Sequence[int], fractions: Sequence[float], seeds: int):
    base_total = run_single(algo_path, BACKTEST_DATA_DIR, days)["total"]
    summary = {}
    for fraction in fractions:
        stressed = []
        increments = []
        for seed in range(seeds):
            with tempfile.TemporaryDirectory() as tmpdir:
                thin_root = thin_quotes(days, fraction, seed, Path(tmpdir))
                thin_total = run_single(algo_path, thin_root, days)["total"]
            stressed.append(thin_total)
            increments.append(base_total - thin_total)
        summary[fraction] = {
            "stressed_mean": statistics.mean(stressed),
            "increment_mean": statistics.mean(increments),
            "increment_samples": increments,
        }
    return summary


def recommend_maf(algo_path: Path, days: Sequence[int], fractions: Sequence[float], seeds: int):
    scenario_values = []
    for holdout in days:
        train_days = [day for day in days if day != holdout]
        full_total = run_single(algo_path, BACKTEST_DATA_DIR, train_days)["total"]
        for fraction in fractions:
            for seed in range(seeds):
                with tempfile.TemporaryDirectory() as tmpdir:
                    thin_root = thin_quotes(train_days, fraction, seed, Path(tmpdir))
                    thin_total = run_single(algo_path, thin_root, train_days)["total"]
                scenario_values.append(full_total - thin_total)

    if not scenario_values:
        return {"recommended": 0, "mean": 0.0, "q35": 0.0, "values": []}

    mean_value = statistics.mean(scenario_values)
    sorted_values = sorted(scenario_values)
    q35_idx = max(0, min(len(sorted_values) - 1, int(math.floor(0.35 * (len(sorted_values) - 1)))))
    q35 = sorted_values[q35_idx]
    recommended = max(0, int(math.floor(min(q35, 0.60 * mean_value))))
    return {
        "recommended": recommended,
        "mean": mean_value,
        "q35": q35,
        "values": scenario_values,
    }


def print_raw(results):
    print("Raw visible-day backtests")
    for algo_name, summary in results.items():
        print(f" {algo_name}: total={summary['total']:.1f} mean_holdout={summary['mean_holdout']:.1f} min_holdout={summary['min_holdout']:.1f}")
        for day in sorted(summary["per_day"]):
            row = summary["per_day"][day]
            ash = row["products"].get(ASH, 0.0)
            pepper = row["products"].get(PEPPER, 0.0)
            print(
                f"  day {day:>2}: {ASH}={ash:>10.1f} {PEPPER}={pepper:>10.1f} "
                f"total={row['total']:>10.1f} avg_pepper_pos={row['avg_pepper_pos']:>7.2f}"
            )


def print_thinning(algo_name: str, summary):
    print(f"\nQuote-thinning stress for {algo_name}")
    for fraction in sorted(summary):
        row = summary[fraction]
        print(
            f" {fraction:.0%} quotes: stressed_mean={row['stressed_mean']:.1f} "
            f"increment_mean={row['increment_mean']:.1f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Research evaluation harness for Prosperity Round 2.")
    parser.add_argument("--algos", nargs="*", default=list(DEFAULT_ALGOS))
    parser.add_argument("--days", nargs="*", type=int, default=list(DEFAULT_DAYS))
    parser.add_argument("--fractions", nargs="*", type=float, default=[0.60, 0.75, 0.90])
    parser.add_argument("--seeds", type=int, default=8)
    parser.add_argument("--skip-thinning", action="store_true")
    parser.add_argument("--skip-maf", action="store_true")
    args = parser.parse_args()

    ensure_backtest_layout(args.days)
    algo_paths = [ROOT / algo for algo in args.algos if (ROOT / algo).exists()]

    results = {path.name: run_single(path, BACKTEST_DATA_DIR, args.days) for path in algo_paths}
    print_raw(results)

    if not args.skip_thinning:
        for path in algo_paths:
            thinning = quote_thinning_summary(path, args.days, args.fractions, args.seeds)
            print_thinning(path.name, thinning)

    if not args.skip_maf:
        print("\nMAF recommendations")
        for path in algo_paths:
            maf = recommend_maf(path, args.days, args.fractions, args.seeds)
            print(
                f" {path.name}: recommended={maf['recommended']} "
                f"mean_value={maf['mean']:.1f} q35={maf['q35']:.1f}"
            )


if __name__ == "__main__":
    main()
