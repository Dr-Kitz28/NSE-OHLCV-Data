"""Incrementally append NSE OHLCV data and refine the GoldenEye SAC model.

Workflow handled by this script:
1. Read the latest timestamps from the existing daily/hourly CSVs under P1/P2/P3.
2. Pull fresh OHLCV candles from Kite Connect starting a little before the
   latest timestamp (to guard against holiday gaps) and append them to disk.
3. Rebuild expanding-moment features via ``goldeneye_builder.py`` for the
   touched symbols, ensuring ``D:/Trading Strategies/GoldenEye`` stays current.
4. Reload the previously trained SAC policy, continue learning for a
   user-specified number of timesteps, and save the refined model.

The script is intentionally modular so it can be invoked manually or via an
external scheduler (Windows Task Scheduler instructions are provided in the
project README/assistant response).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np

import pandas as pd
from kiteconnect import KiteConnect
from kiteconnect.exceptions import KiteException
from requests.exceptions import ReadTimeout
from stable_baselines3 import SAC
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv
from zoneinfo import ZoneInfo

# Make sure we can import sibling helpers
TOOLS_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_ROOT.parent
sys.path.append(str(TOOLS_ROOT))

from goldeneye_env import GoldenEyeEnv  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_GOLDENEYE_ROOT = Path("D:/Trading Strategies/GoldenEye")
DEFAULT_MODEL_DIR = DEFAULT_GOLDENEYE_ROOT / "models"
RAW_BUCKETS = ("P1", "P2", "P3")

# Kite interva/filename metadata
INTERVAL_SPECS = {
    "day": {
        "suffix": "daily",
        "tolerance": timedelta(days=3),
    },
    "60minute": {
        "suffix": "hourly",
        "tolerance": timedelta(hours=12),
    },
}

MAX_FETCH_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5


@dataclass
class SymbolContext:
    symbol: str
    bucket: str
    raw_dir: Path

    @property
    def daily_csv(self) -> Path:
        return self.raw_dir / f"{self.symbol}_{INTERVAL_SPECS['day']['suffix']}.csv"

    @property
    def hourly_csv(self) -> Path:
        return self.raw_dir / f"{self.symbol}_{INTERVAL_SPECS['60minute']['suffix']}.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append OHLCV data and refine SAC model")
    parser.add_argument("--symbols", nargs="+", help="Symbols to refresh (e.g. TIMETECHNO)")
    parser.add_argument("--all", action="store_true", help="Process all symbols found under P1/P2/P3")
    parser.add_argument("--api-key", default="jc05rr20uksos0hc", help="Kite API key")
    parser.add_argument(
        "--access-token",
        default=os.environ.get("KITE_ACCESS_TOKEN", ""),
        help="Kite access token (must be regenerated daily)",
    )
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=PROJECT_ROOT,
        help="Root containing P1/P2/P3 folders (default: repo root)",
    )
    parser.add_argument(
        "--goldeneye-root",
        type=Path,
        default=DEFAULT_GOLDENEYE_ROOT,
        help="Path where cumulative moments + models live",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=DEFAULT_MODEL_DIR,
        help="Directory storing SAC checkpoints (default: GoldenEye/models)",
    )
    parser.add_argument(
        "--timesteps",
        type=int,
        default=20000,
        help="Additional timesteps to learn after appending data (default: 20k)",
    )
    parser.add_argument(
        "--n-envs",
        type=int,
        default=None,
        help="Parallel env count for refinement (default: cpu_count-1)",
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip API calls and reuse on-disk CSVs",
    )
    parser.add_argument(
        "--skip-builder",
        action="store_true",
        help="Do not rebuild cumulative moments after appending",
    )
    parser.add_argument(
        "--skip-train",
        action="store_true",
        help="Skip SAC refinement (only update CSVs / features)",
    )
    parser.add_argument(
        "--checkpoint-tag",
        default="",
        help="Optional suffix for the refined model (e.g. _20241124)",
    )
    parser.add_argument(
        "--train-start-date",
        default=None,
        help="UTC date (inclusive) to begin RL training data (e.g. 2010-01-01)",
    )
    parser.add_argument(
        "--train-end-date",
        default=None,
        help="UTC date (exclusive) to stop RL training data (e.g. 2025-01-01)",
    )
    parser.add_argument(
        "--refine-start-date",
        default=None,
        help="UTC date (inclusive) to begin the fine-tuning window (e.g. 2025-01-01)",
    )
    parser.add_argument(
        "--refine-end-date",
        default=None,
        help="UTC date (exclusive) to end the fine-tuning window",
    )
    parser.add_argument(
        "--refine-timesteps",
        type=int,
        default=0,
        help="Timesteps to learn during the fine-tuning window (0 = skip second phase)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log the intended actions without modifying files",
    )
    return parser.parse_args()


def ensure_symbol_context(symbol: str, raw_root: Path) -> SymbolContext:
    upper = symbol.upper()
    for bucket in RAW_BUCKETS:
        candidate = raw_root / bucket / upper
        if candidate.exists():
            return SymbolContext(symbol=upper, bucket=bucket, raw_dir=candidate)
    raise FileNotFoundError(f"Symbol {symbol} not found under {raw_root}/P?/SYMBOL")


def load_token_map(kite: KiteConnect) -> Dict[str, int]:
    instruments = kite.instruments("NSE")
    return {
        inst["tradingsymbol"].upper(): int(inst["instrument_token"])
        for inst in instruments
        if inst.get("tradingsymbol") and inst.get("instrument_token")
    }


def latest_timestamp(csv_path: Path) -> datetime:
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing CSV {csv_path}; run the bulk downloader once before append mode")
    df = pd.read_csv(csv_path, usecols=["date"])
    if df.empty:
        raise ValueError(f"CSV {csv_path} has no rows")
    ts = pd.to_datetime(df["date"].iloc[-1])
    if ts.tzinfo is None:
        ts = ts.tz_localize(IST)
    else:
        ts = ts.tz_convert(IST)
    return ts


def kite_datetime_bounds(last_ts: datetime, tolerance: timedelta) -> Tuple[datetime, datetime]:
    start = (last_ts - tolerance).astimezone(IST).replace(tzinfo=None)
    end = datetime.now(IST).replace(tzinfo=None)
    return start, end


def fetch_increment(kite: KiteConnect, token: int, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    data = kite.historical_data(
        instrument_token=token,
        from_date=start,
        to_date=end,
        interval=interval,
    )
    df = pd.DataFrame(data)
    if df.empty:
        return df
    df["date"] = _ensure_ist(df["date"])
    return df


def _ensure_ist(series: Iterable) -> pd.Series:
    # Robust parsing for mixed timezone/naive datetime values.
    # First try a vectorized parse with utc=True (handles offset-aware strings),
    # then convert to IST. If that fails (mixed or unparsable values), fall
    # back to element-wise conversion to avoid accessor errors.
    try:
        dt = pd.to_datetime(series, errors="coerce", utc=True)
        # dt is timezone-aware (UTC) or NaT; convert to IST
        return dt.dt.tz_convert(IST)
    except Exception:
        # element-wise defensive conversion
        def _convert(x):
            if pd.isna(x):
                return pd.NaT
            try:
                t = pd.to_datetime(x)
            except Exception:
                return pd.NaT
            # if parsed value has tzinfo, convert; otherwise localize as IST
            if getattr(t, "tzinfo", None) is None:
                try:
                    return t.tz_localize(IST)
                except Exception:
                    # if localization fails, return NaT
                    return pd.NaT
            else:
                try:
                    return t.tz_convert(IST)
                except Exception:
                    return pd.NaT

        idx = getattr(series, "index", None)
        return pd.Series([_convert(x) for x in series], index=idx)


def append_frame(csv_path: Path, new_rows: pd.DataFrame, dry_run: bool = False) -> int:
    if new_rows.empty:
        return 0
    existing = pd.read_csv(csv_path)
    combined = pd.concat([existing, new_rows], ignore_index=True)
    combined["date"] = _ensure_ist(combined["date"])
    combined.drop_duplicates(subset=["date"], keep="last", inplace=True)
    combined.sort_values("date", inplace=True)
    if dry_run:
        print(f"DRY-RUN: would append {len(combined) - len(existing)} rows to {csv_path}")
    else:
        combined.to_csv(csv_path, index=False)
        print(f"Updated {csv_path.name}: +{len(combined) - len(existing)} rows")
    return max(0, len(combined) - len(existing))


def update_symbol_csvs(
    kite: KiteConnect,
    token: int,
    ctx: SymbolContext,
    dry_run: bool = False,
) -> Dict[str, int]:
    deltas: Dict[str, int] = {}
    for interval, meta in INTERVAL_SPECS.items():
        suffix = meta["suffix"]
        tolerance = meta["tolerance"]
        csv_path = ctx.daily_csv if suffix == "daily" else ctx.hourly_csv
        if not csv_path.exists():
            print(f"Missing base CSV for {ctx.symbol} ({csv_path}); skipping this interval")
            deltas[suffix] = 0
            continue
        try:
            last_ts = latest_timestamp(csv_path)
        except ValueError as exc:
            print(f"Skipping {ctx.symbol} {suffix}: {exc}")
            deltas[suffix] = 0
            continue
        start, end = kite_datetime_bounds(last_ts, tolerance)
        if dry_run:
            print(f"DRY-RUN: would call Kite for {ctx.symbol} {interval} from {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M}")
            deltas[suffix] = 0
            continue
        fresh = None
        for attempt in range(1, MAX_FETCH_RETRIES + 1):
            try:
                fresh = fetch_increment(kite, token, interval, start, end)
                break
            except ReadTimeout as exc:
                if attempt == MAX_FETCH_RETRIES:
                    raise RuntimeError(
                        f"Kite API timeout while fetching {ctx.symbol} {interval} after {MAX_FETCH_RETRIES} attempts"
                    ) from exc
                wait = RETRY_BACKOFF_SECONDS * attempt
                print(
                    f"Timeout fetching {ctx.symbol} {interval} (attempt {attempt}/{MAX_FETCH_RETRIES}); retrying in {wait}s"
                )
                time.sleep(wait)
            except KiteException as exc:
                raise RuntimeError(f"Kite API error while fetching {ctx.symbol} {interval}: {exc}") from exc
        if fresh is None:
            fresh = pd.DataFrame()
        added = append_frame(csv_path, fresh, dry_run=dry_run)
        deltas[suffix] = added
    return deltas


def run_builder(goldeneye_root: Path, raw_root: Path, bucket: str, symbols: Sequence[str], dry_run: bool = False) -> None:
    if not symbols:
        return
    cmd = [
        sys.executable,
        str(TOOLS_ROOT / "goldeneye_builder.py"),
        "--root",
        str(raw_root),
        "--goldeneye-root",
        str(goldeneye_root),
        "--buckets",
        bucket,
        "--symbols",
        *symbols,
    ]
    env = os.environ.copy()
    env.setdefault("GOLDENEYE_NO_PLOTS", "1")
    print(f"Recomputing cumulative moments for bucket {bucket}: {', '.join(symbols)}")
    if dry_run:
        print("DRY-RUN:", " ".join(cmd))
        return
    subprocess.run(cmd, check=True, env=env, cwd=str(PROJECT_ROOT))


def make_env(data_dir: Path, symbol: str, bucket: str, env_kwargs: Dict[str, object] | None = None):
    kwargs = env_kwargs or {}

    def _init():
        env = GoldenEyeEnv(str(data_dir), symbol, bucket, **kwargs)
        return Monitor(env)

    return _init


def refine_model(
    goldeneye_root: Path,
    model_dir: Path,
    symbol: str,
    bucket: str,
    timesteps: int,
    n_envs: int | None,
    checkpoint_tag: str,
    train_start_date: str | None,
    train_end_date: str | None,
    refine_start_date: str | None,
    refine_end_date: str | None,
    refine_timesteps: int,
    dry_run: bool = False,
) -> None:
    model_path = model_dir / f"sac_{symbol}.zip"
    if not model_path.exists():
        if dry_run:
            print(f"DRY-RUN: model checkpoint missing for {symbol}; would skip refinement ({model_path})")
            return
        print(f"Model checkpoint not found for {symbol}; skipping refinement ({model_path})")
        return

    if n_envs is None:
        cpu = os.cpu_count() or 1
        n_envs = max(1, cpu - 1)

    env_kwargs: Dict[str, object] = {
        "start_date": train_start_date,
        "end_date": train_end_date,
    }
    env_fns = [make_env(goldeneye_root, symbol, bucket, env_kwargs) for _ in range(n_envs)]
    vec_env = SubprocVecEnv(env_fns) if n_envs > 1 else DummyVecEnv(env_fns)
    base_observation_bounds = (
        np.array(vec_env.observation_space.low, dtype=np.float32),
        np.array(vec_env.observation_space.high, dtype=np.float32),
    )

    print(f"Refining SAC model for {symbol} with {n_envs} env(s) and {timesteps:,} extra steps")
    if dry_run:
        print(f"DRY-RUN: would load {model_path} and continue learning")
        return

    load_custom_objects = {
        "observation_space": vec_env.observation_space,
        "action_space": vec_env.action_space,
    }
    model = SAC.load(str(model_path), env=vec_env, custom_objects=load_custom_objects)
    model.learn(total_timesteps=timesteps, reset_num_timesteps=False)

    if refine_timesteps > 0:
        refine_kwargs: Dict[str, object] = {
            "start_date": refine_start_date,
            "end_date": refine_end_date,
            "observation_bounds": base_observation_bounds,
        }
        refine_env_fns = [make_env(goldeneye_root, symbol, bucket, refine_kwargs) for _ in range(n_envs)]
        refine_vec_env = SubprocVecEnv(refine_env_fns) if n_envs > 1 else DummyVecEnv(refine_env_fns)
        model.observation_space = refine_vec_env.observation_space
        model.action_space = refine_vec_env.action_space
        model.set_env(refine_vec_env)
        window_desc = f"{refine_start_date or '-inf'} -> {refine_end_date or '+inf'}"
        print(
            f"Running fine-tune window for {symbol} over {window_desc} with {refine_timesteps:,} steps"
        )
        model.learn(total_timesteps=refine_timesteps, reset_num_timesteps=False)

    out_path = model_path if not checkpoint_tag else model_dir / f"sac_{symbol}{checkpoint_tag}.zip"
    model.save(str(out_path))
    print(f"Saved refined model to {out_path}")


def main() -> None:
    args = parse_args()
    if args.all:
        # Gather all symbol directories under P1/P2/P3
        symbols = []
        for bucket in RAW_BUCKETS:
            bucket_dir = args.raw_root / bucket
            if not bucket_dir.exists():
                continue
            for p in bucket_dir.iterdir():
                if p.is_dir():
                    symbols.append(p.name.upper())
    else:
        symbols = [sym.upper() for sym in (args.symbols or [])]
    contexts: Dict[str, SymbolContext] = {}
    for sym in symbols:
        ctx = ensure_symbol_context(sym, args.raw_root)
        if not ctx.daily_csv.exists() and not ctx.hourly_csv.exists():
            print(f"Skipping {sym}: missing both daily and hourly CSVs (run bulk download once to include it)" )
            continue
        contexts[sym] = ctx

    kite = None
    token_map: Dict[str, int] = {}
    if not args.skip_fetch:
        if not args.api_key or not args.access_token:
            raise SystemExit("Kite credentials missing. Set KITE_API_KEY/KITE_ACCESS_TOKEN or pass via CLI flags.")
        kite = KiteConnect(api_key=args.api_key)
        kite.set_access_token(args.access_token)
        print("Downloading NSE instrument dump...")
        token_map = load_token_map(kite)

    # 1) Append OHLCV data
    if not args.skip_fetch:
        for sym, ctx in contexts.items():
            token = token_map.get(sym)
            if token is None:
                print(f"Instrument token missing for {sym}; skipping fetch")
                continue
            delta = update_symbol_csvs(kite, token, ctx, dry_run=args.dry_run)
            print(f"{sym}: added {delta['daily']} daily and {delta['hourly']} hourly rows")

    # 2) Rebuild cumulative stats per bucket
    if not args.skip_builder:
        by_bucket: Dict[str, List[str]] = {bucket: [] for bucket in RAW_BUCKETS}
        for sym, ctx in contexts.items():
            by_bucket[ctx.bucket].append(sym)
        for bucket, bucket_symbols in by_bucket.items():
            if bucket_symbols:
                run_builder(
                    goldeneye_root=args.goldeneye_root,
                    raw_root=args.raw_root,
                    bucket=bucket,
                    symbols=bucket_symbols,
                    dry_run=args.dry_run,
                )

    # 3) Continue SAC training
    if not args.skip_train:
        for sym, ctx in contexts.items():
            refine_model(
                goldeneye_root=args.goldeneye_root,
                model_dir=args.model_dir,
                symbol=sym,
                bucket=ctx.bucket,
                timesteps=args.timesteps,
                n_envs=args.n_envs,
                checkpoint_tag=args.checkpoint_tag,
                train_start_date=args.train_start_date,
                train_end_date=args.train_end_date,
                refine_start_date=args.refine_start_date,
                refine_end_date=args.refine_end_date,
                refine_timesteps=args.refine_timesteps,
                dry_run=args.dry_run,
            )


if __name__ == "__main__":
    main()
