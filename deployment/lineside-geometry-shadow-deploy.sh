#!/usr/bin/env bash
# Publish only rollback-safe Lineside geometry shadow assets.  This deliberately
# does not restart the TD consumer or overwrite learner/runtime state.
set -euo pipefail

cloud_host=gduthie@cloud.gdx.org.uk
cloud_dir=/home/gduthie/joggler
release_id="lineside-geometry-$(date -u +%Y%m%dT%H%M%SZ)"

ssh "$cloud_host" "set -eu
  backup='$cloud_dir/release-backups/$release_id'
  mkdir -p \"\$backup\" \"$cloud_dir/data\"
  for asset in lineside.html lineside-layout-v2.js data/lineside-layout-v1.json data/lineside-layout-v2.json; do
    if [ -f \"$cloud_dir/\$asset\" ]; then
      mkdir -p \"\$backup/\$(dirname \"\$asset\")\"
      cp -p \"$cloud_dir/\$asset\" \"\$backup/\$asset\"
    fi
  done"

scp lineside.html lineside-layout-v2.js "$cloud_host:$cloud_dir/"
scp data/lineside-layout-v1.json data/lineside-layout-v2.json "$cloud_host:$cloud_dir/data/"

ssh "$cloud_host" "set -eu
  curl --fail --silent http://127.0.0.1:8002/lineside?layout=v2 >/dev/null
  test -s '$cloud_dir/lineside-layout-v2.js'
  printf '%s\\n' 'Lineside geometry shadow deployed: $release_id'"
