from __future__ import annotations

import argparse
import random
import shutil
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import pandas as pd


ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / ".vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

from prosperity4bt.data import LIMITS
from prosperity4bt.file_reader import FileSystemReader
from prosperity4bt.models import TradeMatchingMode
from prosperity4bt.runner import run_backtest

from round2_trader import ASH_COATED_OSMIUM, INTARIAN_PEPPER_ROOT, Trader


LIMITS.update(
    {
        ASH_COATED_OSMIUM: 80,
        INTARIAN_PEPPER_ROOT: 80,
    }
)

ROUND_NUM = 2
DEFAULT_DAYS = (-1, 0, 1)
BACKTEST_DATA_DIR = ROOT / "backtest_data"
ROUND_DATA_DIR = BACKTEST_DATA_DIR / f"round{ROUND_NUM}"


def ensure_backtest_layout(days: Sequence[int]) -> None:
    ROUND_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for day in days:
        for prefix in ("prices", "trades"):
            source = ROOT / f"{prefix}_round_{ROUND_NUM}_day_{day}.csv"
            target = ROUND_DATA_DIR / source.name
            if source.exists() and not target.exists():
                shutil.copy2(source, target)


def pnl_by_product(result) -> Dict[str, float]:
    last_timestamp = result.activity_logs[-1].timestamp
    return {
        row.columns[2]: float(row.columns[-1])
        for row in result.activity_logs
        if row.timestamp == last_timestamp
    }


def run_days(data_root: Path, days: Sequence[int]) -> Tuple[Dict[int, Dict[str, float]], float]:
    reader = FileSystemReader(data_root)
    per_day: Dict[int, Dict[str, float]] = {}
    total = 0.0

    for day in days:
        result = run_backtest(
            Trader(),
            reader,
            ROUND_NUM,
            day,
            print_output=False,
            trade_matching_mode=TradeMatchingMode.all,
            no_names=True,
            show_progress_bar=False,
        )
        summary = pnl_by_product(result)
        per_day[day] = summary
        total += sum(summary.values())

    return per_day, total


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

        thinned_prices = prices[price_timestamps.isin(keep)].copy()
        thinned_trades = trades[trade_timestamps.isin(keep)].copy()

        thinned_prices.to_csv(round_dir / f"prices_round_{ROUND_NUM}_day_{day}.csv", sep=";", index=False)
        thinned_trades.to_csv(round_dir / f"trades_round_{ROUND_NUM}_day_{day}.csv", sep=";", index=False)

    return output_root


def estimate_maf(days: Sequence[int], quote_fraction: float, seeds: int) -> Tuple[float, List[float]]:
    _, full_total = run_days(BACKTEST_DATA_DIR, days)
    increments: List[float] = []

    for seed in range(seeds):
        with tempfile.TemporaryDirectory() as tmpdir:
            thin_root = thin_quotes(days, fraction=quote_fraction, seed=seed, output_root=Path(tmpdir))
            _, thin_total = run_days(thin_root, days)
            increments.append(full_total - thin_total)

    return full_total, increments


def print_backtest(per_day: Dict[int, Dict[str, float]], total: float) -> None:
    print("Backtest summary")
    for day in sorted(per_day):
        ash = per_day[day].get(ASH_COATED_OSMIUM, 0.0)
        pepper = per_day[day].get(INTARIAN_PEPPER_ROOT, 0.0)
        day_total = ash + pepper
        print(
            f" day {day:>2}: "
            f"{ASH_COATED_OSMIUM}={ash:>10.1f}  "
            f"{INTARIAN_PEPPER_ROOT}={pepper:>10.1f}  "
            f"total={day_total:>10.1f}"
        )
    print(f" overall: {total:,.1f}")


def print_maf_estimate(full_total: float, increments: Iterable[float], quote_fraction: float) -> None:
    increments = list(increments)
    expected_increment = statistics.mean(increments) if increments else 0.0
    conservative_bid = max(0, int(round(0.60 * expected_increment)))
    break_even_bid = max(0, int(round(expected_increment)))

    print()
    print(f"MAF simulation against {quote_fraction:.0%} quote access")
    print(f" full-access backtest total: {full_total:,.1f}")
    if increments:
        print(f" incremental values: {[round(x, 1) for x in increments]}")
        print(f" mean incremental value: {expected_increment:,.1f}")
        print(f" break-even MAF ceiling: {break_even_bid:,}")
        print(f" conservative submission MAF: {conservative_bid:,}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest and estimate MAF for Prosperity Round 2.")
    parser.add_argument("--days", nargs="*", type=int, default=list(DEFAULT_DAYS))
    parser.add_argument("--quote-fraction", type=float, default=0.75)
    parser.add_argument("--maf-seeds", type=int, default=5)
    parser.add_argument("--skip-maf", action="store_true")
    args = parser.parse_args()

    ensure_backtest_layout(args.days)

    per_day, total = run_days(BACKTEST_DATA_DIR, args.days)
    print_backtest(per_day, total)

    if not args.skip_maf:
        full_total, increments = estimate_maf(
            days=args.days,
            quote_fraction=args.quote_fraction,
            seeds=args.maf_seeds,
        )
        print_maf_estimate(full_total, increments, args.quote_fraction)


if __name__ == "__main__":
    main()
