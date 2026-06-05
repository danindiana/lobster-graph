#!/usr/bin/env bash
# snapshot_db.sh
# ─────────────────────────────────────────────────────────────────────────────
# Safely pauses the Neo4j container to execute a native binary snapshot dump.
# Run this from the root of the paper_processor directory.
# ─────────────────────────────────────────────────────────────────────────────
set -e

BOLD="\033[1m"
GREEN="\033[0;32m"
CYAN="\033[0;36m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
RESET="\033[0m"

log()  { echo -e "${CYAN}▶  $*${RESET}"; }
ok()   { echo -e "${GREEN}✓  $*${RESET}"; }
warn() { echo -e "${YELLOW}⚠  $*${RESET}"; }

CONTAINER_NAME="paper-processor-neo4j"
BACKUP_DIR="backups/neo4j_snapshots"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DUMP_FILE="neo4j_snapshot_$TIMESTAMP.dump"

echo -e "\n${BOLD}🦞  Lobster Graph — Native Database Snapshot${RESET}\n"

# Check if the container exists
if ! docker ps -a --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}\$"; then
    warn "Docker container '${CONTAINER_NAME}' not found."
    warn "Ensure you started Neo4j using the command recommended in the installers."
    exit 1
fi

log "Pausing graph database (${CONTAINER_NAME})..."
docker stop "$CONTAINER_NAME" >/dev/null

log "Executing native Neo4j-admin binary dump..."
# We run a temporary container that mounts the same /data volume to execute the dump and output to stdout
docker run --rm \
    --volumes-from "$CONTAINER_NAME" \
    neo4j:latest \
    neo4j-admin database dump neo4j --to-stdout > "$BACKUP_DIR/$DUMP_FILE"

log "Resuming graph database..."
docker start "$CONTAINER_NAME" >/dev/null

echo ""
ok "Snapshot successfully generated!"
echo -e "  ${BOLD}Location:${RESET} $(pwd)/$BACKUP_DIR/$DUMP_FILE"
echo -e "This .dump file can be loaded into any Neo4j instance to instantly restore the graph state."
