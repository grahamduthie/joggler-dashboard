#!/usr/bin/env bash
# Normal production deployment for Dashboard/Nearby. Run from the repository root on the Mac.
# Runtime state and credentials deliberately remain on the VM.
set -euo pipefail

cloud_host=gduthie@cloud.gdx.org.uk
cloud_dir=/home/gduthie/joggler

rsync -a \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude 'venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '*.log' \
  --exclude 'hive-credentials.json' \
  --exclude 'hive-tokens.json' \
  --exclude 'bus-stops.json' \
  --exclude 'bus-route-stops.json' \
  --exclude 'berth_chain.json' \
  --exclude 'signals_learned.json' \
  --exclude 'calibration_log.jsonl' \
  --exclude 'train-evidence.jsonl' \
  --exclude 'airport-names.json' \
  --exclude 'logos/' \
  --exclude 'aircraft-info/' \
  ./ "$cloud_host:$cloud_dir/"

ssh "$cloud_host" "set -eu
  '$cloud_dir/venv/bin/pip' install --quiet -r '$cloud_dir/requirements.txt'
  sudo supervisorctl restart joggler
  sleep 2
  curl --fail --silent http://127.0.0.1:8002/health
  printf '\\nCloud deployment complete.\\n'"
