#!/usr/bin/env python3
"""
Train GoldenEye SAC Agent on all available stocks.
Designed for running on HPC clusters like aqua.iitm.ac.in

Usage:
    # Train all stocks sequentially
    python train_all_goldeneye.py --data-dir ~/TS/GoldenEye --model-dir ~/TS/GoldenEye/models
    
    # Train specific bucket
    python train_all_goldeneye.py --bucket P1 --data-dir ~/TS/GoldenEye
    
    # Run 3 parallel jobs (split by job-id for aqua's 3-job limit)
    python train_all_goldeneye.py --job-id 0 --total-jobs 3
    python train_all_goldeneye.py --job-id 1 --total-jobs 3
    python train_all_goldeneye.py --job-id 2 --total-jobs 3
"""

import argparse
from pathlib import Path
import os
import sys
import json
import time
from datetime import datetime

# Add tools to path
sys.path.append(str(Path(__file__).parent))

def get_all_symbols(data_dir: Path, buckets: list) -> list:
    """Get all symbols from all specified buckets."""
    symbols = []
    for bucket in buckets:
        # Check daily directory
        daily_path = data_dir / "daily" / bucket
        if daily_path.exists():
            for symbol_dir in daily_path.iterdir():
                if symbol_dir.is_dir():
                    moments_file = symbol_dir / "cumulative_moments.csv"
                    if moments_file.exists():
                        symbols.append((symbol_dir.name, bucket, "daily"))
        
        # Check hourly directory
        hourly_path = data_dir / "hourly" / bucket
        if hourly_path.exists():
            for symbol_dir in hourly_path.iterdir():
                if symbol_dir.is_dir():
                    moments_file = symbol_dir / "cumulative_moments.csv"
                    if moments_file.exists():
                        # Only add if not already in daily
                        if not any(s[0] == symbol_dir.name and s[1] == bucket for s in symbols):
                            symbols.append((symbol_dir.name, bucket, "hourly"))
    
    return sorted(symbols, key=lambda x: (x[1], x[0]))

def load_checkpoint(checkpoint_file: Path) -> dict:
    """Load training checkpoint."""
    if checkpoint_file.exists():
        with open(checkpoint_file, 'r') as f:
            return json.load(f)
    return {"completed": [], "failed": [], "in_progress": None}

def save_checkpoint(checkpoint_file: Path, checkpoint: dict):
    """Save training checkpoint."""
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    with open(checkpoint_file, 'w') as f:
        json.dump(checkpoint, f, indent=2)

def train_symbol(symbol: str, bucket: str, data_dir: str, model_dir: str, 
                 timesteps: int, n_envs: int, device: str) -> dict:
    """Train a single symbol and return results."""
    from goldeneye_env import GoldenEyeEnv
    from stable_baselines3 import SAC
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv
    from stable_baselines3.common.monitor import Monitor
    
    result = {
        "symbol": symbol,
        "bucket": bucket,
        "success": False,
        "error": None,
        "timesteps": timesteps,
        "training_time": 0,
    }
    
    start_time = time.time()
    
    try:
        def make_env():
            env = GoldenEyeEnv(data_dir, symbol, bucket)
            env = Monitor(env)
            return env
        
        # Create environment(s)
        env_fns = [make_env for _ in range(n_envs)]
        if n_envs == 1:
            env = DummyVecEnv(env_fns)
        else:
            env = SubprocVecEnv(env_fns)
        
        # Create model directory for this symbol
        symbol_model_dir = Path(model_dir) / bucket / symbol
        symbol_model_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize SAC with GPU support if available
        model = SAC(
            "MlpPolicy", 
            env, 
            verbose=0,
            device=device,
            tensorboard_log=str(symbol_model_dir / "logs")
        )
        
        # Train
        model.learn(total_timesteps=timesteps)
        
        # Save model
        save_path = symbol_model_dir / f"sac_{symbol}"
        model.save(str(save_path))
        
        result["success"] = True
        result["model_path"] = str(save_path)
        
        # Clean up
        env.close()
        
    except Exception as e:
        result["error"] = str(e)
        
    result["training_time"] = time.time() - start_time
    return result

def main():
    parser = argparse.ArgumentParser(description="Train GoldenEye SAC Agent on all stocks")
    parser.add_argument("--data-dir", type=str, default="~/TS/GoldenEye", 
                        help="GoldenEye data directory")
    parser.add_argument("--model-dir", type=str, default="~/TS/GoldenEye/models",
                        help="Model save directory")
    parser.add_argument("--buckets", nargs="*", default=["P1", "P2", "P3"],
                        help="Buckets to process")
    parser.add_argument("--timesteps", type=int, default=10000,
                        help="Training timesteps per symbol")
    parser.add_argument("--n-envs", type=int, default=1,
                        help="Number of parallel environments per training")
    parser.add_argument("--job-id", type=int, default=0,
                        help="Job ID for parallel execution (0-indexed)")
    parser.add_argument("--total-jobs", type=int, default=1,
                        help="Total number of parallel jobs")
    parser.add_argument("--device", type=str, default="auto",
                        help="Device: 'cuda', 'cpu', or 'auto'")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from checkpoint")
    parser.add_argument("--symbols", nargs="*", default=None,
                        help="Specific symbols to train (optional)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of symbols to train")
    
    args = parser.parse_args()
    
    # Expand paths
    data_dir = Path(os.path.expanduser(args.data_dir))
    model_dir = Path(os.path.expanduser(args.model_dir))
    
    if not data_dir.exists():
        print(f"Error: Data directory {data_dir} does not exist.")
        return 1
    
    model_dir.mkdir(parents=True, exist_ok=True)
    
    # Get all symbols
    print(f"Scanning for symbols in {data_dir}...")
    all_symbols = get_all_symbols(data_dir, args.buckets)
    
    if args.symbols:
        # Filter to specific symbols
        all_symbols = [(s, b, t) for s, b, t in all_symbols if s in args.symbols]
    
    print(f"Found {len(all_symbols)} symbols total.")
    
    # Split symbols among jobs
    job_symbols = [s for i, s in enumerate(all_symbols) if i % args.total_jobs == args.job_id]
    print(f"Job {args.job_id}/{args.total_jobs}: Processing {len(job_symbols)} symbols")
    
    if args.limit:
        job_symbols = job_symbols[:args.limit]
        print(f"Limited to {args.limit} symbols")
    
    # Checkpoint file for this job
    checkpoint_file = model_dir / f"checkpoint_job{args.job_id}.json"
    checkpoint = load_checkpoint(checkpoint_file) if args.resume else {"completed": [], "failed": [], "in_progress": None}
    
    # Filter out completed symbols
    completed_set = set(checkpoint["completed"])
    pending_symbols = [(s, b, t) for s, b, t in job_symbols if f"{b}/{s}" not in completed_set]
    
    print(f"Pending: {len(pending_symbols)} symbols ({len(completed_set)} already completed)")
    
    # Detect device
    device = args.device
    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
    print(f"Using device: {device}")
    
    # Train each symbol
    success_count = 0
    fail_count = 0
    
    for i, (symbol, bucket, data_type) in enumerate(pending_symbols):
        key = f"{bucket}/{symbol}"
        checkpoint["in_progress"] = key
        save_checkpoint(checkpoint_file, checkpoint)
        
        print(f"\n[{i+1}/{len(pending_symbols)}] Training {symbol} ({bucket}, {data_type})...")
        
        result = train_symbol(
            symbol=symbol,
            bucket=bucket,
            data_dir=str(data_dir),
            model_dir=str(model_dir),
            timesteps=args.timesteps,
            n_envs=args.n_envs,
            device=device
        )
        
        if result["success"]:
            print(f"  ✓ Success: {result['training_time']:.1f}s")
            checkpoint["completed"].append(key)
            success_count += 1
        else:
            print(f"  ✗ Failed: {result['error']}")
            checkpoint["failed"].append({"key": key, "error": result["error"]})
            fail_count += 1
        
        checkpoint["in_progress"] = None
        save_checkpoint(checkpoint_file, checkpoint)
    
    # Summary
    print(f"\n{'='*60}")
    print(f"Training Complete!")
    print(f"  Successful: {success_count}")
    print(f"  Failed: {fail_count}")
    print(f"  Total completed (including previous runs): {len(checkpoint['completed'])}")
    print(f"{'='*60}")
    
    # Save final summary
    summary_file = model_dir / f"summary_job{args.job_id}.json"
    summary = {
        "job_id": args.job_id,
        "total_jobs": args.total_jobs,
        "completed_count": len(checkpoint["completed"]),
        "failed_count": len(checkpoint["failed"]),
        "timestamp": datetime.now().isoformat(),
        "device": device,
        "timesteps": args.timesteps,
    }
    with open(summary_file, 'w') as f:
        json.dump(summary, f, indent=2)
    
    return 0 if fail_count == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
