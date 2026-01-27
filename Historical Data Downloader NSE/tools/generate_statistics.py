"""CLI entrypoint to generate statistics for all NSE symbols."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, Iterator, List

from statistics_engine import (
    DatasetBundle,
    load_symbol_datasets,
    summarise_statistics,
    process_symbol_path,
)
import concurrent.futures
import os


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_ROOT = REPO_ROOT
DEFAULT_BUCKETS = ("P1", "P2", "P3")
DEFAULT_STATS_ROOT = (REPO_ROOT.parent / "Cleaning Data" / "Statistics").resolve()


def iter_symbol_dirs(root: Path, buckets: Iterable[str]) -> Iterator[tuple[str, Path]]:
    for bucket in buckets:
        bucket_path = root / bucket
        if not bucket_path.exists():
            continue
        for entry in sorted(bucket_path.iterdir()):
            if entry.is_dir():
                yield bucket, entry


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate statistics artifacts for NSE datasets.")
    parser.add_argument("--root", type=Path, default=DEFAULT_DATA_ROOT, help="Root containing the P1/P2/P3 symbol folders.")
    parser.add_argument(
        "--stats-root",
        type=Path,
        default=DEFAULT_STATS_ROOT,
        help="Directory where per-symbol statistics should be written (default: sibling 'Cleaning Data').",
    )
    parser.add_argument("--buckets", nargs="*", default=list(DEFAULT_BUCKETS), help="Symbol buckets to process (default: P1 P2 P3).")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N symbols.")
    parser.add_argument("--symbols", nargs="*", default=None, help="Explicit list of symbols to process (overrides --limit).")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-symbol detail messages.")
    parser.add_argument("--workers", type=int, default=None, help="Number of worker processes to use (default: all CPUs).")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)

    data_root = args.root.resolve()
    stats_root = args.stats_root.resolve()

    if not data_root.exists():
        print(f"Data root '{data_root}' does not exist.", file=sys.stderr)
        return 2

    stats_root.mkdir(parents=True, exist_ok=True)
    for bucket in args.buckets:
        (stats_root / bucket.upper()).mkdir(parents=True, exist_ok=True)

    if args.symbols:
        targets: List[tuple[str, Path]] = []
        for bucket in args.buckets:
            bucket_path = data_root / bucket
            if not bucket_path.exists():
                continue
            for symbol in args.symbols:
                symbol_path = bucket_path / symbol
                if symbol_path.exists():
                    targets.append((bucket, symbol_path))
                else:
                    print(f"Skipping missing symbol folder: {symbol_path}", file=sys.stderr)
    else:
        targets = list(iter_symbol_dirs(data_root, args.buckets))
        if args.limit is not None and args.limit >= 0:
            targets = targets[: args.limit]

    total = len(targets)
    if total == 0:
        print("No symbol folders found to process.")
        return 0

    print(f"Generating statistics for {total} symbols…")
    successes = 0

    # choose worker count (default: all available CPUs)
    default_workers = os.cpu_count() or 1
    workers = args.workers if getattr(args, "workers", None) is not None and args.workers > 0 else default_workers

    if workers <= 0:
        workers = 1

    if workers == 1:
        # fallback to sequential for single-core environments
        for index, (bucket, symbol_dir) in enumerate(targets, start=1):
            symbol = symbol_dir.name.upper()
            if not args.quiet:
                print(f"[{index}/{total}] {symbol} ({bucket})")

            bundle: DatasetBundle = load_symbol_datasets(symbol_dir)

            if bundle.daily is None and bundle.hourly is None:
                if not args.quiet:
                    print(f"    ⚠️  Skipping {symbol} (no datasets found).")
                continue

            summary = summarise_statistics(symbol, bundle, stats_root=stats_root, bucket=bucket)
            successes += 1
            if not args.quiet:
                print(
                    f"    ✓ daily={summary['daily_summary_rows']} rows, hourly={summary['hourly_summary_rows']} rows,"
                    f" streaks={summary['weekday_streak_rows']} rows"
                )
    else:
        print(f"Using {workers} workers for parallel processing.")
        # Submit jobs as independent worker processes. Pass string paths so
        # the ProcessPool on Windows can safely import/deserialize arguments.
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as exe:
            future_map = {}
            for index, (bucket, symbol_dir) in enumerate(targets, start=1):
                # submit (symbol_dir_path_str, stats_root_str, bucket)
                fut = exe.submit(process_symbol_path, str(symbol_dir), str(stats_root), bucket)
                future_map[fut] = (index, bucket, symbol_dir.name.upper())

            for fut in concurrent.futures.as_completed(future_map):
                index, bucket, symbol = future_map[fut]
                try:
                    result = fut.result()
                except Exception as exc:
                    if not args.quiet:
                        print(f"[{index}/{total}] {symbol} ({bucket}) -> ERROR: {exc}")
                    continue

                if result.get("success"):
                    successes += 1
                    if not args.quiet:
                        summary = result.get("summary", {})
                        print(
                            f"[{index}/{total}] {symbol} ({bucket})\n"
                            f"    ✓ daily={summary.get('daily_summary_rows', 0)} rows, hourly={summary.get('hourly_summary_rows', 0)} rows,"
                            f" streaks={summary.get('weekday_streak_rows', 0)} rows"
                        )
                else:
                    reason = result.get("reason")
                    if not args.quiet:
                        if reason == "no_datasets":
                            print(f"[{index}/{total}] {symbol} ({bucket})\n    ⚠️  Skipping {symbol} (no datasets found).")
                        else:
                            print(f"[{index}/{total}] {symbol} ({bucket}) -> failed: {result.get('error', reason)}")

    print(f"Completed statistics generation for {successes}/{total} symbols.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
