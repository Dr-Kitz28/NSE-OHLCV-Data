from __future__ import annotations
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd
from kiteconnect import KiteConnect
from requests.exceptions import RequestException

ROOT = Path(__file__).resolve().parent.parent
PARTITIONS = [ROOT / "P1", ROOT / "P2", ROOT / "P3"]
OUT_SUFFIX = "_window_{start}_{end}_{interval}.csv"

API_KEY = os.environ.get("KITE_API_KEY", "jc05rr20uksos0hc")
ACCESS_TOKEN = os.environ.get("KITE_ACCESS_TOKEN", "qxF1Xk1PY9sW2PwY7vqRPD87d1qA60DE")

START = datetime.fromisoformat("2025-11-26")
END = datetime.fromisoformat("2026-01-23")

INTERVALS = {"day": "day", "60minute": "60minute"}
MIN_INTERVAL = 0.34  # 3 req/sec ceiling

class RateLimiter:
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


def gather_symbols_from_partitions(partition_paths: List[Path]) -> List[str]:
    symbols: List[str] = []
    for p in partition_paths:
        if not p.exists():
            continue
        for child in p.iterdir():
            if child.is_dir():
                symbols.append(child.name.upper())
    return sorted(set(symbols))


def build_token_map(kite: KiteConnect) -> Dict[str, int]:
    instruments = kite.instruments("NSE")
    mapping: Dict[str, int] = {}
    for inst in instruments:
        tradingsymbol = inst.get("tradingsymbol")
        token = inst.get("instrument_token")
        if tradingsymbol and token:
            mapping[tradingsymbol.upper()] = int(token)
    return mapping


def fetch_and_save(kite: KiteConnect, token: int, out_dir: Path, interval: str, limiter: RateLimiter) -> None:
    try:
        limiter.wait()
        data = kite.historical_data(instrument_token=token, from_date=START, to_date=END, interval=interval)
    except Exception as e:
        print(f"  Failed to fetch {interval} for token {token}: {e}")
        return

    if not data:
        print(f"  No {interval} data for token {token} in window")
        return

    df = pd.DataFrame(data)
    df.sort_values("date", inplace=True)
    start_s = START.date().isoformat().replace('-', '')
    end_s = END.date().isoformat().replace('-', '')
    fname = f"{out_dir.name}{OUT_SUFFIX.format(start=start_s,end=end_s,interval=interval)}"
    dest = out_dir / fname
    df.to_csv(dest, index=False)
    print(f"  Saved {dest} ({len(df):,} rows)")


def main() -> int:
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)

    print("Building instrument token map...")
    token_map = build_token_map(kite)

    symbols = gather_symbols_from_partitions(PARTITIONS)
    if not symbols:
        print("No symbols found under partitions P1/P2/P3")
        return 1

    limiter = RateLimiter(MIN_INTERVAL)
    missing: List[str] = []

    for idx, sym in enumerate(symbols, start=1):
        token = token_map.get(sym)
        print(f"[{idx}/{len(symbols)}] {sym}")
        if not token:
            missing.append(sym)
            print(f"  Instrument token not found, skipping")
            continue
        # find which partition contains the symbol folder
        out_dir = None
        for p in PARTITIONS:
            candidate = p / sym
            if candidate.exists():
                out_dir = candidate
                break
        if out_dir is None:
            # fallback: create in P1
            out_dir = PARTITIONS[0] / sym
            out_dir.mkdir(parents=True, exist_ok=True)

        for interval in INTERVALS.values():
            fetch_and_save(kite, token, out_dir, interval, limiter)

    if missing:
        miss_path = ROOT / "missing_window_symbols.txt"
        miss_path.write_text("\n".join(missing))
        print(f"Instrument tokens missing for {len(missing)} symbols -> {miss_path}")

    print("Done.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
