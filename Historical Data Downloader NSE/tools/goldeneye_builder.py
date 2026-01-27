"""
GoldenEye Data Builder
Generates cumulative statistical moments (mean, std, skew, kurtosis) for RL model consumption.
Iterates through symbol datasets and produces expanding window statistics.
"""

import argparse
import sys
import os
from pathlib import Path
import pandas as pd
import numpy as np
import concurrent.futures
import traceback

# Ensure we can import from the same directory
sys.path.append(str(Path(__file__).parent))

from statistics_engine import load_symbol_datasets, DatasetBundle, _localise_hourly

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLDENEYE_ROOT = Path("D:/Trading Strategies/GoldenEye")
DEFAULT_BUCKETS = ["P1", "P2", "P3"]

def calculate_expanding_moments(series: pd.Series) -> pd.DataFrame:
    """
    Compute expanding window moments: count, mean, std, skew, kurtosis.
    Matches statistics_engine logic (std ddof=0).
    """
    df = pd.DataFrame(index=series.index)
    expanding = series.expanding()
    
    df['count'] = expanding.count()
    df['mean'] = expanding.mean()
    # std with ddof=0
    # Pandas expanding().std() supports ddof argument
    df['std'] = expanding.std(ddof=0)
    df['skew'] = expanding.skew()
    df['kurtosis'] = expanding.kurt()
    
    # Fill NaNs (first few rows)
    df = df.fillna(0)
    
    return df

def process_symbol(symbol_dir: Path, goldeneye_root: Path, bucket: str) -> dict:
    symbol = symbol_dir.name.upper()
    try:
        bundle = load_symbol_datasets(symbol_dir)
        results = {"symbol": symbol, "daily_rows": 0, "hourly_rows": 0}
        
        # 1. Daily Processing
        if bundle.daily is not None and not bundle.daily.empty:
            daily_df = bundle.daily.copy()
            if 'date' in daily_df.columns:
                daily_df['date'] = pd.to_datetime(daily_df['date'], utc=True)
            
            daily_df = daily_df.sort_values('date')
            
            if 'pct_change' in daily_df.columns:
                # Filter valid data
                valid_daily = daily_df.dropna(subset=['pct_change', 'date'])
                
                if not valid_daily.empty:
                    moments = calculate_expanding_moments(valid_daily['pct_change'])
                    moments['date'] = valid_daily['date']
                    moments['pct_change'] = valid_daily['pct_change']
                    
                    # Save
                    out_dir = goldeneye_root / "daily" / bucket / symbol
                    out_dir.mkdir(parents=True, exist_ok=True)
                    moments.to_csv(out_dir / "cumulative_moments.csv", index=False)
                    results["daily_rows"] = len(moments)

        # 2. Hourly Processing
        if bundle.hourly is not None and not bundle.hourly.empty:
            # Localise and prepare
            hourly_df = _localise_hourly(bundle.hourly)
            if 'date' in hourly_df.columns:
                hourly_df['date'] = pd.to_datetime(hourly_df['date'], utc=True)
                
            hourly_df = hourly_df.sort_values('date')
            
            if 'pct_change' in hourly_df.columns:
                valid_hourly = hourly_df.dropna(subset=['pct_change', 'date'])
                
                if not valid_hourly.empty:
                    moments = calculate_expanding_moments(valid_hourly['pct_change'])
                    moments['date'] = valid_hourly['date']
                    moments['pct_change'] = valid_hourly['pct_change']
                    if 'weekday' in valid_hourly.columns:
                        moments['weekday'] = valid_hourly['weekday']
                    if 'hour_label' in valid_hourly.columns:
                        moments['hour_label'] = valid_hourly['hour_label']
                        
                    # Save
                    out_dir = goldeneye_root / "hourly" / bucket / symbol
                    out_dir.mkdir(parents=True, exist_ok=True)
                    moments.to_csv(out_dir / "cumulative_moments.csv", index=False)
                    results["hourly_rows"] = len(moments)
                    
        return results

    except Exception as e:
        return {"symbol": symbol, "error": str(e)}

def main():
    parser = argparse.ArgumentParser(description="Build GoldenEye cumulative statistics.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Data root (P1/P2/P3).")
    parser.add_argument("--goldeneye-root", type=Path, default=DEFAULT_GOLDENEYE_ROOT, help="Output root.")
    parser.add_argument("--buckets", nargs="*", default=DEFAULT_BUCKETS, help="Buckets to process.")
    parser.add_argument("--symbols", nargs="*", help="Specific symbols to process.")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers.")
    parser.add_argument("--quiet", action="store_true", help="Suppress output.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of symbols per bucket.")
    
    args = parser.parse_args()
    
    if not args.root.exists():
        print(f"Error: Data root {args.root} does not exist.")
        return 1
        
    args.goldeneye_root.mkdir(parents=True, exist_ok=True)
    
    tasks = []
    
    # Collect tasks
    for bucket in args.buckets:
        bucket_path = args.root / bucket
        if not bucket_path.exists():
            continue
            
        for symbol_path in bucket_path.iterdir():
            if not symbol_path.is_dir():
                continue
                
            symbol = symbol_path.name.upper()
            if args.symbols and symbol not in args.symbols:
                continue
                
            tasks.append((symbol_path, args.goldeneye_root, bucket))
            
    print(f"Found {len(tasks)} symbols to process.")
    
    if args.limit:
        tasks = tasks[:args.limit]
        print(f"Limiting to {args.limit} tasks.")
    
    success_count = 0
    
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_symbol, *task): task[0].name for task in tasks}
        
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            symbol = futures[future]
            try:
                res = future.result()
                if "error" in res:
                    if not args.quiet:
                        print(f"[{i+1}/{len(tasks)}] {symbol}: Failed - {res['error']}")
                else:
                    success_count += 1
                    if not args.quiet:
                        print(f"[{i+1}/{len(tasks)}] {symbol}: Daily={res['daily_rows']}, Hourly={res['hourly_rows']}")
            except Exception as e:
                print(f"[{i+1}/{len(tasks)}] {symbol}: Exception - {e}")
                
    print(f"GoldenEye build complete. Processed {success_count}/{len(tasks)} symbols.")

if __name__ == "__main__":
    main()
