"""Bulk-download NSE OHLCV data via KiteConnect.

This script reads all tradingsymbols from `Tickers/nse_symbols_all.csv`,
maps them to instrument tokens from the live Kite instruments dump, and
downloads both daily and hourly OHLCV candles from each stock's IPO date
(or from 1990-01-01 when the listing date is unknown).

Usage
-----
Set the required environment variables (API credentials + access token)
before running:

```
set KITE_API_KEY=<your_key>
set KITE_ACCESS_TOKEN=<fresh_access_token>
python fetch_kite_ohlcv.py
```

Notes
-----
* The access token must be freshly generated via `ATG_GoldenEye.py`.
* The script throttles itself to stay within Kite rate limits but expect it
  to run for an extended period (2000+ symbols × two intervals).
* Output files are written under partitioned folders (`P1`, `P2`, `P3`) so
    no single directory contains more than ~1,000 CSV pairs. Symbols 1–1000 land
    in P1, 1001–2000 in P2, and the remainder in P3.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from requests.exceptions import RequestException

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

# Intervals to fetch and their chunk sizes (in days) per Kite historical limits
INTERVAL_CONFIG = {
    "day": 1800,  # Kite caps daily range at 2000 days per request
    "60minute": 390,  # 60-minute candles allow up to ~400 days per request
}

DEFAULT_START_DATE = datetime(1990, 1, 1)

def resolve_bucket_root(symbol_index: int) -> Path:
    for start, end, path in OUTPUT_PARTITIONS:
        if symbol_index >= start and (end is None or symbol_index <= end):
            return path
    # Fallback to the last partition if the symbol count ever grows beyond expectations
    return OUTPUT_PARTITIONS[-1][2]

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

    @property
    def interval(self) -> float:
        return self.min_interval


def load_symbols(csv_path: Path) -> List[str]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Symbol list not found: {csv_path}")

    symbols: List[str] = []
    with csv_path.open(encoding="utf-8-sig") as fh:
        for row in fh:
            symbol = row.strip().lstrip("\ufeff")
            if not symbol:
                continue
            # Entries are like `INFY.NS`; drop the suffix for Kite
            if symbol.endswith(".NS"):
                symbol = symbol[:-3]
            symbols.append(symbol.upper())
    return symbols


def load_listing_dates(equity_csv: Optional[Path]) -> Dict[str, datetime]:
    """Read `EQUITY_L.csv` and return SYMBOL -> listing datetime."""

    mapping: Dict[str, datetime] = {}
    if not equity_csv or not equity_csv.exists():
        return mapping

    import csv as _csv

    with equity_csv.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = _csv.DictReader(fh)
        for raw_row in reader:
            row = {
                (key or "").strip(): (value or "").strip()
                for key, value in raw_row.items()
            }
            sym = row.get("SYMBOL", "").upper()
            raw_date = row.get("DATE OF LISTING") or row.get("DATE_OF_LISTING") or ""
            if not sym or not raw_date:
                continue
            parsed = None
            for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%b-%y"):
                try:
                    parsed = datetime.strptime(raw_date, fmt)
                    break
                except Exception:
                    continue
            if parsed:
                mapping[sym] = parsed
    return mapping


def build_token_map(kite: KiteConnect) -> Dict[str, int]:
    instruments = kite.instruments("NSE")
    mapping: Dict[str, int] = {}
    for inst in instruments:
        tradingsymbol = inst.get("tradingsymbol")
        token = inst.get("instrument_token")
        if tradingsymbol and token:
            mapping[tradingsymbol.upper()] = int(token)
    return mapping


def resolve_listing_csv(cli_path: Optional[Path]) -> Optional[Path]:
    candidates: List[Path] = []
    if cli_path:
        candidates.append(cli_path)
    default = ROOT / "Tickers" / "EQUITY_L.csv"
    candidates.append(default)
    parent_candidate = ROOT.parent / "Tickers" / "EQUITY_L.csv"
    if parent_candidate not in candidates:
        candidates.append(parent_candidate)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def chunk_date_ranges(start: datetime, end: datetime, chunk_days: int) -> Iterable[tuple[datetime, datetime]]:
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        yield cursor, chunk_end
        cursor = chunk_end


def fetch_interval(
    kite: KiteConnect,
    token: int,
    interval: str,
    start: datetime,
    end: datetime,
    chunk_days: int,
    limiter: RateLimiter,
) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for chunk_start, chunk_end in chunk_date_ranges(start, end, chunk_days):
        tries = 0
        while True:
            tries += 1
            limiter.wait()
            try:
                data = kite.historical_data(
                    instrument_token=token,
                    from_date=chunk_start,
                    to_date=chunk_end,
                    interval=interval,
                )
                frame = pd.DataFrame(data)
                if not frame.empty:
                    frames.append(frame)
                break
            except KiteException as exc:
                message = str(exc)
                if "Incorrect `api_key`" in message or "TokenException" in message:
                    raise
                if tries >= 3:
                    raise
                print(
                    f"  Retry ({tries}) fetching {interval} chunk {chunk_start:%Y-%m-%d}->{chunk_end:%Y-%m-%d}: {exc}"
                )
                backoff = max(limiter.interval, 0.5) * tries
                time.sleep(backoff)
            except RequestException as exc:
                if tries >= 3:
                    raise
                print(
                    f"  Retry ({tries}) fetching {interval} chunk {chunk_start:%Y-%m-%d}->{chunk_end:%Y-%m-%d}: {exc}"
                )
                backoff = max(limiter.interval, 0.5) * tries
                time.sleep(backoff)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df.sort_values("date", inplace=True)
    return df


def ensure_output_dir(symbol: str, bucket_root: Path) -> Path:
    out_dir = bucket_root / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def save_dataframe(df: pd.DataFrame, dest: Path) -> None:
    if df.empty:
        print(f"    No data returned; skipping write for {dest.name}")
        return
    df.to_csv(dest, index=False)
    print(f"    Saved {dest} ({len(df):,} rows)")


def files_ready(files: List[Path]) -> bool:
    return all(file.exists() and file.stat().st_size > 0 for file in files)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch hourly & daily OHLCV for NSE symbols via KiteConnect")
    parser.add_argument("--api-key", default="jc05rr20uksos0hc", help="Kite API key")
    parser.add_argument("--access-token", default=os.environ.get("KITE_ACCESS_TOKEN", ""), help="Kite access token")
    parser.add_argument(
        "--from-date",
        default=DEFAULT_START_DATE.strftime("%Y-%m-%d"),
        help="ISO start date used when no listing date is found (default: 1990-01-01)",
    )
    parser.add_argument("--to-date", default=datetime.today().strftime("%Y-%m-%d"), help="ISO end date (default: today)")
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.34,
        help="Minimum seconds between Kite API calls (default honours 3 req/sec)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit number of symbols (for testing)")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip symbols whose daily/hourly CSVs already exist",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip symbols listed in the completed log file",
    )
    parser.add_argument(
        "--completed-log",
        type=Path,
        default=ROOT / "completed_symbols.txt",
        help="Path to completed-symbols log used with --resume",
    )
    parser.add_argument(
        "--listing-csv",
        type=Path,
        default=None,
        help="Optional path to EQUITY_L.csv containing IPO/listing dates",
    )
    args = parser.parse_args()

    if not args.api_key or not args.access_token:
        print("API key / access token missing. Set KITE_API_KEY and KITE_ACCESS_TOKEN or pass via flags.")
        sys.exit(1)

    from_date = datetime.fromisoformat(args.from_date)
    to_date = datetime.fromisoformat(args.to_date)
    if from_date >= to_date:
        print("from-date must be earlier than to-date")
        sys.exit(1)

    symbols = load_symbols(TICKER_CSV)
    if args.limit:
        symbols = symbols[: args.limit]

    limiter = RateLimiter(args.sleep)

    kite = KiteConnect(api_key=args.api_key)
    kite.set_access_token(args.access_token)

    print("Downloading NSE instrument dump...")
    token_map = build_token_map(kite)
    missing = []

    completed: Set[str] = set()
    if args.resume and args.completed_log.exists():
        completed = {
            line.strip().upper()
            for line in args.completed_log.read_text().splitlines()
            if line.strip()
        }
        print(f"Loaded {len(completed)} completed symbols from {args.completed_log}")

    listing_csv = resolve_listing_csv(args.listing_csv)
    if listing_csv:
        print(f"Using listing dates from {listing_csv}")
    else:
        print("Listing CSV not found; relying on --from-date fallback for all symbols")
    listing_dates = load_listing_dates(listing_csv)

    for idx, symbol in enumerate(symbols, start=1):
        token = token_map.get(symbol)
        if args.resume and symbol in completed:
            print(f"[{idx}/{len(symbols)}] {symbol}: already marked completed, skipping")
            continue

        bucket_root = resolve_bucket_root(idx)
        out_dir = ensure_output_dir(symbol, bucket_root)
        daily_path = out_dir / f"{symbol}_daily.csv"
        hourly_path = out_dir / f"{symbol}_hourly.csv"

        if args.skip_existing and files_ready([daily_path, hourly_path]):
            print(f"[{idx}/{len(symbols)}] {symbol}: CSVs already exist, skipping")
            continue

        if not token:
            missing.append(symbol)
            print(f"[{idx}/{len(symbols)}] {symbol}: instrument token not found, skipping")
            continue
        print(f"[{idx}/{len(symbols)}] {symbol} (token {token})")

        # Determine symbol-specific start date: prefer listing/IPO date from EQUITY_L.csv
        base_symbol = symbol.replace(".NS", "")
        symbol_start = listing_dates.get(base_symbol)
        if symbol_start is None:
            symbol_start = datetime.fromisoformat(args.from_date)
        else:
            # ensure timezone-naive datetime at midnight
            symbol_start = datetime(symbol_start.year, symbol_start.month, symbol_start.day)

        wrote_any = False
        for interval, chunk_days in INTERVAL_CONFIG.items():
            dest = daily_path if interval == "day" else hourly_path
            try:
                df = fetch_interval(
                    kite=kite,
                    token=token,
                    interval=interval,
                    start=symbol_start,
                    end=to_date,
                    chunk_days=chunk_days,
                    limiter=limiter,
                )
            except KiteException as exc:
                message = str(exc)
                if "Incorrect `api_key`" in message or "TokenException" in message:
                    print(
                        "  Authentication with KiteConnect failed. Generate a fresh access token and rerun"
                        " the script with --resume/--skip-existing. Aborting run now."
                    )
                    sys.exit(2)
                print(f"  Failed {interval} fetch for {symbol}: {exc}")
                continue

            save_dataframe(df, dest)
            if not df.empty:
                wrote_any = True

        if wrote_any:
            args.completed_log.parent.mkdir(parents=True, exist_ok=True)
            with args.completed_log.open("a", encoding="utf-8") as fh:
                fh.write(f"{symbol}\n")

    if missing:
        missing_path = ROOT / "missing_symbols.txt"
        missing_path.write_text("\n".join(missing))
        print(f"Instrument tokens missing for {len(missing)} symbols -> {missing_path}")


if __name__ == "__main__":
    main()