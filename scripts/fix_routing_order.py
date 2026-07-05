#!/usr/bin/env python3
"""
Fix: LLM routing must happen BEFORE Is Project Set? check.

Current flow (broken):
  Load Project State → Is Project Set?
    [TRUE]  → Embed Query → Claude HTTP (Claude Code relay)
    [FALSE] → Build Route Request → LLM Router → Route by Intent → agents

Problem: if currentProject is set, ALL messages go to Claude HTTP, including email.

New flow:
  Load Project State → Build Route Request → LLM Router → Route by Intent
    [email]     → Email Agent
    [research]  → Research Agent
    [knowledge] → Is Project Set? → [TRUE]  → Embed Query → Claude HTTP
                                  → [FALSE] → Route by Agent → Knowledge Agent
    [code]      → Is Project Set? → [TRUE]  → Embed Query → Claude HTTP
                                  → [FALSE] → Route by Agent → Code Agent
    [drafting]  → Drafting Agent
    [general]   → General Agent

Changes:
  1. Load Project State[0]  → Build Route Request (was: Is Project Set?)
  2. Route by Intent[2]     → Is Project Set?     (was: Knowledge Agent)
  3. Route by Intent[3]     → Is Project Set?     (was: Code Agent)
  4. Is Project Set?[1]     → Route by Agent      (was: Build Route Request)
  5. Add new "Route by Agent" Switch node
  6. Route by Agent[0] knowledge → Knowledge Agent
  7. Route by Agent fallback → Code Agent
"""
import json, subprocess, sys

MAIN_AGENT_ID = "13329864-5514-4f83-acf9-a4610cd1e903"
NEW_NODE_ID   = "routeagt-0001-4000-0000-000000000099"


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


# ── Load current state ────────────────────────────────────────────────────────

raw_nodes = psql_query(f"SELECT nodes::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';")
raw_conns = psql_query(f"SELECT connections::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';")

nodes = json.loads(raw_nodes)
conns = json.loads(raw_conns)

# ── Guard: already patched? ───────────────────────────────────────────────────

if any(n["id"] == NEW_NODE_ID for n in nodes):
    print("Already patched — Route by Agent node exists. Exiting.")
    sys.exit(0)

# ── 1. Add "Route by Agent" Switch node ──────────────────────────────────────

# Position it between Is Project Set? and the agent nodes
route_by_agent_node = {
    "id": NEW_NODE_ID,
    "name": "Route by Agent",
    "type": "n8n-nodes-base.switch",
    "typeVersion": 3.2,
    "position": [2400, 900],
    "parameters": {
        "mode": "rules",
        "rules": {
            "values": [
                {
                    "outputKey": "knowledge",
                    "conditions": {
                        "options": {
                            "version": 2,
                            "leftValue": "",
                            "caseSensitive": False,
                            "typeValidation": "loose"
                        },
                        "combinator": "and",
                        "conditions": [{
                            "id": "cond-agt-knowledge",
                            "operator": {"type": "string", "operation": "equals"},
                            "leftValue": "={{ $json.intent }}",
                            "rightValue": "knowledge"
                        }]
                    },
                    "renameOutput": True
                }
            ]
        },
        "fallbackOutput": "extra",
        "options": {}
    }
}

nodes.append(route_by_agent_node)
print("Added node: Route by Agent")

# ── 2. Patch connections ──────────────────────────────────────────────────────

def set_output(conns, src, slot_idx, target_node):
    """Set a specific output slot of src to target_node (replacing whatever was there)."""
    if src not in conns:
        conns[src] = {"main": []}
    main = conns[src].get("main", [])
    # Extend slots list if needed
    while len(main) <= slot_idx:
        main.append([])
    main[slot_idx] = [{"node": target_node, "type": "main", "index": 0}]
    conns[src]["main"] = main


def add_output(conns, src, slot_idx, target_node):
    """Add target_node to a specific output slot without removing existing edges."""
    if src not in conns:
        conns[src] = {"main": []}
    main = conns[src].get("main", [])
    while len(main) <= slot_idx:
        main.append([])
    # Only add if not already there
    existing = [e["node"] for e in main[slot_idx]]
    if target_node not in existing:
        main[slot_idx].append({"node": target_node, "type": "main", "index": 0})
    conns[src]["main"] = main


# Change 1: Load Project State[0] → Build Route Request (was: Is Project Set?)
set_output(conns, "Load Project State", 0, "Build Route Request")
print("Load Project State[0] → Build Route Request")

# Change 2: Route by Intent[2] → Is Project Set? (was: Knowledge Agent)
set_output(conns, "Route by Intent", 2, "Is Project Set?")
print("Route by Intent[2] → Is Project Set?")

# Change 3: Route by Intent[3] → Is Project Set? (was: Code Agent)
# NOTE: n8n allows multiple sources to connect to the same target node.
# Both knowledge[2] and code[3] slots now point to Is Project Set?.
# Is Project Set? merges them (using whichever triggered).
add_output(conns, "Route by Intent", 2, "Is Project Set?")  # already set above — no-op via add
set_output(conns, "Route by Intent", 3, "Is Project Set?")
print("Route by Intent[3] → Is Project Set?")

# Change 4: Is Project Set?[1] (FALSE) → Route by Agent (was: Build Route Request)
set_output(conns, "Is Project Set?", 1, "Route by Agent")
print("Is Project Set?[1] → Route by Agent")

# Change 5: Route by Agent[0] knowledge → Knowledge Agent
set_output(conns, "Route by Agent", 0, "Knowledge Agent")
print("Route by Agent[0] → Knowledge Agent")

# Change 6: Route by Agent fallback (extra output = index 1) → Code Agent
# In n8n switch with fallbackOutput="extra", the extra output is at rules.length index
# Since we have 1 rule (knowledge), fallback is slot index 1
set_output(conns, "Route by Agent", 1, "Code Agent")
print("Route by Agent[1] (fallback) → Code Agent")

# ── 3. Write back to postgres ─────────────────────────────────────────────────

new_nodes_json = json.dumps(nodes)
new_conns_json = json.dumps(conns)

sql = (
    f"UPDATE workflow_entity\n"
    f"SET nodes = $MNODES${new_nodes_json}$MNODES$,\n"
    f"    connections = $MCONNS${new_conns_json}$MCONNS$,\n"
    f"    \"updatedAt\" = NOW()\n"
    f"WHERE id = '{MAIN_AGENT_ID}';\n"
)

out, err = psql(sql)
if "ERROR" in err:
    print(f"ERROR writing to postgres: {err}")
    sys.exit(1)
print("Postgres updated.")

# ── 4. Verify ─────────────────────────────────────────────────────────────────

print("\nVerifying...")
raw_check = psql_query(f"SELECT connections::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';")
check = json.loads(raw_check)

checks = [
    ("Load Project State", 0, "Build Route Request"),
    ("Route by Intent", 2, "Is Project Set?"),
    ("Route by Intent", 3, "Is Project Set?"),
    ("Is Project Set?", 1, "Route by Agent"),
    ("Route by Agent", 0, "Knowledge Agent"),
    ("Route by Agent", 1, "Code Agent"),
]

all_ok = True
for src, slot, expected in checks:
    slots = check.get(src, {}).get("main", [])
    targets = [e["node"] for e in (slots[slot] if slot < len(slots) else [])]
    ok = expected in targets
    print(f"  {'OK' if ok else 'FAIL'} {src}[{slot}] → {expected} (got: {targets})")
    if not ok:
        all_ok = False

if all_ok:
    print("\nAll checks passed. Restart n8n:")
    print("  docker restart telegram-agent-n8n-1")
else:
    print("\nSome checks failed — review above.")
    sys.exit(1)
