#!/usr/bin/env python3
"""
Insert qwen_rag_search workflow into n8n and wire it to Coding Agent + General Agent.
Run with: python3 insert_rag_search.py
"""
import json, subprocess, sys

WORKFLOW_ID  = "00000011-0000-4000-0000-000000000011"
VERSION_ID   = "00000011-v001-4000-0000-000000000011"
MAIN_AGENT_ID = "13329864-5514-4f83-acf9-a4610cd1e903"
PROJECT_ID   = "Vyx2IP9iq7IBCYYG"
CODING_AGENT_NAME = "Coding Agent"
GENERAL_AGENT_NAME = "General Agent"
NEW_TOOL_NODE_ID = "tool0001-0011-4000-0000-000000000011"


def psql(sql: str) -> tuple[str, str]:
    r = subprocess.run(
        ["docker", "exec", "-i", "telegram-agent-postgres-1",
         "psql", "-U", "n8n", "-d", "n8n", "-v", "ON_ERROR_STOP=1"],
        input=sql.encode(),
        capture_output=True,
    )
    return r.stdout.decode(), r.stderr.decode()


def psql_query(sql: str) -> str:
    """Run a query and return raw value (tuples-only, unaligned)."""
    r = subprocess.run(
        ["docker", "exec", "-i", "telegram-agent-postgres-1",
         "psql", "-U", "n8n", "-d", "n8n", "-t", "-A"],
        input=sql.encode(),
        capture_output=True,
    )
    err = r.stderr.decode()
    if "ERROR" in err:
        raise RuntimeError(f"psql error: {err}")
    return r.stdout.decode().strip()


# ── 1. Build the workflow nodes/connections ───────────────────────────────────

nodes = [
    {
        "parameters": {
            "workflowInputs": {
                "values": [
                    {"name": "query",   "type": "string"},
                    {"name": "project", "type": "string"},
                    {"name": "limit",   "type": "number"},
                ]
            }
        },
        "id": "00000011-0001-4000-0000-000000000011",
        "name": "Execute Workflow Trigger",
        "type": "n8n-nodes-base.executeWorkflowTrigger",
        "typeVersion": 1.1,
        "position": [240, 300],
    },
    {
        "parameters": {
            "jsCode": (
                "const input = $input.first().json;\n"
                "return [{ json: { model: 'qwen3-embedding:4b', prompt: input.query } }];"
            )
        },
        "id": "00000011-0002-4000-0000-000000000011",
        "name": "Build Embed Request",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [460, 300],
    },
    {
        "parameters": {
            "method": "POST",
            "url": "http://host.docker.internal:11434/api/embeddings",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ $json }}",
            "options": {"timeout": 20000},
        },
        "id": "00000011-0003-4000-0000-000000000011",
        "name": "Embed Query",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [680, 300],
    },
    {
        "parameters": {
            "jsCode": (
                "const embedding = $input.first().json.embedding;\n"
                "const trigger = $('Execute Workflow Trigger').first().json;\n"
                "const limit = Number(trigger.limit) || 5;\n"
                "const project = (trigger.project || '').trim();\n\n"
                "const body = {\n"
                "  vector: embedding,\n"
                "  limit: limit,\n"
                "  with_payload: true,\n"
                "  score_threshold: 0.5\n"
                "};\n\n"
                "if (project) {\n"
                "  body.filter = {\n"
                "    must: [{ key: 'metadata.project', match: { value: project } }]\n"
                "  };\n"
                "}\n\n"
                "return [{ json: body }];"
            )
        },
        "id": "00000011-0004-4000-0000-000000000011",
        "name": "Build Search",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [900, 300],
    },
    {
        "parameters": {
            "method": "POST",
            "url": "http://qdrant:6333/collections/project_rag/points/search",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ $json }}",
            "options": {
                "timeout": 10000,
                "response": {"response": {"responseFormat": "json"}},
            },
        },
        "id": "00000011-0005-4000-0000-000000000011",
        "name": "Search Qdrant",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [1120, 300],
    },
    {
        "parameters": {
            "jsCode": (
                "const results = $input.first().json.result || [];\n"
                "const trigger = $('Execute Workflow Trigger').first().json;\n\n"
                "if (results.length === 0) {\n"
                "  return [{ json: { output: `No relevant results found in project files for: \"${trigger.query}\"` } }];\n"
                "}\n\n"
                "const formatted = results.map((r, i) => {\n"
                "  const m = r.payload?.metadata || {};\n"
                "  const content = r.payload?.content || '';\n"
                "  const lines = m.loc?.lines ? ` lines ${m.loc.lines.from}\\u2013${m.loc.lines.to}` : '';\n"
                "  const score = (r.score * 100).toFixed(0);\n"
                "  const summary = m.summary ? `// ${m.summary}\\n` : '';\n"
                "  return `[${i + 1}] ${m.filepath}${lines} (score: ${score}%)\\n\\`\\`\\`${m.filetype || ''}\\n${summary}${content}\\n\\`\\`\\``;\n"
                "}).join('\\n\\n');\n\n"
                "return [{ json: { output: `Project RAG search: \"${trigger.query}\" \\u2014 ${results.length} result(s)\\n\\n${formatted}` } }];"
            )
        },
        "id": "00000011-0006-4000-0000-000000000011",
        "name": "Format Results",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [1340, 300],
    },
]

connections = {
    "Execute Workflow Trigger": {
        "main": [[{"node": "Build Embed Request", "type": "main", "index": 0}]]
    },
    "Build Embed Request": {
        "main": [[{"node": "Embed Query", "type": "main", "index": 0}]]
    },
    "Embed Query": {
        "main": [[{"node": "Build Search", "type": "main", "index": 0}]]
    },
    "Build Search": {
        "main": [[{"node": "Search Qdrant", "type": "main", "index": 0}]]
    },
    "Search Qdrant": {
        "main": [[{"node": "Format Results", "type": "main", "index": 0}]]
    },
}

nodes_json    = json.dumps(nodes)
conns_json    = json.dumps(connections)
settings_json = json.dumps({"executionOrder": "v1"})


# ── 2. Insert the workflow ────────────────────────────────────────────────────

print("Inserting qwen_rag_search workflow...")

already = psql_query(f"SELECT COUNT(*) FROM workflow_entity WHERE id = '{WORKFLOW_ID}';")
if already.strip() == "1":
    print("  Workflow already exists — skipping insert.")
else:
    # workflow_entity.activeVersionId FKs to workflow_history.versionId
    # so workflow_entity must be inserted WITHOUT activeVersionId first,
    # then workflow_history, then we set activeVersionId.
    sql_insert_wf = (
        f"INSERT INTO workflow_entity\n"
        f"  (id, name, active, nodes, connections, settings, \"versionId\",\n"
        f"   \"triggerCount\", \"isArchived\", \"versionCounter\", \"nodeGroups\", \"createdAt\", \"updatedAt\")\n"
        f"VALUES (\n"
        f"  '{WORKFLOW_ID}',\n"
        f"  'qwen_rag_search',\n"
        f"  true,\n"
        f"  $NODES${nodes_json}$NODES$,\n"
        f"  $CONNS${conns_json}$CONNS$,\n"
        f"  $SETS${settings_json}$SETS$,\n"
        f"  '{VERSION_ID}',\n"
        f"  0, false, 1, '[]', NOW(), NOW()\n"
        f");\n\n"
        f"INSERT INTO workflow_history\n"
        f"  (\"versionId\", \"workflowId\", authors, nodes, connections, name, \"nodeGroups\")\n"
        f"VALUES (\n"
        f"  '{VERSION_ID}',\n"
        f"  '{WORKFLOW_ID}',\n"
        f"  'system',\n"
        f"  $NODES${nodes_json}$NODES$,\n"
        f"  $CONNS${conns_json}$CONNS$,\n"
        f"  'qwen_rag_search',\n"
        f"  '[]'\n"
        f");\n\n"
        f"UPDATE workflow_entity SET \"activeVersionId\" = '{VERSION_ID}' WHERE id = '{WORKFLOW_ID}';\n\n"
        f"INSERT INTO shared_workflow (\"workflowId\", \"projectId\", role)\n"
        f"VALUES ('{WORKFLOW_ID}', '{PROJECT_ID}', 'workflow:owner');\n"
    )
    out, err = psql(sql_insert_wf)
    if "ERROR" in err:
        print(f"  ERROR: {err}")
        sys.exit(1)
    print("  Workflow inserted.")


# ── 3. Patch the main agent — add Tool node + connections ────────────────────

print("Patching main agent workflow...")

# Fetch current nodes and connections
raw_nodes = psql_query(
    f"SELECT nodes::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';"
)
raw_conns = psql_query(
    f"SELECT connections::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';"
)

main_nodes = json.loads(raw_nodes)
main_conns = json.loads(raw_conns)

# Check if already patched
if any(n["id"] == NEW_TOOL_NODE_ID for n in main_nodes):
    print("  Main agent already patched — skipping.")
else:
    # New toolWorkflow node
    new_tool_node = {
        "id": NEW_TOOL_NODE_ID,
        "name": "Tool — RAG Search",
        "type": "@n8n/n8n-nodes-langchain.toolWorkflow",
        "typeVersion": 1.2,
        "position": [2736, 1392],
        "parameters": {
            "name": "search_project_files",
            "description": (
                "Semantically search the indexed project codebase. "
                "Use when you need to find relevant code, understand how something is implemented, "
                "locate a function, or explore project structure. "
                "Pass 'project' to narrow results to a specific project (e.g. 'telegram-agent'). "
                "Returns matching file chunks with filepath, line numbers, and relevance score."
            ),
            "fields": {
                "values": [
                    {
                        "name": "query",
                        "stringValue": "={{ $fromAI('query', 'Natural language description of what to find in the codebase') }}",
                    },
                    {
                        "name": "project",
                        "stringValue": "={{ $fromAI('project', 'Optional: project name to filter results, e.g. telegram-agent. Leave empty to search all projects.') }}",
                    },
                    {
                        "name": "limit",
                        "stringValue": "={{ $fromAI('limit', 'Number of results to return (default: 5, max: 10)') }}",
                    },
                ]
            },
            "workflowId": {
                "__rl": True,
                "mode": "id",
                "value": WORKFLOW_ID,
            },
        },
    }

    main_nodes.append(new_tool_node)

    # Add ai_tool connections to Coding Agent and General Agent
    tool_name = "Tool — RAG Search"
    for agent_name in [CODING_AGENT_NAME, GENERAL_AGENT_NAME]:
        if tool_name not in main_conns:
            main_conns[tool_name] = {"ai_tool": []}
        if "ai_tool" not in main_conns[tool_name]:
            main_conns[tool_name]["ai_tool"] = []
        # Check if connection already exists
        existing_targets = [
            e["node"]
            for slot in main_conns[tool_name]["ai_tool"]
            for e in slot
        ]
        if agent_name not in existing_targets:
            main_conns[tool_name]["ai_tool"].append(
                [{"node": agent_name, "type": "ai_tool", "index": 0}]
            )

    new_nodes_json = json.dumps(main_nodes)
    new_conns_json = json.dumps(main_conns)

    sql_patch = (
        f"UPDATE workflow_entity\n"
        f"SET nodes = $MNODES${new_nodes_json}$MNODES$,\n"
        f"    connections = $MCONNS${new_conns_json}$MCONNS$,\n"
        f"    \"updatedAt\" = NOW()\n"
        f"WHERE id = '{MAIN_AGENT_ID}';\n"
    )
    out, err = psql(sql_patch)
    if "ERROR" in err:
        print(f"  ERROR: {err}")
        sys.exit(1)
    print(f"  Main agent patched: Tool — RAG Search added and wired to {CODING_AGENT_NAME} + {GENERAL_AGENT_NAME}.")


print("\nDone. Restart n8n to activate qwen_rag_search:")
print("  docker restart telegram-agent-n8n-1")
