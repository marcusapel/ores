#!/bin/bash
# Ingest Sleipner.epc into RDDMS (eqndev/swedev) via openETPServer ETP WSS
#
# Prerequisites:
#   - Docker with osdu-etp-sslclient image
#   - k8s secrets (INSTANCE_EQNDEV_CLIENT_SECRET) or .env with credentials
#
# Usage:
#   cd /home/maap/ores
#   bash demo/ingest_sleipner.sh
#   bash demo/ingest_sleipner.sh --dry-run
#   bash demo/ingest_sleipner.sh --skip-create   # if dataspace already exists

set -e
cd "$(dirname "$0")/.."

python3 demo/drogon/resqml/ingest_resqml_rddms.py \
  --instance eqndev \
  --dataspace maap/sleipner \
  --epc /home/maap/ores/demo/Sleipner.epc \
  "$@"
