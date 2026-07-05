#!/usr/bin/env python3
"""
Fix Send Typing node: replace httpRequest (which can't access $env) with
n8n-nodes-base.telegram using the existing Telegram credential.
Also syncs workflow_history to match workflow_entity.
"""
import json, subprocess, sys

MAIN_AGENT_ID = "13329864-5514-4f83-acf9-a4610cd1e903"
TELEGRAM_CRED = {"id": "Z8q2xwxyJY7RguGl", "name": "Telegram account"}
TYPING_NODE_ID = "typing01-0001-4000-0000-000000000099"


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

# Replace the Send Typing node definition in-place
replaced = False
for i, n in enumerate(nodes):
    if n["id"] == TYPING_NODE_ID:
        nodes[i] = {
            "id": TYPING_NODE_ID,
            "name": "Send Typing",
            "type": "n8n-nodes-base.telegram",
            "typeVersion": 1.2,
            "position": n.get("position", [1200, 500]),
            "parameters": {
                "operation": "sendChatAction",
                "chatId": "={{ $json.chat_id }}",
                "action": "typing",
            },
            "credentials": {
                "telegramApi": TELEGRAM_CRED
            }
        }
        replaced = True
        print("Replaced Send Typing: httpRequest → telegram.sendChatAction")
        break

if not replaced:
    print("ERROR: Send Typing node not found by ID")
    sys.exit(1)

new_nodes_json = json.dumps(nodes)
new_conns_json = json.dumps(conns)

# Get activeVersionId
version_id = psql_query(
    f"SELECT \"activeVersionId\" FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';"
)
print(f"Syncing history version: {version_id}")

sql = f"""
UPDATE workflow_entity
SET nodes = $MNODES${new_nodes_json}$MNODES$,
    connections = $MCONNS${new_conns_json}$MCONNS$,
    "updatedAt" = NOW()
WHERE id = '{MAIN_AGENT_ID}';

UPDATE workflow_history
SET nodes       = $HNODES${new_nodes_json}$HNODES$,
    connections = $HCONNS${new_conns_json}$HCONNS$
WHERE "versionId" = '{version_id}';
"""

out, err = psql(sql)
if "ERROR" in err:
    print(f"ERROR: {err}")
    sys.exit(1)
print("Both workflow_entity and workflow_history updated.")

# Verify
check = psql_query(
    f"SELECT nodes::text FROM workflow_history WHERE \"versionId\" = '{version_id}';"
)
history_nodes = json.loads(check)
typing = next((n for n in history_nodes if n["id"] == TYPING_NODE_ID), None)
if typing:
    print(f"Verified: Send Typing type in history = {typing['type']}, operation = {typing['parameters'].get('operation')}")
else:
    print("ERROR: Send Typing not found in history")
    sys.exit(1)
