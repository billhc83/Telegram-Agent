#!/usr/bin/env python3
"""
Hot-path memory injection patch.

Adds 3-tier memory read/write to the main n8n workflow:

  [A] Load Memory Context  — inserted between Load Project State → Build Route Request
      • Scrolls verbatim collection for recent session history (no embedding needed)
      • Scrolls claims collection for active global preferences
      • Formats context prefix, saves to _verbatim_history, _global_preferences
      • Keeps _original_message_text for the router; injects history into message_text for agents
      • Writes user message to verbatim (embed + upsert, synchronous)

  [B] Write Agent Verbatim — inserted between Merge Responses → Store Conversation
      • Embeds + writes the assistant response to verbatim

  [C] Build Route Request  — code updated to use _original_message_text for routing
      (agents keep receiving the enriched message_text with context prefix)

Idempotent: checks for node IDs before inserting.
Also syncs workflow_history to match workflow_entity.
"""

import json, subprocess, sys, uuid

MAIN_AGENT_ID  = "13329864-5514-4f83-acf9-a4610cd1e903"
NODE_A_ID      = "memctx01-0001-4000-0000-000000000001"
NODE_B_ID      = "writevbm-0001-4000-0000-000000000002"

QDRANT_BASE    = "http://qdrant:6333"
OLLAMA_BASE    = "http://host.docker.internal:11434"
EMBED_MODEL    = "qwen3-embedding:4b"


# ── Postgres helpers ───────────────────────────────────────────────────────────

def psql(sql: str) -> tuple[str, str]:
    r = subprocess.run(
        ["docker", "exec", "-i", "telegram-agent-postgres-1",
         "psql", "-U", "n8n", "-d", "n8n", "-v", "ON_ERROR_STOP=1"],
        input=sql.encode(), capture_output=True,
    )
    return r.stdout.decode(), r.stderr.decode()


def psql_query(sql: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "-i", "telegram-agent-postgres-1",
         "psql", "-U", "n8n", "-d", "n8n", "-t", "-A"],
        input=sql.encode(), capture_output=True,
    )
    err = r.stderr.decode()
    if "ERROR" in err:
        raise RuntimeError(f"psql error: {err}")
    return r.stdout.decode().strip()


def load_workflow() -> tuple[list, dict]:
    raw_nodes = psql_query(
        f"SELECT nodes::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';"
    )
    raw_conns = psql_query(
        f"SELECT connections::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';"
    )
    return json.loads(raw_nodes), json.loads(raw_conns)


def save_workflow(nodes: list, conns: dict) -> None:
    version_id = psql_query(
        f"SELECT \"activeVersionId\" FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';"
    )
    nodes_json = json.dumps(nodes)
    conns_json = json.dumps(conns)
    sql = f"""
UPDATE workflow_entity
SET nodes       = $NN${nodes_json}$NN$,
    connections = $NC${conns_json}$NC$,
    "updatedAt" = NOW()
WHERE id = '{MAIN_AGENT_ID}';

UPDATE workflow_history
SET nodes       = $HN${nodes_json}$HN$,
    connections = $HC${conns_json}$HC$
WHERE "versionId" = '{version_id}';
"""
    out, err = psql(sql)
    if "ERROR" in err:
        print(f"ERROR: {err}")
        sys.exit(1)
    print(f"Saved to workflow_entity and workflow_history (version {version_id}).")


def set_output(conns: dict, src: str, slot: int, target: str) -> None:
    if src not in conns:
        conns[src] = {"main": []}
    main = conns[src].setdefault("main", [])
    while len(main) <= slot:
        main.append([])
    main[slot] = [{"node": target, "type": "main", "index": 0}]


# ── Node definitions ───────────────────────────────────────────────────────────

def make_node_a() -> dict:
    code = f"""
const item = $input.first().json;
const sessionId = String(item.chat_id || '');
const currentMsg = item.message_text || '';

// ── 1. Load recent verbatim history ──────────────────────────────────────────
let history = '';
let verbatimPoints = [];
try {{
  const scrollResp = await fetch('{QDRANT_BASE}/collections/verbatim/points/scroll', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      filter: {{ must: [{{ key: 'session_id', match: {{ value: sessionId }} }}] }},
      limit: 15,
      order_by: {{ key: 'timestamp_unix', direction: 'desc' }},
      with_payload: true
    }})
  }});
  const scrollData = await scrollResp.json();
  verbatimPoints = (scrollData.result?.points || []).reverse();
  history = verbatimPoints
    .map(p => `[${{(p.payload.role || 'user').toUpperCase()}}]: ${{p.payload.content || ''}}`)
    .join('\\n');
}} catch(e) {{
  console.error('verbatim scroll error:', e.message);
}}

// ── 2. Load active global preferences ────────────────────────────────────────
let preferences = '';
try {{
  const prefsResp = await fetch('{QDRANT_BASE}/collections/claims/points/scroll', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      filter: {{ must: [
        {{ key: 'claim_type', match: {{ value: 'preference' }} }},
        {{ key: 'scope', match: {{ value: 'global' }} }},
        {{ key: 'status', match: {{ value: 'active' }} }}
      ]}},
      limit: 20,
      with_payload: true
    }})
  }});
  const prefsData = await prefsResp.json();
  const prefs = prefsData.result?.points || [];
  preferences = prefs.map(p => `- ${{p.payload.claim || ''}}`).join('\\n');
}} catch(e) {{
  console.error('claims scroll error:', e.message);
}}

// ── 3. Write user message to verbatim (embed + upsert) ───────────────────────
let userVerbatimId = null;
try {{
  const embedResp = await fetch('{OLLAMA_BASE}/api/embeddings', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ model: '{EMBED_MODEL}', prompt: currentMsg }})
  }});
  const embedData = await embedResp.json();
  const vector = embedData.embedding || [];
  if (vector.length > 0) {{
    const now = new Date();
    userVerbatimId = crypto.randomUUID();
    await fetch('{QDRANT_BASE}/collections/verbatim/points', {{
      method: 'PUT',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        points: [{{
          id: userVerbatimId,
          vector,
          payload: {{
            role: 'user',
            content: currentMsg,
            session_id: sessionId,
            agent: '',
            project_hint: item.currentProject || '',
            timestamp: now.toISOString(),
            timestamp_unix: now.getTime() / 1000,
            extracted: false,
            extraction_batch_id: null
          }}
        }}]
      }})
    }});
  }}
}} catch(e) {{
  console.error('verbatim write error:', e.message);
}}

// ── 4. Enrich message_text for agents ────────────────────────────────────────
let agentMessage = currentMsg;
if (history) {{
  agentMessage = `[Recent conversation]\\n${{history}}\\n\\n[Current message]\\n${{currentMsg}}`;
}}
if (preferences) {{
  agentMessage = `[User preferences]\\n${{preferences}}\\n\\n${{agentMessage}}`;
}}

return [{{
  json: {{
    ...item,
    _original_message_text: currentMsg,
    _verbatim_history: history,
    _global_preferences: preferences,
    _user_verbatim_id: userVerbatimId,
    _verbatim_count: verbatimPoints.length,
    message_text: agentMessage
  }}
}}];
"""
    return {
        "id": NODE_A_ID,
        "name": "Load Memory Context",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [2050, 700],
        "parameters": {"jsCode": code, "mode": "runOnceForAllItems"},
    }


def make_node_b() -> dict:
    code = f"""
const item = $input.first().json;
const reply = item.reply || item.output || '';
const sessionId = String(item.chat_id || '');

if (!reply || !sessionId) {{
  return [$input.first()];
}}

try {{
  const embedResp = await fetch('{OLLAMA_BASE}/api/embeddings', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ model: '{EMBED_MODEL}', prompt: reply }})
  }});
  const embedData = await embedResp.json();
  const vector = embedData.embedding || [];
  if (vector.length > 0) {{
    const now = new Date();
    await fetch('{QDRANT_BASE}/collections/verbatim/points', {{
      method: 'PUT',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        points: [{{
          id: crypto.randomUUID(),
          vector,
          payload: {{
            role: 'assistant',
            content: reply,
            session_id: sessionId,
            agent: item.intent || '',
            project_hint: item.currentProject || '',
            timestamp: now.toISOString(),
            timestamp_unix: now.getTime() / 1000,
            extracted: false,
            extraction_batch_id: null
          }}
        }}]
      }})
    }});
  }}
}} catch(e) {{
  console.error('agent verbatim write error:', e.message);
}}

return [$input.first()];
"""
    return {
        "id": NODE_B_ID,
        "name": "Write Agent Verbatim",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [3800, 700],
        "parameters": {"jsCode": code, "mode": "runOnceForAllItems"},
    }


def patch_build_route_request(nodes: list) -> bool:
    """Update Build Route Request to use _original_message_text for routing."""
    for n in nodes:
        if n["name"] == "Build Route Request":
            old_code = n["parameters"].get("jsCode", "")
            if "_original_message_text" in old_code:
                print("Build Route Request already patched — skipping.")
                return False
            # Replace reference to orig.message_text in the router prompt
            # The current code uses: content: orig.message_text
            new_code = old_code.replace(
                "const orig = $input.first().json;",
                "const orig = $input.first().json;\n"
                "const routeMsg = orig._original_message_text || orig.message_text;"
            ).replace(
                "content: orig.message_text",
                "content: routeMsg"
            )
            if new_code == old_code:
                print("WARNING: could not find message_text reference in Build Route Request code")
                return False
            n["parameters"]["jsCode"] = new_code
            print("Patched: Build Route Request uses _original_message_text for routing.")
            return True
    print("ERROR: Build Route Request node not found")
    return False


# ── Main ───────────────────────────────────────────────────────────────────────

nodes, conns = load_workflow()

already_patched = any(n["id"] in (NODE_A_ID, NODE_B_ID) for n in nodes)
if already_patched:
    print("Already patched — memory nodes exist. Exiting.")
    sys.exit(0)

# ── Add nodes ──────────────────────────────────────────────────────────────────
node_a = make_node_a()
node_b = make_node_b()
nodes.append(node_a)
nodes.append(node_b)
print("Added: Load Memory Context (A)")
print("Added: Write Agent Verbatim (B)")

# ── Patch Build Route Request ──────────────────────────────────────────────────
patch_build_route_request(nodes)

# ── Rewire connections ─────────────────────────────────────────────────────────

# A: Load Project State[0] → Load Memory Context (was: Build Route Request)
set_output(conns, "Load Project State", 0, "Load Memory Context")
print("Load Project State[0] → Load Memory Context")

# A: Load Memory Context[0] → Build Route Request
set_output(conns, "Load Memory Context", 0, "Build Route Request")
print("Load Memory Context[0] → Build Route Request")

# B: Merge Responses[0] had two edges (Store Conversation, Send Reply)
# Insert Write Agent Verbatim between Merge Responses and Store Conversation only
# Keep the direct Merge Responses → Send Reply edge for low-latency reply
# Wire: Merge Responses[0] → Write Agent Verbatim (replace Store Conversation edge)
#       Write Agent Verbatim[0] → Store Conversation
# Keep: Merge Responses direct → Send Reply

# Check current Merge Responses[0] targets
merge_main = conns.get("Merge Responses", {}).get("main", [[]])
slot0 = merge_main[0] if merge_main else []

# Keep Send Reply direct; replace Store Conversation with Write Agent Verbatim
new_slot0 = []
for edge in slot0:
    if edge["node"] == "Store Conversation":
        new_slot0.append({"node": "Write Agent Verbatim", "type": "main", "index": 0})
    else:
        new_slot0.append(edge)

# If Store Conversation wasn't in slot0, add Write Agent Verbatim anyway
if not any(e["node"] == "Write Agent Verbatim" for e in new_slot0):
    new_slot0.append({"node": "Write Agent Verbatim", "type": "main", "index": 0})

conns.setdefault("Merge Responses", {})["main"] = [new_slot0]
print("Merge Responses[0] → Write Agent Verbatim (+ Send Reply kept)")

set_output(conns, "Write Agent Verbatim", 0, "Store Conversation")
print("Write Agent Verbatim[0] → Store Conversation")

# ── Save ───────────────────────────────────────────────────────────────────────
save_workflow(nodes, conns)

# ── Verify ─────────────────────────────────────────────────────────────────────
_, conns_check_raw = load_workflow()
checks = [
    ("Load Project State", 0, "Load Memory Context"),
    ("Load Memory Context", 0, "Build Route Request"),
    ("Write Agent Verbatim", 0, "Store Conversation"),
]
print("\nVerifying connections...")
all_ok = True
for src, slot, expected in checks:
    slots = conns_check_raw.get(src, {}).get("main", [])
    targets = [e["node"] for e in (slots[slot] if slot < len(slots) else [])]
    ok = expected in targets
    print(f"  {'OK' if ok else 'FAIL'} {src}[{slot}] → {expected} (got: {targets})")
    if not ok:
        all_ok = False

if all_ok:
    print("\nAll checks passed. Restart n8n to apply:")
    print("  docker restart telegram-agent-n8n-1")
else:
    print("\nSome checks failed.")
    sys.exit(1)
