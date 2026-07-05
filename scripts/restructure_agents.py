#!/usr/bin/env python3
"""
Restructure main agent workflow:
1. Fix Is Project Set? FALSE branch → connect to new LLM router (currently both branches go to relay)
2. Add LLM routing nodes (Build Route Request → Call Router LLM → Extract Intent)
3. Rename Coding Agent → Knowledge Agent with recall-focused system prompt
4. Swap OpenRouter — Coding/Drafting/General → Ollama qwen3-14b-nothink
5. Add Code Agent (new node + Ollama LLM + Memory + Embeddings)
6. Update Route by Intent: string equality on $json.intent, add knowledge + code routes
7. Update all connection references for renamed nodes
"""

import json, subprocess, sys

MAIN_AGENT_ID  = "13329864-5514-4f83-acf9-a4610cd1e903"
OLLAMA_CRED_ID = "Qs8Dh8bkuU19W10U"
QDRANT_CRED_ID = "aWAok1h2HHdNtmht"

# ── Existing node IDs ─────────────────────────────────────────────────────────
SWITCH_ID            = "c3d4e5f6-a7b8-9012-cdef-123456789012"
CODING_AGENT_ID      = "f2a3b4c5-d6e7-89ab-f012-012345678901"
OPENROUTER_CODING_ID = "a3b4c5d6-e7f8-9abc-0123-123456789012"
OPENROUTER_DRAFT_ID  = "e7f8a9b0-c1d2-def0-4567-567890123456"
OPENROUTER_GEN_ID    = "c1d2e3f4-a5b6-1234-89ab-901234567890"

# ── New node IDs ──────────────────────────────────────────────────────────────
BUILD_ROUTE_ID  = "router01-0001-4000-0000-000000000099"
CALL_ROUTER_ID  = "router01-0002-4000-0000-000000000099"
EXTRACT_INT_ID  = "router01-0003-4000-0000-000000000099"
CODE_AGENT_ID   = "codeagt1-0001-4000-0000-000000000099"
OLLAMA_CODE_ID  = "codeagt1-0002-4000-0000-000000000099"
MEMORY_CODE_ID  = "codeagt1-0003-4000-0000-000000000099"
EMBED_CODE_ID   = "codeagt1-0004-4000-0000-000000000099"


# ── DB helpers ────────────────────────────────────────────────────────────────

def psql(sql):
    r = subprocess.run(
        ["docker", "exec", "-i", "telegram-agent-postgres-1",
         "psql", "-U", "n8n", "-d", "n8n", "-v", "ON_ERROR_STOP=1"],
        input=sql.encode(), capture_output=True,
    )
    return r.stdout.decode(), r.stderr.decode()


def psql_query(sql):
    r = subprocess.run(
        ["docker", "exec", "-i", "telegram-agent-postgres-1",
         "psql", "-U", "n8n", "-d", "n8n", "-t", "-A"],
        input=sql.encode(), capture_output=True,
    )
    err = r.stderr.decode()
    if "ERROR" in err:
        raise RuntimeError(f"psql error: {err}")
    return r.stdout.decode().strip()


def rename_in_connections(conns, old_name, new_name):
    """Rename all references to old_name → new_name throughout the connections dict."""
    result = {}
    for src, targets in conns.items():
        new_src = new_name if src == old_name else src
        new_targets = {}
        for conn_type, slots in targets.items():
            new_slots = []
            for slot in slots:
                new_slot = [
                    {**e, "node": new_name} if e.get("node") == old_name else e
                    for e in slot
                ]
                new_slots.append(new_slot)
            new_targets[conn_type] = new_slots
        result[new_src] = new_targets
    return result


# ── 1. Read current workflow ──────────────────────────────────────────────────

print("Reading current workflow...")
raw_nodes = psql_query(f"SELECT nodes::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';")
raw_conns = psql_query(f"SELECT connections::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';")

nodes = json.loads(raw_nodes)
conns = json.loads(raw_conns)

nodes_by_id   = {n["id"]: n for n in nodes}
nodes_by_name = {n["name"]: n for n in nodes}


# ── 2. Rename Coding Agent → Knowledge Agent ──────────────────────────────────

print("Renaming Coding Agent → Knowledge Agent...")

coding_agent = nodes_by_id[CODING_AGENT_ID]
coding_agent["name"] = "Knowledge Agent"
coding_agent["parameters"]["options"]["systemMessage"] = (
    "You are a knowledge and recall assistant. Your job is to answer questions about "
    "the user's projects, files, notes, and past work.\n\n"
    "Always use search_project_files before answering project questions — "
    "it searches the indexed codebase semantically. Use search_memory to recall past "
    "conversations. Use the filesystem tool to read specific files when needed.\n\n"
    "Format responses for Telegram: use <code>inline</code> and <pre>block</pre> for code."
)

# Rename all connection references
conns = rename_in_connections(conns, "Coding Agent", "Knowledge Agent")


# ── 3. Swap model nodes to Ollama ─────────────────────────────────────────────

print("Swapping OpenRouter nodes → Ollama for Knowledge/Drafting/General...")

ollama_node_template = {
    "type": "@n8n/n8n-nodes-langchain.lmChatOllama",
    "typeVersion": 1,
    "parameters": {
        "model": "qwen3-14b-nothink:latest",
        "options": {},
    },
    "credentials": {
        "ollamaApi": {"id": OLLAMA_CRED_ID, "name": "Ollama account"}
    },
}

swaps = [
    (OPENROUTER_CODING_ID, "OpenRouter — Coding", "Ollama — Knowledge", [1200, 1264]),
    (OPENROUTER_DRAFT_ID,  "OpenRouter — Drafting", "Ollama — Drafting", [1392, 1872]),
    (OPENROUTER_GEN_ID,    "OpenRouter — General",  "Ollama — General",  [1168, 2688]),
]

for node_id, old_name, new_name, position in swaps:
    node = nodes_by_id[node_id]
    node["name"]        = new_name
    node["type"]        = ollama_node_template["type"]
    node["typeVersion"] = ollama_node_template["typeVersion"]
    node["parameters"]  = ollama_node_template["parameters"].copy()
    node["credentials"] = ollama_node_template["credentials"].copy()
    node["position"]    = position
    conns = rename_in_connections(conns, old_name, new_name)


# ── 4. Add LLM routing nodes ──────────────────────────────────────────────────

print("Adding LLM routing nodes...")

ROUTE_SYSTEM = (
    "You are a message router. Classify the user message into ONE label.\n\n"
    "email     — reading, writing, or managing email/Gmail\n"
    "code      — explicitly asked to write, generate, debug, or fix code "
    "(e.g. 'write a script', 'here is my code', 'debug this function', 'arduino code for X')\n"
    "research  — needs external web information, news, browsing, or current events\n"
    "knowledge — questions about personal projects, files, notes, past work, memory, recall. "
    "Includes 'summarize my X project', 'what did I build', 'find in my files'\n"
    "drafting  — writing, composing, editing text documents, essays, messages\n"
    "general   — conversation, planning, questions that don't fit the above\n\n"
    "IMPORTANT: A project name containing 'code' (e.g. 'kids code') is NOT a code task. "
    "Only label 'code' when the user explicitly asks to write or debug code.\n\n"
    "Reply with ONE word only: email, code, research, knowledge, drafting, or general."
)

build_route_node = {
    "id": BUILD_ROUTE_ID,
    "name": "Build Route Request",
    "type": "n8n-nodes-base.code",
    "typeVersion": 2,
    "position": [200, 1600],
    "parameters": {
        "jsCode": (
            "const orig = $input.first().json;\n"
            "return [{ json: {\n"
            "  ...orig,\n"
            "  _route_request: {\n"
            "    model: 'qwen3-14b-nothink:latest',\n"
            "    messages: [\n"
            "      { role: 'system', content: " + json.dumps(ROUTE_SYSTEM) + " },\n"
            "      { role: 'user',   content: orig.message_text }\n"
            "    ],\n"
            "    stream: false,\n"
            "    options: { temperature: 0 }\n"
            "  }\n"
            "} }];"
        )
    },
}

call_router_node = {
    "id": CALL_ROUTER_ID,
    "name": "Call Router LLM",
    "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2,
    "position": [420, 1600],
    "parameters": {
        "method": "POST",
        "url": "http://host.docker.internal:11434/api/chat",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ $json._route_request }}",
        "options": {"timeout": 15000},
    },
}

extract_intent_node = {
    "id": EXTRACT_INT_ID,
    "name": "Extract Intent",
    "type": "n8n-nodes-base.code",
    "typeVersion": 2,
    "position": [640, 1600],
    "parameters": {
        "jsCode": (
            "const orig = $('Build Route Request').item.json;\n"
            "const raw   = ($input.first().json.message?.content || 'general').trim().toLowerCase();\n"
            "const valid = ['email','code','research','knowledge','drafting','general'];\n"
            "const intent = valid.includes(raw) ? raw : 'general';\n"
            "return [{ json: { ...orig, intent } }];"
        )
    },
}

nodes.extend([build_route_node, call_router_node, extract_intent_node])


# ── 5. Add Code Agent nodes ───────────────────────────────────────────────────

print("Adding Code Agent nodes...")

code_agent_node = {
    "id": CODE_AGENT_ID,
    "name": "Code Agent",
    "type": "@n8n/n8n-nodes-langchain.agent",
    "typeVersion": 1.7,
    "position": [1328, 1848],
    "parameters": {
        "text": "={{ $json.message_text }}",
        "promptType": "define",
        "options": {
            "systemMessage": (
                "You are a coding assistant. Write, generate, debug, and explain code.\n\n"
                "Use the shell tool to run scripts and test code. "
                "Use the filesystem tool to read and write files. "
                "Use search_memory to recall past code context.\n\n"
                "Format code for Telegram: use <code>inline</code> and <pre>block</pre>."
            )
        },
    },
}

ollama_code_node = {
    "id": OLLAMA_CODE_ID,
    "name": "Ollama — Code",
    "type": "@n8n/n8n-nodes-langchain.lmChatOllama",
    "typeVersion": 1,
    "position": [1200, 2050],
    "parameters": {"model": "qwen3-14b-nothink:latest", "options": {}},
    "credentials": {"ollamaApi": {"id": OLLAMA_CRED_ID, "name": "Ollama account"}},
}

memory_code_node = {
    "id": MEMORY_CODE_ID,
    "name": "Memory — Code",
    "type": "@n8n/n8n-nodes-langchain.vectorStoreQdrant",
    "typeVersion": 1.1,
    "position": [912, 2050],
    "parameters": {
        "mode": "retrieve-as-tool",
        "options": {},
        "toolName": "search_memory",
        "toolDescription": "Search past conversation history for relevant context.",
        "qdrantCollection": {"__rl": True, "mode": "id", "value": "agent_memory"},
    },
    "credentials": {"qdrantApi": {"id": QDRANT_CRED_ID, "name": "Qdrant account"}},
}

embed_code_node = {
    "id": EMBED_CODE_ID,
    "name": "Embeddings — Code",
    "type": "@n8n/n8n-nodes-langchain.embeddingsOllama",
    "typeVersion": 1,
    "position": [992, 2250],
    "parameters": {"model": "nomic-embed-text:latest"},
    "credentials": {"ollamaApi": {"id": OLLAMA_CRED_ID, "name": "Ollama account"}},
}

nodes.extend([code_agent_node, ollama_code_node, memory_code_node, embed_code_node])


# ── 6. Update Route by Intent Switch → string equality on $json.intent ────────

print("Updating Route by Intent switch rules...")

switch_node = nodes_by_id[SWITCH_ID]

def intent_rule(label, output_key=None):
    return {
        "outputKey": output_key or label,
        "renameOutput": True,
        "conditions": {
            "options": {
                "version": 2, "leftValue": "", "caseSensitive": False, "typeValidation": "strict"
            },
            "combinator": "or",
            "conditions": [{
                "id": f"cond-{label}",
                "operator": {"type": "string", "operation": "equals"},
                "leftValue": "={{ $json.intent }}",
                "rightValue": label,
            }],
        },
    }

switch_node["parameters"] = {
    "rules": {
        "values": [
            intent_rule("email"),       # [0] → Email Agent
            intent_rule("research"),    # [1] → Research Agent
            intent_rule("knowledge"),   # [2] → Knowledge Agent
            intent_rule("code"),        # [3] → Code Agent
            intent_rule("drafting"),    # [4] → Drafting Agent
            # general → fallback (extra output [5])
        ]
    },
    "options": {"fallbackOutput": "extra"},
}


# ── 7. Update connections ─────────────────────────────────────────────────────

print("Updating connections...")

# Fix Is Project Set? FALSE [1] → Build Route Request (instead of Embed Query)
if "Is Project Set?" in conns:
    conns["Is Project Set?"]["main"][1] = [
        {"node": "Build Route Request", "type": "main", "index": 0}
    ]

# Wire LLM routing chain
conns["Build Route Request"] = {
    "main": [[{"node": "Call Router LLM", "type": "main", "index": 0}]]
}
conns["Call Router LLM"] = {
    "main": [[{"node": "Extract Intent", "type": "main", "index": 0}]]
}
conns["Extract Intent"] = {
    "main": [[{"node": "Route by Intent", "type": "main", "index": 0}]]
}

# Update Route by Intent outputs:
# Old: [0]=Email [1]=Research [2]=CodingAgent [3]=Drafting [4]=General(extra)
# New: [0]=Email [1]=Research [2]=Knowledge   [3]=Code     [4]=Drafting [5]=General(extra)
conns["Route by Intent"] = {
    "main": [
        [{"node": "Email Agent",     "type": "main", "index": 0}],  # [0] email
        [{"node": "Research Agent",  "type": "main", "index": 0}],  # [1] research
        [{"node": "Knowledge Agent", "type": "main", "index": 0}],  # [2] knowledge
        [{"node": "Code Agent",      "type": "main", "index": 0}],  # [3] code
        [{"node": "Drafting Agent",  "type": "main", "index": 0}],  # [4] drafting
        [{"node": "General Agent",   "type": "main", "index": 0}],  # [5] general (extra)
    ]
}

# Code Agent output → Merge Responses
conns["Code Agent"] = {
    "main": [[{"node": "Merge Responses", "type": "main", "index": 0}]]
}

# Wire Code Agent LLM + memory + embeddings
conns["Ollama — Code"] = {
    "ai_languageModel": [[{"node": "Code Agent", "type": "ai_languageModel", "index": 0}]]
}
conns["Memory — Code"] = {
    "ai_tool": [[{"node": "Code Agent", "type": "ai_tool", "index": 0}]]
}
conns["Embeddings — Code"] = {
    "ai_embeddingModel": [[{"node": "Memory — Code", "type": "ai_embeddingModel", "index": 0}]]
}

# Share Shell MCP, GitHub MCP, Filesystem MCP — Coding with Code Agent
for tool_name in ["Shell MCP", "GitHub MCP", "Filesystem MCP — Coding"]:
    if tool_name in conns and "ai_tool" in conns[tool_name]:
        conns[tool_name]["ai_tool"].append(
            [{"node": "Code Agent", "type": "ai_tool", "index": 0}]
        )

# Wire code sub-workflow tools to Code Agent (move from Knowledge Agent)
code_tools = [
    "Tool — Bash Command",
    "Tool — Commit Message",
    "Tool — Changelog",
    "Tool — PR Description",
    "Tool — Regex",
    "Tool — Transform JSON",
    "Tool — Test Stubs",
]
for tool_name in code_tools:
    if tool_name in conns and "ai_tool" in conns[tool_name]:
        # Replace Knowledge Agent target with Code Agent
        new_slots = []
        for slot in conns[tool_name]["ai_tool"]:
            new_slot = [
                {**e, "node": "Code Agent"} if e.get("node") == "Knowledge Agent" else e
                for e in slot
            ]
            new_slots.append(new_slot)
        conns[tool_name]["ai_tool"] = new_slots


# ── 8. Write back to postgres ─────────────────────────────────────────────────

print("Writing updated workflow to postgres...")

nodes_json = json.dumps(nodes)
conns_json = json.dumps(conns)

sql = (
    f"UPDATE workflow_entity\n"
    f"SET nodes = $NODES${nodes_json}$NODES$,\n"
    f"    connections = $CONNS${conns_json}$CONNS$,\n"
    f'    "updatedAt" = NOW()\n'
    f"WHERE id = '{MAIN_AGENT_ID}';\n"
)

out, err = psql(sql)
if "ERROR" in err:
    print(f"ERROR writing to postgres:\n{err}")
    sys.exit(1)

print("\nDone. Summary of changes:")
print("  ✓ Coding Agent → Knowledge Agent (knowledge recall system prompt)")
print("  ✓ OpenRouter — Coding/Drafting/General → Ollama qwen3-14b-nothink")
print("  ✓ Added LLM routing (Build Route Request → Call Router LLM → Extract Intent)")
print("  ✓ Is Project Set? FALSE → new LLM router (was incorrectly → relay path)")
print("  ✓ Route by Intent updated: string equality on $json.intent")
print("  ✓ Added Code Agent (new) with Ollama LLM, Memory, Embeddings")
print("  ✓ Code tools (bash, commit, regex, etc.) moved to Code Agent")
print("  ✓ Shell MCP + GitHub MCP + Filesystem MCP shared with Code Agent")
print()
print("Restart n8n to apply:")
print("  docker restart telegram-agent-n8n-1")
