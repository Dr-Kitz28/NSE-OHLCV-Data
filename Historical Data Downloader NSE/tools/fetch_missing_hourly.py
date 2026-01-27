"""Fetch hourly candles for symbols missing 2026-01-23 hourly rows and merge them.

This script imports helpers from `fetch_kite_ohlcv.py` so it uses the same
instrument mapping and file layout. It can run in dry-run mode (no Kite calls)
when `--access-token` is not provided.

Usage examples:
  # dry-run (find missing files and report)
  python tools/fetch_missing_hourly.py --date 2026-01-23

  # fetch using provided token
  python tools/fetch_missing_hourly.py --date 2026-01-23 --api-key <key> --access-token <token>
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import csv
import sys

import pandas as pd
from kiteconnect import KiteConnect

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)


def find_hourly_files(base: Path) -> list[Path]:
    return list(base.glob("P*/*/*_hourly.csv"))


def file_has_date(path: Path, date_token: str) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if date_token in line:
                    return True
    except Exception:
        return False
    return False


def merge_hourly_csv(existing: Path, new_df: pd.DataFrame) -> int:
    if new_df.empty:
        return 0
    try:
        old = pd.read_csv(existing, parse_dates=["date"]) if existing.exists() else pd.DataFrame()
    except Exception:
        old = pd.DataFrame()
    combined = pd.concat([old, new_df], ignore_index=True) if not old.empty else new_df
    combined.drop_duplicates(subset=["date"], inplace=True)
    combined.sort_values("date", inplace=True)
    combined.to_csv(existing, index=False)
    return len(combined)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="ISO date to ensure hourly rows exist (e.g. 2026-01-23)")
    parser.add_argument("--api-key", default="jc05rr20uksos0hc", help="Kite API key (optional)")
    parser.add_argument("--access-token", help="Kite access token (optional). If omitted the script runs dry-run only.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call Kite; only report missing files")
    args = parser.parse_args()

    target_date = datetime.fromisoformat(args.date)
    date_token = args.date

    hourly_files = find_hourly_files(ROOT)
    missing = []
    for p in hourly_files:
        if not file_has_date(p, date_token):
            missing.append(p)

    out_missing = REPORTS / f"missing_hourly_{date_token}.csv"
    with out_missing.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file"])
        for p in missing:
            writer.writerow([str(p.relative_to(ROOT))])

    print(f"Found {len(missing)} hourly files missing {date_token}. Report: {out_missing}")

    # If user requested a fetch and provided creds, perform fetch/merge per symbol
    if args.access_token and args.api_key and not args.dry_run and missing:
        kite = KiteConnect(api_key=args.api_key)
        kite.set_access_token(args.access_token)
        print("Building instrument token map from Kite...")
        token_map = {k.upper(): v for k, v in ((inst["tradingsymbol"].upper(), inst["instrument_token"]) for inst in kite.instruments("NSE"))}

        for rel in missing:
            p = ROOT / rel
            symbol = p.name.split("_hourly.csv")[0]
            # parent folder is bucket/symbol folder; try to resolve symbol folder name
            symdir = p.parent
            symbol_name = symdir.name.upper()
            token = token_map.get(symbol_name)
            if not token:
                print(f"Instrument token not found for {symbol_name}, skipping")
                continue
            start = datetime(target_date.year, target_date.month, target_date.day)
            end = start + timedelta(days=1)
            try:
                data = kite.historical_data(instrument_token=int(token), from_date=start, to_date=end, interval="60minute")
            except Exception as exc:
                print(f"Failed fetching {symbol_name}: {exc}")
                continue
            df = pd.DataFrame(data)
            if df.empty:
                print(f"No hourly candles returned for {symbol_name} on {date_token}")
                continue
            merged_rows = merge_hourly_csv(p, df)
            print(f"Merged {symbol_name}: {merged_rows} total rows written to {p}")

    else:
        if not args.access_token or not args.api_key:
            print("Dry-run or credentials missing; no Kite calls were performed.")


if __name__ == '__main__':
    main()
