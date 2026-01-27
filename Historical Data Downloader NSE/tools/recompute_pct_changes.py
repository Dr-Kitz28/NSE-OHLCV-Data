"""Recompute percentage change columns for NSE historical datasets.

This utility walks the P1/P2/P3 symbol folders under the Historical Data
Downloader workspace and refreshes the `pct_change` and `adj_pct_change`
columns for both the daily (`*_complete_with_pct.csv`) and hourly
(`*_complete_historical_1h_730days.csv`) datasets.

The script is idempotent and can be run daily after new data is
downloaded to backfill any missing percentage values.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import numpy as np
import pandas as pd

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
SYMBOL_BUCKETS = ("P1", "P2", "P3")
DAILY_SUFFIX = "_complete_with_pct.csv"
HOURLY_SUFFIX = "_complete_historical_1h_730days.csv"
PCT_COLUMNS = ("pct_change", "adj_pct_change")


@dataclass(slots=True)
class Dataset:
    csv_path: Path
    kind: str  # "daily" or "hourly"


def iter_symbol_folders(root: Path, buckets: Sequence[str]) -> Iterator[Path]:
    for bucket in buckets:
        bucket_path = root / bucket
        if not bucket_path.exists():
            continue
        for entry in bucket_path.iterdir():
            if entry.is_dir():
                yield entry


def collect_datasets(symbol_dir: Path) -> Iterator[Dataset]:
    base_name = symbol_dir.name.upper()
    daily_path = symbol_dir / f"{base_name}{DAILY_SUFFIX}"
    if daily_path.exists():
        yield Dataset(csv_path=daily_path, kind="daily")

    hourly_path = symbol_dir / f"{base_name}{HOURLY_SUFFIX}"
    if hourly_path.exists():
        yield Dataset(csv_path=hourly_path, kind="hourly")


def recompute_percentage_columns(dataset: Dataset, digits: int = 4) -> bool:
    df = pd.read_csv(dataset.csv_path, dtype=str)
    if df.empty:
        return False

    changed = False
    for close_col, pct_col in (("close", "pct_change"), ("adj_close", "adj_pct_change")):
        if close_col not in df.columns or pct_col not in df.columns:
            continue

        computed = _compute_pct_series(df[close_col])
        formatted = [_format_pct(value, digits) for value in computed]
        current = df[pct_col].fillna("").tolist()
        if current != formatted:
            df[pct_col] = formatted
            changed = True

    if not changed:
        return False

    dataset.csv_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.fillna("").to_csv(dataset.csv_path, index=False, lineterminator="\n")
    except PermissionError as exc:  # pragma: no cover - OS dependent
        print(f"WARNING: Skipped {dataset.csv_path} (permission denied: {exc})", file=sys.stderr)
        return False
    return True


def _compute_pct_series(close_series: pd.Series) -> pd.Series:
    close_numeric = pd.to_numeric(close_series, errors="coerce")
    if close_numeric.isna().all():
        return pd.Series([None] * len(close_numeric))

    prev = close_numeric.ffill().shift(1)
    pct = ((close_numeric - prev) / prev * 100).round(4)
    pct = pct.replace([np.inf, -np.inf], pd.NA)
    pct = pct.where(~(prev == 0), pd.NA)
    pct = pct.where(~close_numeric.isna(), pd.NA)
    return pct


def _format_pct(value: float | None, digits: int) -> str:
    if value is None or pd.isna(value):
        return ""
    formatted = f"{float(value):.{digits}f}"
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    if not formatted:
        formatted = "0"
    if "." not in formatted:
        formatted = f"{formatted}.0"
    return formatted


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recompute pct_change columns for NSE datasets.")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Root of the Historical Data Downloader workspace (default: script parent).",
    )
    parser.add_argument(
        "--buckets",
        nargs="*",
    default=list(SYMBOL_BUCKETS),
    help="Symbol buckets to process (default: P1 P2 P3).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N symbol folders (useful for smoke tests).",
    )
    parser.add_argument(
        "--digits",
        type=int,
        default=4,
        help="Number of decimal places to keep when formatting percentages.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-file logging output.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    if not root.exists():
        print(f"Root path {root} does not exist", file=sys.stderr)
        return 2

    symbol_dirs = list(iter_symbol_folders(root, args.buckets))
    symbol_dirs.sort()
    if args.limit is not None:
        symbol_dirs = symbol_dirs[: args.limit]

    total = len(symbol_dirs)
    if total == 0:
        print("No symbol folders found to process.")
        return 0

    updated = 0
    for index, symbol_dir in enumerate(symbol_dirs, start=1):
        datasets = list(collect_datasets(symbol_dir))
        if not datasets:
            continue

        for dataset in datasets:
            if recompute_percentage_columns(dataset, digits=args.digits):
                updated += 1
                if not args.quiet:
                    print(f"[{index}/{total}] Updated {dataset.kind} dataset -> {dataset.csv_path}")

    if not args.quiet:
        print(f"Processed {total} symbols; refreshed {updated} datasets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
