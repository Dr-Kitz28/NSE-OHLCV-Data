import argparse
from pathlib import Path
import gymnasium as gym
from stable_baselines3 import SAC
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv
from stable_baselines3.common.monitor import Monitor
import os
import sys
import multiprocessing

# Add tools to path
sys.path.append(str(Path(__file__).parent))
from goldeneye_env import GoldenEyeEnv

def make_env(data_dir, symbol, bucket):
    def _init():
        env = GoldenEyeEnv(data_dir, symbol, bucket)
        env = Monitor(env)
        return env
    return _init

def main():
    parser = argparse.ArgumentParser(description="Train GoldenEye SAC Agent")
    parser.add_argument("--symbol", type=str, default="TIMETECHNO", help="Symbol to train on")
    parser.add_argument("--bucket", type=str, default="P3", help="Bucket")
    parser.add_argument("--data-dir", type=str, default="D:/Trading Strategies/GoldenEye", help="Data root")
    parser.add_argument("--timesteps", type=int, default=10000, help="Training timesteps")
    parser.add_argument("--model-dir", type=str, default="models/goldeneye", help="Model save directory")
    parser.add_argument("--n-envs", type=int, default=None, help="Number of parallel environments (default: cpu_count-1)")
    
    args = parser.parse_args()
    
    # Determine number of parallel environments
    if args.n_envs is None:
        cpu = os.cpu_count() or 1
        n_envs = max(1, cpu - 1)
    else:
        n_envs = max(1, int(args.n_envs))

    # Create vectorized environments. Use SubprocVecEnv for true CPU parallelism when n_envs>1
    env_fns = [make_env(args.data_dir, args.symbol, args.bucket) for _ in range(n_envs)]
    if n_envs == 1:
        env = DummyVecEnv(env_fns)
    else:
        # On Windows SubprocVecEnv uses multiprocessing; ensure spawn-safe code (our env is top-level)
        env = SubprocVecEnv(env_fns)

    print(f"Using {n_envs} environment(s) for training")
    
    # Initialize SAC
    # MlpPolicy is suitable for vector observations
    model = SAC("MlpPolicy", env, verbose=1, tensorboard_log=f"{args.model_dir}/logs")
    
    print(f"Starting training on {args.symbol} for {args.timesteps} timesteps...")
    model.learn(total_timesteps=args.timesteps)
    
    # Save model
    os.makedirs(args.model_dir, exist_ok=True)
    save_path = f"{args.model_dir}/sac_{args.symbol}"
    model.save(save_path)
    print(f"Model saved to {save_path}")

if __name__ == "__main__":
    main()
