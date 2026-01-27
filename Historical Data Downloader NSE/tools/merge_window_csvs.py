"""Merge window CSVs with existing daily/hourly CSVs for all tickers."""
from __future__ import annotations
from pathlib import Path
from typing import List
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PARTITIONS = [ROOT / "P1", ROOT / "P2", ROOT / "P3"]


def merge_csv_files(existing_path: Path, window_path: Path) -> None:
    """Merge window CSV into existing CSV, removing duplicates and sorting by date."""
    if not window_path.exists():
        print(f"  Window file not found: {window_path.name}")
        return
    
    # Read window data
    window_df = pd.read_csv(window_path)
    window_df['date'] = pd.to_datetime(window_df['date'])
    
    # Read existing data if it exists
    if existing_path.exists():
        existing_df = pd.read_csv(existing_path)
        existing_df['date'] = pd.to_datetime(existing_df['date'])
        
        # Combine and remove duplicates (keep latest)
        combined = pd.concat([existing_df, window_df], ignore_index=True)
        combined.drop_duplicates(subset=['date'], keep='last', inplace=True)
    else:
        combined = window_df
    
    # Sort by date
    combined.sort_values('date', inplace=True)
    combined.reset_index(drop=True, inplace=True)
    
    # Save
    combined.to_csv(existing_path, index=False)
    print(f"  Merged {existing_path.name}: {len(combined)} total rows")


def process_all_symbols() -> None:
    """Process all symbol folders in P1, P2, P3."""
    total_merged = 0
    
    for partition in PARTITIONS:
        if not partition.exists():
            continue
        
        for symbol_dir in sorted(partition.iterdir()):
            if not symbol_dir.is_dir():
                continue
            
            symbol = symbol_dir.name
            print(f"Processing {symbol}...")
            
            # Merge daily
            existing_daily = symbol_dir / f"{symbol}_daily.csv"
            window_daily = symbol_dir / f"{symbol}_window_20251126_20260123_day.csv"
            if window_daily.exists():
                merge_csv_files(existing_daily, window_daily)
                total_merged += 1
            
            # Merge hourly
            existing_hourly = symbol_dir / f"{symbol}_hourly.csv"
            window_hourly = symbol_dir / f"{symbol}_window_20251126_20260123_60minute.csv"
            if window_hourly.exists():
                merge_csv_files(existing_hourly, window_hourly)
                total_merged += 1
    
    print(f"\n✅ Merged {total_merged} CSV files across all partitions.")


if __name__ == '__main__':
    process_all_symbols()
