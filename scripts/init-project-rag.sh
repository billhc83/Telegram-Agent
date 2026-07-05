#!/usr/bin/env bash
# Creates the Qdrant collection used to index /mnt/data/projects for RAG.
# nomic-embed-text produces 768-dimensional vectors.
# Run once before the project-rag-index n8n workflow's first execution.

set -e
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
COLLECTION="project_rag"

echo "Waiting for Qdrant to be ready..."
until curl -sf "${QDRANT_URL}/healthz" > /dev/null; do sleep 2; done

echo "Creating collection '${COLLECTION}'..."
curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}" \
  -H "Content-Type: application/json" \
  -d '{
    "vectors": {
      "size": 768,
      "distance": "Cosine"
    },
    "optimizers_config": {
      "default_segment_number": 2
    }
  }' | python3 -m json.tool

echo ""
echo "Collection '${COLLECTION}' is ready."
