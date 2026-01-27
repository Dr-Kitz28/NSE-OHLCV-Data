"""Data quality validation tool for NSE symbol datasets.

Run this script after refreshing the raw CSVs to automatically flag
schema and data integrity issues.  The script exits with a non-zero
status when any blocking errors are detected so it can gate automated
pipelines.
"""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from statistics_engine import DatasetBundle, load_symbol_datasets
from normalise_data import normalise_dataframe


REQUIRED_PRICE_COLUMNS = ["open", "high", "low", "close", "adj_close"]
REQUIRED_DAILY_COLUMNS = REQUIRED_PRICE_COLUMNS + ["volume", "pct_change", "date"]
REQUIRED_HOURLY_COLUMNS = REQUIRED_PRICE_COLUMNS + ["volume", "pct_change", "date"]


@dataclass
class ValidationIssue:
    level: str
    message: str


@dataclass
class SymbolReport:
    symbol: str
    bucket: str
    daily_issues: List[ValidationIssue] = field(default_factory=list)
    hourly_issues: List[ValidationIssue] = field(default_factory=list)
    cross_issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(issue.level == "ERROR" for issue in self.all_issues)

    @property
    def all_issues(self) -> List[ValidationIssue]:
        return self.daily_issues + self.hourly_issues + self.cross_issues

    def record(self, scope: str, level: str, message: str) -> None:
        issue = ValidationIssue(level=level, message=message)
        if scope == "daily":
            self.daily_issues.append(issue)
        elif scope == "hourly":
            self.hourly_issues.append(issue)
        else:
            self.cross_issues.append(issue)


def iter_symbol_dirs(root: Path, buckets: Sequence[str]) -> Iterator[Tuple[str, Path]]:
    for bucket in buckets:
        bucket_dir = root / bucket
        if not bucket_dir.exists():
            continue
        for child in sorted(bucket_dir.iterdir()):
            if child.is_dir():
                yield bucket, child


def validate_required_columns(report: SymbolReport, scope: str, frame: pd.DataFrame, required: Sequence[str]) -> None:
    missing = [col for col in required if col not in frame.columns]
    if missing:
        report.record(scope, "ERROR", f"Missing columns: {', '.join(missing)}")


def validate_price_relationships(report: SymbolReport, scope: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        report.record(scope, "ERROR", "Dataframe is empty")
        return

    for col in REQUIRED_PRICE_COLUMNS:
        if col in frame.columns and frame[col].isna().all():
            report.record(scope, "ERROR", f"Column '{col}' is entirely NaN")
        if col in frame.columns and (frame[col] <= 0).any():
            report.record(scope, "ERROR", f"Column '{col}' contains non-positive values")

    if not set(REQUIRED_PRICE_COLUMNS[:4]).issubset(frame.columns):
        return

    highs = frame["high"]
    lows = frame["low"]
    opens = frame["open"]
    closes = frame["close"]

    if not ((highs >= opens) & (highs >= closes) & (highs >= lows)).all():
        report.record(scope, "ERROR", "'high' values violate OHLC relationship")
    if not ((lows <= opens) & (lows <= closes) & (lows <= highs)).all():
        report.record(scope, "ERROR", "'low' values violate OHLC relationship")


def validate_pct_change(report: SymbolReport, scope: str, frame: pd.DataFrame, column: str = "pct_change") -> None:
    if column not in frame.columns or "adj_close" not in frame.columns:
        return

    # The `pct_change` as it exists in the file.
    observed_pct_change = frame[column]

    # To validate, we re-calculate pct_change from the 'adj_close' in the file.
    # First, apply the standard cleaning logic to a temporary copy of the 'adj_close' series.
    adj_close_cleaned = frame["adj_close"].copy()
    adj_close_cleaned[adj_close_cleaned <= 0] = pd.NA
    adj_close_cleaned = adj_close_cleaned.ffill()
    adj_close_cleaned = adj_close_cleaned.bfill()
    adj_close_cleaned = adj_close_cleaned.fillna(0)

    # CRITICAL: Round the cleaned data to match the precision used during generation.
    adj_close_cleaned = adj_close_cleaned.round(4)

    # Now, calculate the expected pct_change from this cleaned and rounded series.
    try:
        expected_pct_change = adj_close_cleaned.pct_change(fill_method=None) * 100
    except TypeError:  # pandas < 2.1 compatibility
        expected_pct_change = adj_close_cleaned.pct_change() * 100

    # Compare the two series.
    diff = (
        pd.to_numeric(observed_pct_change, errors="coerce") - 
        pd.to_numeric(expected_pct_change, errors="coerce")
    ).abs()
    
    # The first row will always be NaN, which is expected.
    # We also allow for very small floating point differences.
    mismatched = diff[diff > 0.0001].dropna()
    
    if not mismatched.empty:
        sample_diff = mismatched.iloc[0]
        report.record(scope, "ERROR", f"{column} deviates from computed values (sample diff={sample_diff:.4f})")


def validate_volume(report: SymbolReport, scope: str, frame: pd.DataFrame) -> None:
    if "volume" in frame.columns and (frame["volume"] < 0).any():
        report.record(scope, "ERROR", "Volume contains negative values")


def validate_datetimes(report: SymbolReport, scope: str, frame: pd.DataFrame) -> None:
    if "date" not in frame.columns:
        return

    if not pd.api.types.is_datetime64_any_dtype(frame["date"]):
        report.record(scope, "ERROR", "'date' column is not datetime")
        return

    if frame["date"].isna().any():
        report.record(scope, "ERROR", "'date' column contains NaT")

    if not frame["date"].is_monotonic_increasing:
        report.record(scope, "ERROR", "'date' column is not sorted ascending")

    duplicated = frame["date"].duplicated()
    if duplicated.any():
        report.record(scope, "ERROR", f"Duplicate timestamps detected ({duplicated.sum()} rows)")


def validate_daily_frame(report: SymbolReport, frame: Optional[pd.DataFrame]) -> None:
    if frame is None:
        report.record("daily", "ERROR", "Daily dataset missing")
        return

    validate_required_columns(report, "daily", frame, REQUIRED_DAILY_COLUMNS)
    validate_datetimes(report, "daily", frame)
    validate_price_relationships(report, "daily", frame)
    validate_pct_change(report, "daily", frame, "pct_change")
    validate_volume(report, "daily", frame)


def validate_hourly_frame(report: SymbolReport, frame: Optional[pd.DataFrame]) -> None:
    if frame is None:
        report.record("hourly", "ERROR", "Hourly dataset missing")
        return

    validate_required_columns(report, "hourly", frame, REQUIRED_HOURLY_COLUMNS)
    validate_datetimes(report, "hourly", frame)
    validate_price_relationships(report, "hourly", frame)
    validate_pct_change(report, "hourly", frame, "pct_change")
    validate_volume(report, "hourly", frame)


def _daily_calendar(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty or "date" not in df.columns:
        return pd.Series(dtype="datetime64[ns]")
    return df["date"].dt.normalize()


def cross_validate(report: SymbolReport, daily_df: Optional[pd.DataFrame], hourly_df: Optional[pd.DataFrame]) -> None:
    if daily_df is None or hourly_df is None or daily_df.empty or hourly_df.empty:
        return

    hourly = hourly_df.copy()
    hourly["date_local"] = hourly["date"].dt.tz_convert("Asia/Kolkata") if hourly["date"].dt.tz is not None else hourly["date"].dt.tz_localize("Asia/Kolkata")
    hourly["trade_date"] = hourly["date_local"].dt.normalize()

    daily = daily_df.copy()
    daily["trade_date"] = pd.to_datetime(daily["date"]).dt.normalize()

    merged_dates = sorted(set(daily["trade_date"]).intersection(hourly["trade_date"]))
    tolerance_abs = 0.5
    tolerance_pct = 0.02  # 2%

    for trade_date in merged_dates[-30:]:  # focus on most recent month for performance
        daily_row = daily[daily["trade_date"] == trade_date]
        hourly_slice = hourly[hourly["trade_date"] == trade_date]
        if daily_row.empty or hourly_slice.empty:
            continue

        daily_row = daily_row.iloc[0]
        hourly_sorted = hourly_slice.sort_values(by="date")
        first_open = hourly_sorted["open"].iloc[0]
        last_close = hourly_sorted["close"].iloc[-1]
        volume_sum = hourly_sorted["volume"].sum()

        def _check(metric: str, observed: float, expected: float) -> None:
            if math.isfinite(observed) and math.isfinite(expected):
                if abs(observed - expected) > max(tolerance_abs, tolerance_pct * max(abs(expected), 1.0)):
                    report.record(
                        "cross",
                        "ERROR",
                        f"{metric} mismatch on {trade_date.date()}: daily={observed:.2f}, hourly-derived={expected:.2f}",
                    )

        _check("Open", float(daily_row["open"]), float(first_open))
        _check("Close", float(daily_row["close"]), float(last_close))
        _check("Volume", float(daily_row["volume"]), float(volume_sum))


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate NSE symbol datasets for schema and data quality issues")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path.cwd(),
    help="Path to the data root that contains the P1/P2/P3 subdirectories (default: current working directory)",
    )
    parser.add_argument(
        "--buckets",
        nargs="*",
    default=["P1", "P2", "P3"],
    help="Buckets to validate (default: P1, P2, P3)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of symbols per bucket (useful for smoke tests)",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Explicit list of symbols to validate",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    reports: List[SymbolReport] = []
    errors = 0

    for bucket, symbol_dir in iter_symbol_dirs(args.data_root, args.buckets):
        symbol = symbol_dir.name
        if args.symbols and symbol not in args.symbols:
            continue
        if args.limit is not None and len([r for r in reports if r.bucket == bucket]) >= args.limit:
            continue

        bundle = load_symbol_datasets(symbol_dir)
        report = SymbolReport(symbol=symbol, bucket=bucket)
        validate_daily_frame(report, bundle.daily)
        validate_hourly_frame(report, bundle.hourly)
        cross_validate(report, bundle.daily, bundle.hourly)
        reports.append(report)

        if report.all_issues:
            print(f"[{bucket}] {symbol}")
            for issue in report.daily_issues:
                print(f"  [daily][{issue.level}] {issue.message}")
            for issue in report.hourly_issues:
                print(f"  [hourly][{issue.level}] {issue.message}")
            for issue in report.cross_issues:
                print(f"  [cross][{issue.level}] {issue.message}")
            if report.has_errors:
                errors += 1

    total = len(reports)
    error_symbols = sum(1 for r in reports if r.has_errors)
    warning_symbols = sum(1 for r in reports if r.all_issues and not r.has_errors)

    print("\nValidation summary")
    print("===================")
    print(f"Symbols checked : {total}")
    print(f"Errors detected  : {error_symbols}")
    print(f"Warnings detected: {warning_symbols}")

    if errors:
        print("Blocking issues detected. See logs above.")
        return 1

    print("All selected datasets passed validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
