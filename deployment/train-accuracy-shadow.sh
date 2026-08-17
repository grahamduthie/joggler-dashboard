#!/usr/bin/env bash
# Safe first release for the train-accuracy work. It deploys only the four
# compatible train files; the visible pages remain on model=legacy unless a
# validator opens them with ?train_model=v2.
set -euo pipefail

repo_dir=$(cd "$(dirname "$0")/.." && pwd)
cloud_host=gduthie@cloud.gdx.org.uk
cloud_dir=/home/gduthie/joggler
release_id="train-accuracy-shadow-$(date -u +%Y%m%dT%H%M%SZ)"

cd "$repo_dir"
python3 -m unittest tests/test_train_accuracy.py
python3 -m py_compile transport-proxy.py
node --check train-display.js

ssh "$cloud_host" "set -eu
  backup='$cloud_dir/release-backups/$release_id'
  mkdir -p \"\$backup\"
  for file in transport-proxy.py trains.html now.html train-display.js train-shadow.html; do
    if [ -f '$cloud_dir/'\"\$file\" ]; then cp -p '$cloud_dir/'\"\$file\" \"\$backup/\"; fi
  done"

rsync -a --relative \
  transport-proxy.py trains.html now.html train-display.js train-shadow.html \
  "$cloud_host:$cloud_dir/"

ssh "$cloud_host" "set -eu
  sudo supervisorctl restart joggler
  sleep 2
  curl --fail --silent http://127.0.0.1:8002/health
  printf '\nShadow release complete: $release_id\n'
  curl --fail --silent 'http://127.0.0.1:8002/api/trains?model=legacy' | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"model\"] == \"legacy\"; print(\"legacy rows:\", len(d[\"trains\"]))'
  curl --fail --silent 'http://127.0.0.1:8002/api/trains?model=v2&shadow=1' | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"model\"] == \"v2\" and \"shadow\" in d; print(\"v2 rows:\", len(d[\"trains\"]), \"row disagreements:\", d[\"shadow\"][\"disagreement_count\"])'
  curl --fail --silent 'http://127.0.0.1:8002/api/train-evidence' | python3 -c 'import json,sys; d=json.load(sys.stdin); assert \"models\" in d and \"v2_by_source\" in d; print(\"evidence crossings:\", d[\"crossings_scored\"])'"

printf '\nNo kiosk page has changed model. Validate with:\n'
printf '  https://nearby.gdx.org.uk/trains?train_model=v2&train_shadow=1\n'
printf '  https://nearby.gdx.org.uk/now?train_model=v2&train_shadow=1\n'
printf 'Rollback backup: %s/release-backups/%s\n' "$cloud_dir" "$release_id"
