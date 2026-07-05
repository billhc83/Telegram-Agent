#!/usr/bin/env python3
"""Patch the Telegram AI Assistant n8n workflow to add the search_project_rag tool
to the Coding Agent, mirroring the existing Memory — Coding (search_memory) pattern."""

import json

SRC = '/mnt/data/projects/telegram-agent/n8n/live-workflow.json'
OUT = '/mnt/data/projects/telegram-agent/n8n/main-agent-patched.json'

with open(SRC) as f:
    data = json.load(f)

wf = data[0] if isinstance(data, list) else data
assert wf['name'] == 'Telegram AI Assistant'

nodes = wf['nodes']
connections = wf['connections']

names = {n['name'] for n in nodes}
assert 'Coding Agent' in names
assert 'Project RAG — Coding' not in names, 'already patched'

embeddings_node = {
    "id": "rag00015-0000-4000-0000-000000000015",
    "name": "Embeddings — Project RAG (Coding)",
    "type": "@n8n/n8n-nodes-langchain.embeddingsOllama",
    "position": [992, 1600],
    "parameters": {
        "model": "nomic-embed-text:latest"
    },
    "credentials": {
        "ollamaApi": {"id": "Qs8Dh8bkuU19W10U", "name": "Ollama account"}
    },
    "typeVersion": 1
}

tool_node = {
    "id": "rag00016-0000-4000-0000-000000000016",
    "name": "Project RAG — Coding",
    "type": "@n8n/n8n-nodes-langchain.vectorStoreQdrant",
    "position": [912, 1392],
    "parameters": {
        "mode": "retrieve-as-tool",
        "options": {},
        "toolName": "search_project_rag",
        "toolDescription": "Search indexed project files (code, docs, configs) across /mnt/data/projects by semantic similarity. Returns matching chunks with filepath, filename, filetype, project, chunk_type, topics, and summary metadata.",
        "qdrantCollection": {
            "__rl": True,
            "mode": "id",
            "value": "project_rag"
        }
    },
    "credentials": {
        "qdrantApi": {"id": "aWAok1h2HHdNtmht", "name": "Qdrant account"}
    },
    "typeVersion": 1.1
}

nodes.append(embeddings_node)
nodes.append(tool_node)

connections["Embeddings — Project RAG (Coding)"] = {
    "ai_embedding": [[{"node": "Project RAG — Coding", "type": "ai_embedding", "index": 0}]]
}
connections["Project RAG — Coding"] = {
    "ai_tool": [[{"node": "Coding Agent", "type": "ai_tool", "index": 0}]]
}

with open(OUT, 'w') as f:
    json.dump(wf, f, indent=2, ensure_ascii=False)

print(f'Written: {OUT}')
print(f'Total nodes: {len(nodes)}')
