#!/usr/bin/env python3
"""
Add /clearproject command to the Telegram agent.
Clears /memory/current_project.json and confirms via Send Command Reply.
"""
import json, subprocess, sys

MAIN_AGENT_ID  = "13329864-5514-4f83-acf9-a4610cd1e903"
NEW_NODE_ID    = "clearpro-0001-4000-0000-000000000099"


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
    print("Already patched — Handle /clearproject exists. Exiting.")
    sys.exit(0)

# ── 1. New handler node ───────────────────────────────────────────────────────

handle_node = {
    "id": NEW_NODE_ID,
    "name": "Handle /clearproject",
    "type": "n8n-nodes-base.code",
    "typeVersion": 2,
    "position": [2100, 1200],
    "parameters": {
        "jsCode": (
            "const fs = require('fs');\n"
            "fs.writeFileSync('/memory/current_project.json', JSON.stringify({ currentProject: null }));\n"
            "return [{ json: { reply: '\\u2705 Project cleared. No active project.', chat_id: $input.first().json.chat_id } }];"
        )
    }
}

nodes.append(handle_node)
print("Added node: Handle /clearproject")

# ── 2. Add /clearproject rule to Route Command switch ─────────────────────────

route_cmd = next(n for n in nodes if n["name"] == "Route Command")
rules = route_cmd["parameters"]["rules"]["values"]

# Insert before Unknown Command (which is the fallback)
rules.append({
    "outputKey": "clearproject",
    "conditions": {
        "options": {
            "version": 2,
            "leftValue": "",
            "caseSensitive": False,
            "typeValidation": "loose"
        },
        "combinator": "and",
        "conditions": [{
            "id": "cond-clearproject",
            "operator": {"type": "string", "operation": "equals"},
            "leftValue": "={{ $json.message_text.split(' ')[0].toLowerCase() }}",
            "rightValue": "/clearproject"
        }]
    },
    "renameOutput": True
})

print("Added /clearproject rule to Route Command")

# ── 3. Wire Route Command new output → Handle /clearproject ──────────────────
# Current Route Command outputs: [0]listprojects [1]project [2]setproject [3]Unknown Command
# Unknown Command was the fallback (extra output), now becomes index 4 after we add clearproject at 3.
# Wait — the fallback is "extra", not a numbered slot. The rules produce slots 0,1,2,3 (now 0,1,2,3,4).
# Slot 3 = clearproject (new rule), Unknown Command stays on fallback slot 4.

# Check current Route Command connections
rc_main = conns.get("Route Command", {}).get("main", [])
# Slots: [0]→listprojects, [1]→project, [2]→setproject, [3]→Unknown Command (was fallback slot)
# After adding clearproject rule, new slots: [0],[1],[2],[3 clearproject],[4 Unknown Command]
# We need to slide Unknown Command from slot 3 to slot 4

while len(rc_main) <= 4:
    rc_main.append([])

# Move Unknown Command from slot 3 to slot 4
if rc_main[3] and any(e["node"] == "Unknown Command" for e in rc_main[3]):
    rc_main[4] = rc_main[3]
    rc_main[3] = [{"node": "Handle /clearproject", "type": "main", "index": 0}]
else:
    rc_main[3] = [{"node": "Handle /clearproject", "type": "main", "index": 0}]

conns["Route Command"] = {"main": rc_main}
print("Route Command[3] → Handle /clearproject")
print("Route Command[4] → Unknown Command (shifted)")

# ── 4. Wire Handle /clearproject → Send Command Reply ────────────────────────

conns["Handle /clearproject"] = {
    "main": [[{"node": "Send Command Reply", "type": "main", "index": 0}]]
}
print("Handle /clearproject[0] → Send Command Reply")

# ── 5. Update Unknown Command help text ──────────────────────────────────────

unknown_node = next(n for n in nodes if n["name"] == "Unknown Command")
unknown_node["parameters"]["jsCode"] = (
    "const cmd = ($input.first().json.message_text || '').split(' ')[0];\n"
    "const reply = '\\u2753 Unknown command: <code>' + cmd + '</code>\\n\\n"
    "Available commands:\\n"
    "/setproject &lt;name&gt; \\u2014 set active project\\n"
    "/clearproject \\u2014 clear active project\\n"
    "/project \\u2014 show current project\\n"
    "/listprojects \\u2014 list all projects';\n"
    "return [{ json: { reply, chat_id: $input.first().json.chat_id } }];"
)
print("Updated Unknown Command help text")

# ── 6. Write back ─────────────────────────────────────────────────────────────

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

# ── 7. Verify ─────────────────────────────────────────────────────────────────

raw_check = psql_query(f"SELECT connections::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';")
check = json.loads(raw_check)
rc = check.get("Route Command", {}).get("main", [])
print("\nRoute Command outputs:")
for i, slot in enumerate(rc):
    for e in slot:
        print(f"  [{i}] → {e['node']}")

hcp = check.get("Handle /clearproject", {}).get("main", [])
print("Handle /clearproject outputs:")
for i, slot in enumerate(hcp):
    for e in slot:
        print(f"  [{i}] → {e['node']}")

print("\nDone. Restart n8n:")
print("  docker restart telegram-agent-n8n-1")
