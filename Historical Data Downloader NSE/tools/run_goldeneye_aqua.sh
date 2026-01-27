#!/bin/bash
#
# GoldenEye Training Script for AQUA HPC
# Runs 3 parallel training jobs (aqua's job limit)
#
# Usage:
#   chmod +x run_goldeneye_aqua.sh
#   ./run_goldeneye_aqua.sh
#
# Or submit via job scheduler:
#   qsub run_goldeneye_aqua.sh

# Configuration
DATA_DIR="${HOME}/TS/GoldenEye"
MODEL_DIR="${HOME}/TS/GoldenEye/models"
TOOLS_DIR="${HOME}/TS/Historical Data Downloader NSE/tools"
TIMESTEPS=10000
TOTAL_JOBS=3

# Activate conda/python environment if needed
# source ~/miniconda3/bin/activate
# conda activate rl_env

# Check if required packages are installed
echo "Checking Python environment..."
python3 -c "import stable_baselines3; import gymnasium" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Installing required packages..."
    pip install stable-baselines3[extra] gymnasium pandas numpy
fi

# Create log directory
LOG_DIR="${MODEL_DIR}/logs"
mkdir -p "$LOG_DIR"

echo "=========================================="
echo "GoldenEye Training on AQUA"
echo "=========================================="
echo "Data Directory: $DATA_DIR"
echo "Model Directory: $MODEL_DIR"
echo "Timesteps: $TIMESTEPS"
echo "Running $TOTAL_JOBS parallel jobs"
echo "=========================================="

cd "$TOOLS_DIR"

# Launch 3 parallel jobs
for job_id in 0 1 2; do
    log_file="${LOG_DIR}/training_job${job_id}.log"
    echo "Starting Job $job_id -> $log_file"
    
    nohup python3 train_all_goldeneye.py \
        --data-dir "$DATA_DIR" \
        --model-dir "$MODEL_DIR" \
        --timesteps $TIMESTEPS \
        --job-id $job_id \
        --total-jobs $TOTAL_JOBS \
        --device auto \
        --resume \
        > "$log_file" 2>&1 &
    
    echo "  PID: $!"
done

echo ""
echo "All jobs launched! Monitor with:"
echo "  tail -f ${LOG_DIR}/training_job*.log"
echo ""
echo "Check progress with:"
echo "  cat ${MODEL_DIR}/checkpoint_job*.json | jq '.completed | length'"
