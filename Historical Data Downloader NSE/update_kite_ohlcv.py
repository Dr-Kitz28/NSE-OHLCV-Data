"""Incremental OHLCV updater for Kite Connect - appends new candles to existing CSVs.

This script reads existing daily/hourly CSVs, finds the last date, and fetches only
new candles from Kite Connect API. It's designed for daily scheduled runs to keep
data up-to-date without re-downloading from scratch.

Usage
-----
python update_kite_ohlcv.py --api-key <key> --access-token <token>

Or set environment variables:
set KITE_API_KEY=<your_key>
set KITE_ACCESS_TOKEN=<fresh_access_token>
python update_kite_ohlcv.py
"""

from __future__ import annotations
import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from kiteconnect import KiteConnect
from kiteconnect.exceptions import KiteException

ROOT = Path(__file__).resolve().parent
TICKER_CSV = ROOT / "Tickers" / "nse_symbols_all.csv"
OUTPUT_PARTITIONS = (
    (1, 1000, ROOT / "P1"),
    (1001, 2000, ROOT / "P2"),
    (2001, None, ROOT / "P3"),
)

INTERVAL_CONFIG = {
    "day": 1800,
    "60minute": 390,
}

DEFAULT_LOOKBACK_DAYS = 5  # Re-fetch last 5 days to catch any adjustments


class RateLimiter:
    """Coarse rate limiter to honour Kite's 3 req/sec ceiling."""
    def __init__(self, min_interval: float) -> None:
        self.min_interval = max(min_interval, 0.0)
        self._next_time = time.perf_counter()

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        now = time.perf_counter()
        if now < self._next_time:
            time.sleep(self._next_time - now)
            now = time.perf_counter()
        self._next_time = max(now, self._next_time) + self.min_interval


def resolve_bucket_root(symbol_index: int) -> Path:
    for start, end, path in OUTPUT_PARTITIONS:
        if symbol_index >= start and (end is None or symbol_index <= end):
            return path
    return OUTPUT_PARTITIONS[-1][2]


def load_symbols(csv_path: Path) -> List[str]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Symbol list not found: {csv_path}")

    symbols: List[str] = []
    with csv_path.open(encoding="utf-8-sig") as fh:
        for row in fh:
            symbol = row.strip().lstrip("\ufeff")
            if not symbol:
                continue
            if symbol.endswith(".NS"):
                symbol = symbol[:-3]
            symbols.append(symbol.upper())
    return symbols


def build_token_map(kite: KiteConnect) -> Dict[str, int]:
    instruments = kite.instruments("NSE")
    mapping: Dict[str, int] = {}
    for inst in instruments:
        tradingsymbol = inst.get("tradingsymbol")
        token = inst.get("instrument_token")
        if tradingsymbol and token:
            mapping[tradingsymbol.upper()] = int(token)
    return mapping


def get_last_date_from_csv(csv_path: Path) -> Optional[datetime]:
    """Read last date from existing CSV."""
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return None
    try:
        df = pd.read_csv(csv_path)
        if df.empty or 'date' not in df.columns:
            return None
        df['date'] = pd.to_datetime(df['date'])
        last_date = df['date'].max()
        return last_date.to_pydatetime() if pd.notna(last_date) else None
    except Exception as e:
        print(f"  Warning: Could not read last date from {csv_path.name}: {e}")
        return None


def fetch_and_append(
    kite: KiteConnect,
    token: int,
    csv_path: Path,
    interval: str,
    start: datetime,
    end: datetime,
    chunk_days: int,
    limiter: RateLimiter,
) -> bool:
    """Fetch new data and append to existing CSV. Returns True if data was appended."""
    try:
        limiter.wait()
        data = kite.historical_data(
            instrument_token=token,
            from_date=start,
            to_date=end,
            interval=interval,
        )
    except KiteException as exc:
        message = str(exc)
        if "Incorrect `api_key`" in message or "TokenException" in message:
            print(f"  Authentication failed: {exc}")
            sys.exit(2)
        print(f"  Failed to fetch {interval}: {exc}")
        return False
    except Exception as exc:
        print(f"  Failed to fetch {interval}: {exc}")
        return False

    if not data:
        print(f"  No new {interval} data")
        return False

    new_df = pd.DataFrame(data)
    new_df['date'] = pd.to_datetime(new_df['date'])

    # Read existing data and merge
    if csv_path.exists() and csv_path.stat().st_size > 0:
        try:
            existing_df = pd.read_csv(csv_path)
            existing_df['date'] = pd.to_datetime(existing_df['date'])
            
            # Combine and remove duplicates (keep latest)
            combined = pd.concat([existing_df, new_df], ignore_index=True)
            combined.drop_duplicates(subset=['date'], keep='last', inplace=True)
            combined.sort_values('date', inplace=True)
            combined.reset_index(drop=True, inplace=True)
            
            # Only write if we have new rows
            if len(combined) > len(existing_df):
                combined.to_csv(csv_path, index=False)
                new_rows = len(combined) - len(existing_df)
                print(f"  Appended {new_rows} new {interval} rows → {csv_path.name} (total: {len(combined)})")
                return True
            else:
                print(f"  No new {interval} rows (already up-to-date)")
                return False
        except Exception as e:
            print(f"  Error merging data: {e}")
            return False
    else:
        # No existing file, write new data
        new_df.sort_values('date', inplace=True)
        new_df.to_csv(csv_path, index=False)
        print(f"  Created {csv_path.name} with {len(new_df)} {interval} rows")
        return True


def ensure_output_dir(symbol: str, bucket_root: Path) -> Path:
    out_dir = bucket_root / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Incremental OHLCV update for NSE symbols via KiteConnect")
    parser.add_argument("--api-key", default="jc05rr20uksos0hc", help="Kite API key")
    parser.add_argument("--access-token", default=os.environ.get("KITE_ACCESS_TOKEN", ""), help="Kite access token")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help="Re-fetch last N days to catch adjustments (default: 5)",
    )
    parser.add_argument(
        "--to-date",
        default=datetime.today().strftime("%Y-%m-%d"),
        help="ISO end date (default: today)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.34,
        help="Minimum seconds between Kite API calls (default: 0.34 for 3 req/sec)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit number of symbols (for testing)")
    args = parser.parse_args()

    if not args.api_key or not args.access_token:
        print("ERROR: API key / access token missing. Set KITE_API_KEY and KITE_ACCESS_TOKEN or pass via flags.")
        sys.exit(1)

    to_date = datetime.fromisoformat(args.to_date)
    symbols = load_symbols(TICKER_CSV)
    if args.limit:
        symbols = symbols[: args.limit]

    limiter = RateLimiter(args.sleep)

    kite = KiteConnect(api_key=args.api_key)
    kite.set_access_token(args.access_token)

    print("Downloading NSE instrument dump...")
    token_map = build_token_map(kite)
    missing = []
    updated_count = 0
    skipped_count = 0

    for idx, symbol in enumerate(symbols, start=1):
        token = token_map.get(symbol)
        bucket_root = resolve_bucket_root(idx)
        out_dir = ensure_output_dir(symbol, bucket_root)
        
        daily_path = out_dir / f"{symbol}_daily.csv"
        hourly_path = out_dir / f"{symbol}_hourly.csv"

        if not token:
            missing.append(symbol)
            print(f"[{idx}/{len(symbols)}] {symbol}: instrument token not found, skipping")
            skipped_count += 1
            continue

        print(f"[{idx}/{len(symbols)}] {symbol} (token {token})")

        # Determine start dates based on existing data
        updated_any = False
        
        for interval, chunk_days in INTERVAL_CONFIG.items():
            dest = daily_path if interval == "day" else hourly_path
            
            # Get last date from existing CSV
            last_date = get_last_date_from_csv(dest)
            
            if last_date:
                # Start from lookback days before last date
                start = last_date - timedelta(days=args.lookback_days)
                print(f"  {interval}: last date {last_date.date()}, fetching from {start.date()}")
            else:
                # No existing data, fetch last 730 days (2 years)
                start = to_date - timedelta(days=730)
                print(f"  {interval}: no existing data, fetching last 730 days from {start.date()}")
            
            appended = fetch_and_append(
                kite=kite,
                token=token,
                csv_path=dest,
                interval=interval,
                start=start,
                end=to_date,
                chunk_days=chunk_days,
                limiter=limiter,
            )
            
            if appended:
                updated_any = True
        
        if updated_any:
            updated_count += 1
        else:
            skipped_count += 1

    if missing:
        missing_path = ROOT / "missing_symbols.txt"
        missing_path.write_text("\n".join(missing))
        print(f"\nInstrument tokens missing for {len(missing)} symbols → {missing_path}")

    print(f"\n✅ Update complete: {updated_count} symbols updated, {skipped_count} skipped (up-to-date or missing)")


if __name__ == "__main__":
    main()
