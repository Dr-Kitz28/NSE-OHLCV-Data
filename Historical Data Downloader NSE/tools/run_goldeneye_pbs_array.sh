#!/bin/bash
#PBS -N goldeneye_array
#PBS -l select=1:ncpus=8:mem=32gb:ngpus=1
#PBS -l walltime=12:00:00
#PBS -o $HOME/scratch/TS/goldeneye_logs/${PBS_JOBNAME}_$PBS_ARRAY_INDEX.out
#PBS -e $HOME/scratch/TS/goldeneye_logs/${PBS_JOBNAME}_$PBS_ARRAY_INDEX.err
#PBS -J 0-2
#PBS -r y

# NOTE: Adjust 'ngpus' or 'gres' depending on your cluster's PBS config.
# If your cluster uses 'gres=gpu:1' replace select accordingly:
# #PBS -l select=1:ncpus=8:mem=32gb:gres=gpu:1

set -euo pipefail

JOB_ID=${PBS_ARRAY_INDEX}
TOTAL_JOBS=${PBS_ARRAY_COUNT}

# Work in scratch for IO
SCRATCH_DIR="$HOME/scratch/TS"
MODEL_DIR="$SCRATCH_DIR/GoldenEye/models"
TOOLS_DIR="$SCRATCH_DIR/""Historical Data Downloader NSE""/tools"
DATA_DIR="$SCRATCH_DIR/GoldenEye"

mkdir -p "$HOME/goldeneye_logs"
cd "$TOOLS_DIR"

# Load Python/conda module
module purge
module load anaconda3_2023

# Create/activate conda env in scratch (first time only)
SCRATCH_CONDA_PREFIX="$SCRATCH_DIR/conda_envs/goldeneye"
source $(conda info --base)/etc/profile.d/conda.sh || true
if [ ! -d "$SCRATCH_CONDA_PREFIX" ]; then
  # Create a prefix env under scratch (user-writable)
  conda create -y -p "$SCRATCH_CONDA_PREFIX" python=3.9
  conda activate "$SCRATCH_CONDA_PREFIX"
  conda install -y -c conda-forge stable-baselines3 gymnasium pandas numpy tensorboard
  # Install pytorch with CUDA support
  conda install -y -c pytorch pytorch torchvision cudatoolkit=11.7
else
  conda activate "$SCRATCH_CONDA_PREFIX"
fi

# Print runtime info
echo "Running job ${JOB_ID}/${TOTAL_JOBS} on $(hostname)"
python -c "import torch; print('torch', getattr(torch,'__version__',None),'cuda',torch.cuda.is_available(), 'devices', torch.cuda.device_count())"

# Run the training script for the shard this job is responsible for
python train_all_goldeneye.py \
  --data-dir "$DATA_DIR" \
  --model-dir "$MODEL_DIR" \
  --timesteps 50000 \
  --n-envs 1 \
  --job-id ${JOB_ID} \
  --total-jobs ${TOTAL_JOBS} \
  --device auto \
  --resume

# End
