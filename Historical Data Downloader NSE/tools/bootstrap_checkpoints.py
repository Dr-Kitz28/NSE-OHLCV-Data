""Train base SAC checkpoints for all NSE symbols with GoldenEye data.

This helper scans the P1/P2/P3 folders under the historical downloader,
verifies that cumulative-moment features exist under the supplied GoldenEye
root, and trains a fresh SAC policy per symbol whenever
``D:/Trading Strategies/GoldenEye/models/sac_<SYMBOL>.zip`` is missing (or when
``--overwrite`` is requested).

The training window can be restricted via ``--train-start-date`` / ``--train-end-date``
so that the resulting checkpoint exactly matches the historical span you intend
for refinement (e.g. train through 2024-12-31, then fine-tune on 2025 data).
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence
from stable_baselines3 import SAC
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

# Local imports
TOOLS_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_ROOT.parent
RAW_BUCKETS = ("P1", "P2", "P3")

import sys

sys.path.append(str(TOOLS_ROOT))
from goldeneye_env import GoldenEyeEnv  # noqa: E402


@dataclass
class SymbolContext:
    symbol: str
    bucket: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate SAC checkpoints for all symbols")
    parser.add_argument("--symbols", nargs="+", help="Optional subset of symbols to train")
    parser.add_argument("--all", action="store_true", help="Train every symbol discovered under P1/P2/P3")
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=PROJECT_ROOT,
        help="Root directory containing the P1/P2/P3 folders (default: repo root)",
    )
    parser.add_argument(
        "--goldeneye-root",
        type=Path,
        default=Path("D:/Trading Strategies/GoldenEye"),
        help="Directory containing the cumulative moment datasets",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=Path("D:/Trading Strategies/GoldenEye/models"),
        help="Destination directory for sac_<SYMBOL>.zip checkpoints",
    )
    parser.add_argument("--timesteps", type=int, default=200_000, help="Training timesteps per symbol")
    parser.add_argument(
        "--n-envs",
        type=int,
        default=None,
        help="Number of parallel envs (default: cpu_count-1, capped at 4 to avoid thrashing)",
    )
    parser.add_argument("--train-start-date", default=None, help="UTC start date (inclusive) for env data filter")
    parser.add_argument(
        "--train-end-date",
        default="2025-01-01",
        help="UTC end date (exclusive) for env data filter (default: first day of 2025)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Retrain even if sac_<SYMBOL>.zip already exists",
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Optional TensorBoard log root (default: <model-dir>/logs)",
    )
    return parser.parse_args()


def discover_symbols(raw_root: Path, requested: Sequence[str] | None, include_all: bool) -> Dict[str, SymbolContext]:
    symbols: Dict[str, SymbolContext] = {}
    target = [sym.upper() for sym in (requested or [])]
    if include_all:
        target = []  # sentinel meaning everything under buckets
    for bucket in RAW_BUCKETS:
        bucket_dir = raw_root / bucket
        if not bucket_dir.exists():
            continue
        for path in bucket_dir.iterdir():
            if not path.is_dir():
                continue
            sym = path.name.upper()
            if not include_all and target and sym not in target:
                continue
            symbols[sym] = SymbolContext(symbol=sym, bucket=bucket)
    missing: List[str] = []
    if not include_all and target:
        for sym in target:
            if sym not in symbols:
                missing.append(sym)
    if missing:
        raise SystemExit(f"Symbols not found under {raw_root}: {', '.join(sorted(missing))}")
    if not symbols:
        raise SystemExit("No symbols discovered; pass --all or a valid --symbols list")
    return symbols


def make_env(goldeneye_root: Path, ctx: SymbolContext, env_kwargs: Dict[str, object]):
    def _init():
        env = GoldenEyeEnv(
            data_dir=str(goldeneye_root),
            symbol=ctx.symbol,
            bucket=ctx.bucket,
            **env_kwargs,
        )
        return Monitor(env)

    return _init


def choose_env_count(n_envs_arg: int | None) -> int:
    if n_envs_arg is not None:
        return max(1, n_envs_arg)
    cpu = os.cpu_count() or 1
    return max(1, min(4, cpu - 1))


def train_symbol(
    ctx: SymbolContext,
    goldeneye_root: Path,
    model_dir: Path,
    timesteps: int,
    n_envs: int,
    env_kwargs: Dict[str, object],
    log_dir: Path,
) -> None:
    env_fns = [make_env(goldeneye_root, ctx, env_kwargs) for _ in range(n_envs)]
    vec_env = SubprocVecEnv(env_fns) if n_envs > 1 else DummyVecEnv(env_fns)

    # Stable-Baselines expects finite bounds; reuse env bounds for logging clarity
    log_root = log_dir / f"SAC_{ctx.symbol}"
    log_root.parent.mkdir(parents=True, exist_ok=True)
    print(f"[TRAIN] {ctx.symbol} ({ctx.bucket}) → {timesteps:,} steps with {n_envs} env(s)")
    model = SAC(
        "MlpPolicy",
        vec_env,
        verbose=1,
        tensorboard_log=str(log_root),
    )
    model.learn(total_timesteps=timesteps)
    out_path = model_dir / f"sac_{ctx.symbol}.zip"
    model.save(str(out_path))
    print(f"[DONE] Saved checkpoint → {out_path}")


def main() -> None:
    args = parse_args()
    contexts = discover_symbols(args.raw_root, args.symbols, args.all)
    args.model_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(args.log_dir) if args.log_dir else args.model_dir / "logs"
    env_kwargs: Dict[str, object] = {
        "start_date": args.train_start_date,
        "end_date": args.train_end_date,
    }
    n_envs = choose_env_count(args.n_envs)

    for sym, ctx in sorted(contexts.items()):
        checkpoint = args.model_dir / f"sac_{sym}.zip"
        if checkpoint.exists() and not args.overwrite:
            print(f"[SKIP] {sym}: checkpoint already exists ({checkpoint})")
            continue
        try:
            train_symbol(ctx, args.goldeneye_root, args.model_dir, args.timesteps, n_envs, env_kwargs, log_dir)
        except Exception as exc:  # noqa: BLE001 - want symbol context in error message
            print(f"[FAIL] {sym}: {exc}")


if __name__ == "__main__":
    main()
