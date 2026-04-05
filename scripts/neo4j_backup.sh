#!/bin/bash
# Neo4j backup to external disk — runs every 3 days via cron
# Keeps the 3 most recent backups to save space

set -euo pipefail

BACKUP_DIR="/Volumes/External1/neo4j/backup"
NEO4J_DATA="/opt/homebrew/Cellar/neo4j/2026.02.2/libexec/data"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/neo4j_backup_${TIMESTAMP}.tar.gz"
MAX_BACKUPS=3

# Check external disk is mounted
if [ ! -d "$BACKUP_DIR" ]; then
    echo "$(date): External disk not mounted — skipping backup" >> /opt/homebrew/var/log/neo4j_backup.log
    exit 0
fi

# Check Neo4j data exists
if [ ! -d "$NEO4J_DATA/databases" ]; then
    echo "$(date): Neo4j data not found at $NEO4J_DATA — skipping" >> /opt/homebrew/var/log/neo4j_backup.log
    exit 1
fi

echo "$(date): Starting Neo4j backup to $BACKUP_FILE" >> /opt/homebrew/var/log/neo4j_backup.log

# Tar the data directory (databases + dbms, skip transaction logs to save space)
tar -czf "$BACKUP_FILE" \
    -C "$NEO4J_DATA" \
    databases dbms server_id 2>/dev/null

SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
echo "$(date): Backup complete: $BACKUP_FILE ($SIZE)" >> /opt/homebrew/var/log/neo4j_backup.log

# Remove old backups, keep only MAX_BACKUPS most recent
cd "$BACKUP_DIR"
ls -t neo4j_backup_*.tar.gz 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | xargs rm -f 2>/dev/null

echo "$(date): Cleanup done — $(ls neo4j_backup_*.tar.gz 2>/dev/null | wc -l | tr -d ' ') backups kept" >> /opt/homebrew/var/log/neo4j_backup.log
