#!/usr/bin/env bash
# One-time/bootstrap staging helper. Normal production releases use cloud-deploy.sh.
# It does not change Nginx or the Joggler kiosk URL.
set -euo pipefail

cloud_host=gduthie@cloud.gdx.org.uk
cloud_dir=/home/gduthie/joggler

ssh "$cloud_host" "mkdir -p '$cloud_dir'"
rsync -a \
  --exclude '.git/' --exclude '.env' --exclude 'venv/' --exclude '__pycache__/' \
  --exclude 'dashboard.log' --exclude 'proxy.log' --exclude '*.bak*' \
  ./ "$cloud_host:$cloud_dir/"

ssh "$cloud_host" "python3 -m venv '$cloud_dir/venv' && '$cloud_dir/venv/bin/pip' install -r '$cloud_dir/requirements.txt'"

echo 'Stage complete. Copy protected runtime state separately, then install deployment/joggler.supervisor.conf.'
