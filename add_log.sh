#!/usr/bin/env bash
# Gzip the latest WoW combat log(s) into ./logs and stage them.
# Usage: ./add_log.sh /path/to/WoWCombatLog-XXXXXX_XXXXXX.txt [more.txt ...]
# Default source if no args: the live Anniversary Logs folder.
set -e
DEST="$(dirname "$0")/logs"
mkdir -p "$DEST"

if [ "$#" -eq 0 ]; then
  set -- "/c/World of Warcraft/_anniversary_/Logs/"WoWCombatLog-*.txt
fi

for f in "$@"; do
  [ -f "$f" ] || { echo "skip (not found): $f"; continue; }
  out="$DEST/$(basename "$f").gz"
  echo "gzip $f -> $out"
  gzip -c "$f" > "$out"
done

echo "Done. Now: git add logs && git commit -m 'add logs' && git push"
