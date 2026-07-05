#!/usr/bin/env python3
"""
Wire telescope commands into the existing Route Command switch node.

Adds a "telescope" rule (matching /state, /directions, /decisions, /help)
at slot 4, shifting the existing fallback (Unknown Command) to slot 5.

Also removes the now-redundant Is Telescope? IF node branch from the
Load Memory Context → Build Route Request path (restores direct connection).
"""
import json, subprocess, sys

MAIN_AGENT_ID     = "13329864-5514-4f83-acf9-a4610cd1e903"
ROUTE_CMD_ID      = "cc000002-0000-4000-0000-000000000002"
TELESCOPE_CMDS    = ["/state", "/directions", "/decisions", "/help"]
HANDLER_NODE_NAME = "Telescope Handler"


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


nodes = json.loads(psql_query(
    f"SELECT nodes::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';"
))
conns = json.loads(psql_query(
    f"SELECT connections::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';"
))

# ── 1. Add telescope rule to Route Command switch ─────────────────────────────
route_cmd_node = next((n for n in nodes if n["id"] == ROUTE_CMD_ID), None)
if not route_cmd_node:
    print("ERROR: Route Command node not found"); sys.exit(1)

rules = route_cmd_node["parameters"]["rules"]["values"]
if any(r.get("outputKey") == "telescope" for r in rules):
    print("Telescope rule already present in Route Command — skipping rule addition.")
else:
    telescope_rule = {
        "outputKey": "telescope",
        "renameOutput": True,
        "conditions": {
            "options": {"version": 2, "leftValue": "", "caseSensitive": False, "typeValidation": "loose"},
            "combinator": "or",
            "conditions": [
                {
                    "id": f"cond-telescope-{cmd[1:]}",
                    "operator": {"type": "string", "operation": "equals"},
                    "leftValue": "={{ $json.message_text.split(' ')[0].toLowerCase() }}",
                    "rightValue": cmd,
                }
                for cmd in TELESCOPE_CMDS
            ],
        },
    }
    rules.append(telescope_rule)
    print(f"Added telescope rule to Route Command (matches: {', '.join(TELESCOPE_CMDS)})")

# ── 2. Update Route Command connections ───────────────────────────────────────
# Current: slots 0-3 = named commands, slot 4 = Unknown Command (fallback)
# New:     slots 0-3 unchanged, slot 4 = Telescope Handler, slot 5 = Unknown Command
rc_main = conns.get("Route Command", {}).get("main", [])

if len(rc_main) >= 5:
    fallback_slot = rc_main[4]  # [{"node": "Unknown Command", ...}]
    # Check if telescope already wired
    if len(rc_main) > 5 or (rc_main[4] and rc_main[4][0].get("node") == HANDLER_NODE_NAME):
        print("Route Command telescope slot already wired.")
    else:
        # Insert telescope at slot 4, push Unknown Command to slot 5
        rc_main.insert(4, [{"node": HANDLER_NODE_NAME, "type": "main", "index": 0}])
        conns["Route Command"]["main"] = rc_main
        print(f"Route Command[4] → {HANDLER_NODE_NAME}")
        print(f"Route Command[5] → Unknown Command (shifted from slot 4)")
else:
    rc_main.append([{"node": HANDLER_NODE_NAME, "type": "main", "index": 0}])
    conns["Route Command"]["main"] = rc_main
    print(f"Route Command[{len(rc_main)-1}] → {HANDLER_NODE_NAME}")

# ── 3. Restore Load Memory Context → Build Route Request (bypass Is Telescope?) ──
# The Is Telescope? branch is now redundant (telescope commands never reach it)
# but harmless — the "/" check will never be true for normal messages.
# Leave it in place (it's already wired Load Memory Context → Is Telescope? → false → Build Route Request)
# so normal messages still flow correctly through the false branch.
# No change needed here — the false branch already goes to Build Route Request.
print("Note: Is Telescope? IF node stays in place; false branch already → Build Route Request.")

# ── 4. Save ───────────────────────────────────────────────────────────────────
version_id = psql_query(
    f"SELECT \"activeVersionId\" FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';"
)
nodes_json = json.dumps(nodes)
conns_json = json.dumps(conns)

out, err = psql(f"""
UPDATE workflow_entity
SET nodes=$NN${nodes_json}$NN$,
    connections=$NC${conns_json}$NC$,
    "updatedAt"=NOW()
WHERE id='{MAIN_AGENT_ID}';

UPDATE workflow_history
SET nodes=$HN${nodes_json}$HN$,
    connections=$HC${conns_json}$HC$
WHERE "versionId"='{version_id}';
""")
if "ERROR" in err:
    print("DB ERROR:", err); sys.exit(1)

print(f"\nSaved (version {version_id}).")
print("Restart n8n: docker restart telegram-agent-n8n-1")
