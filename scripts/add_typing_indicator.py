#!/usr/bin/env python3
"""
Add a Telegram 'typing...' indicator immediately when a non-command message arrives.
Inserts a sendChatAction node between Is Command?[1] and Load Project State.

Flow before: Is Command?[1] → Load Project State
Flow after:  Is Command?[1] → Send Typing → Load Project State
"""
import json, subprocess, sys

MAIN_AGENT_ID = "13329864-5514-4f83-acf9-a4610cd1e903"
NEW_NODE_ID   = "typing01-0001-4000-0000-000000000099"


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
        raise RuntimeError(err)
    return r.stdout.decode().strip()


raw_nodes = psql_query(f"SELECT nodes::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';")
raw_conns = psql_query(f"SELECT connections::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';")
nodes = json.loads(raw_nodes)
conns = json.loads(raw_conns)

if any(n["id"] == NEW_NODE_ID for n in nodes):
    print("Already patched — Send Typing node exists. Exiting.")
    sys.exit(0)

# ── 1. Add "Send Typing" node ─────────────────────────────────────────────────

typing_node = {
    "id": NEW_NODE_ID,
    "name": "Send Typing",
    "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2,
    "position": [1200, 500],
    "parameters": {
        "method": "POST",
        "url": "=https://api.telegram.org/bot{{ $env.TELEGRAM_BOT_TOKEN }}/sendChatAction",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": '={{ JSON.stringify({ chat_id: $json.chat_id, action: "typing" }) }}',
        "options": {"timeout": 5000, "response": {"response": {"neverError": True}}}
    }
}

nodes.append(typing_node)
print("Added node: Send Typing")

# ── 2. Rewire: Is Command?[1] → Send Typing → Load Project State ─────────────

# Is Command?[1] currently → Load Project State; change to → Send Typing
is_cmd_main = conns.get("Is Command?", {}).get("main", [])
while len(is_cmd_main) <= 1:
    is_cmd_main.append([])
is_cmd_main[1] = [{"node": "Send Typing", "type": "main", "index": 0}]
conns["Is Command?"] = {"main": is_cmd_main}
print("Is Command?[1] → Send Typing")

# Send Typing[0] → Load Project State
conns["Send Typing"] = {
    "main": [[{"node": "Load Project State", "type": "main", "index": 0}]]
}
print("Send Typing[0] → Load Project State")

# ── 3. Write back ─────────────────────────────────────────────────────────────

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
    print(f"ERROR: {err}")
    sys.exit(1)
print("Postgres updated.")

# ── 4. Verify ─────────────────────────────────────────────────────────────────

raw_check = psql_query(f"SELECT connections::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';")
check = json.loads(raw_check)

checks = [
    ("Is Command?", 1, "Send Typing"),
    ("Send Typing", 0, "Load Project State"),
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
    print("\nSome checks failed.")
    sys.exit(1)
