#!/bin/bash
# Export all active n8n workflows to disk.
# Run this after any UI changes: ./scripts/export-workflows.sh
set -euo pipefail

N8N_URL="${N8N_URL:-http://localhost:5678}"
OUT_DIR="$(dirname "$0")/../n8n/workflows"
mkdir -p "$OUT_DIR"

echo "Fetching workflow list from $N8N_URL ..."
workflows=$(curl -sf "$N8N_URL/api/v1/workflows" \
  -H "X-N8N-API-KEY: ${N8N_API_KEY:?set N8N_API_KEY}" | \
  python3 -c "import sys,json; [print(w['id'],w['name']) for w in json.load(sys.stdin)['data']]")

echo "$workflows" | while read -r id name; do
  safe=$(echo "$name" | tr '[:upper:] ' '[:lower:]_' | tr -cd '[:alnum:]_-')
  file="$OUT_DIR/${safe}.json"
  curl -sf "$N8N_URL/api/v1/workflows/$id" \
    -H "X-N8N-API-KEY: $N8N_API_KEY" | \
    python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))" > "$file"
  echo "  saved: $file"
done

echo "Done. Commit the updated workflows/ directory."
