#!/bin/bash
# Ingest Volve surfaces EPC into local OpenETPServer
# Requires: docker compose services running (see docker-compose.yaml)
#
# Usage: ./demo/epc/ingest.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ETP_URL="ws://localhost:9002"
DATASPACE="demo/Volve"
EPC_FILE="$SCRIPT_DIR/volve.surfaces.epc"

echo "=== Local OpenETPServer EPC Ingest ==="
echo "  ETP URL:    $ETP_URL"
echo "  Dataspace:  $DATASPACE"
echo "  EPC file:   $EPC_FILE"
echo ""

# Use the open-etp-server image (which includes the client CLI)
ETP_IMAGE="community.opengroup.org:5555/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server/open-etp-server-main:latest"

# Wait for server to be ready
echo "Waiting for ETP server..."
for i in $(seq 1 30); do
    if docker run --rm --network host "$ETP_IMAGE" \
        openETPServer probe -S "$ETP_URL" --ping 2>/dev/null | grep -q "pong\|alive\|OK"; then
        echo "  Server is ready!"
        break
    fi
    sleep 2
done

# Create dataspace
echo ""
echo "Creating dataspace '$DATASPACE'..."
docker run --rm --network host "$ETP_IMAGE" \
    openETPServer space -S "$ETP_URL" --new -s "$DATASPACE" 2>&1 || true

# Import EPC
echo ""
echo "Importing EPC: $(basename $EPC_FILE)..."
docker run --rm --network host \
    -v "$SCRIPT_DIR:/data" \
    "$ETP_IMAGE" \
    openETPServer space -S "$ETP_URL" -s "$DATASPACE" \
    --import-epc "/data/$(basename $EPC_FILE)"

# List content
echo ""
echo "Dataspace contents:"
docker run --rm --network host "$ETP_IMAGE" \
    openETPServer space -S "$ETP_URL" -s "$DATASPACE" --stats

echo ""
echo "=== Done! ==="
echo ""
echo "To connect GraphQL to this database, set:"
echo '  export GRAPHQL_PG_CONN_STRING="host=localhost port=5433 dbname=openetp user=tester password=tester"'
echo ""
echo "Then restart the ORES app and use the GraphQL panel."
