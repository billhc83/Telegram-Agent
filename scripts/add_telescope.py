#!/usr/bin/env python3
"""
Add telescope commands (/directions, /state, /decisions) to the main workflow.

Inserts between Load Memory Context and Build Route Request:

  Load Memory Context
        ↓
  Is Telescope?  (IF node — checks _original_message_text starts with /)
   true ↓           false ↓
  Telescope Handler   Build Route Request  (existing flow)
        ↓
  Send Telescope Reply  (Telegram node, bypasses agents)

Idempotent: checks for node IDs before inserting.
"""
import json, subprocess, sys

MAIN_AGENT_ID = "13329864-5514-4f83-acf9-a4610cd1e903"

IF_NODE_ID        = "telscope-0001-4000-0000-000000000001"
HANDLER_NODE_ID   = "telscope-0002-4000-0000-000000000002"
SEND_NODE_ID      = "telscope-0003-4000-0000-000000000003"

TELEGRAM_CRED = {"id": "Z8q2xwxyJY7RguGl", "name": "Telegram account"}

TELESCOPE_CMDS = ["/directions", "/state", "/decisions", "/help"]


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


HANDLER_CODE = """
function _post(hostname, port, path, body) {
  return new Promise(function(resolve, reject) {
    var http = require('http');
    var payload = JSON.stringify(body);
    var byteLen = Buffer.byteLength(payload, 'utf8');
    var req = http.request(
      { hostname: hostname, port: port, path: path, method: 'POST',
        headers: {'Content-Type':'application/json','Content-Length':byteLen} },
      function(res) {
        var data = '';
        res.on('data', function(c) { data += c; });
        res.on('end', function() {
          try { resolve(JSON.parse(data)); } catch(e) { resolve({}); }
        });
      }
    );
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

var item = $input.first().json;
var cmd = (item._original_message_text || item.message_text || '').trim().toLowerCase().split(' ')[0];

// ── /directions ───────────────────────────────────────────────────────────────
if (cmd === '/directions') {
  var resp = await _post('qdrant', 6333, '/collections/claims/points/scroll', {
    filter: {
      must: [
        { key: 'claim_type', match: { value: 'direction' } },
        { key: 'status', match: { any: ['active', 'contested'] } }
      ]
    },
    limit: 20,
    with_payload: true
  });
  var points = (resp.result && resp.result.points) ? resp.result.points : [];

  // Score: confidence * log1p(evidence_count) * exp(-0.02 * age_days)
  var now = Date.now();
  points = points.map(function(p) {
    var pay = p.payload;
    var ageDays = (now - new Date(pay.last_supported_at || pay.created_at).getTime()) / 86400000;
    var score = (pay.confidence || 0.5) * Math.log1p(pay.evidence_count || 1) * Math.exp(-0.02 * ageDays);
    return { pay: pay, score: score };
  }).sort(function(a, b) { return b.score - a.score; });

  var lines = ['<b>Active Directions</b>'];
  if (points.length === 0) {
    lines.push('No active directions yet. Directions are extracted automatically from conversations.');
  } else {
    points.forEach(function(item, i) {
      var p = item.pay;
      var status = p.status === 'contested' ? ' ⚠️' : '';
      var proj = p.project ? ' [' + p.project + ']' : '';
      lines.push((i+1) + '. ' + p.claim + proj + status);
    });
  }
  return [{ json: { ...item, _telescope_reply: lines.join('\\n') } }];
}

// ── /decisions ────────────────────────────────────────────────────────────────
if (cmd === '/decisions') {
  var resp = await _post('qdrant', 6333, '/collections/claims/points/scroll', {
    filter: {
      must: [{ key: 'claim_type', match: { value: 'decision' } }],
      must_not: [{ key: 'status', match: { value: 'superseded' } }]
    },
    limit: 20,
    with_payload: true
  });
  var points = (resp.result && resp.result.points) ? resp.result.points : [];
  points.sort(function(a, b) {
    return new Date(b.payload.created_at).getTime() - new Date(a.payload.created_at).getTime();
  });

  var lines = ['<b>Recent Decisions</b>'];
  if (points.length === 0) {
    lines.push('No decisions recorded yet.');
  } else {
    points.slice(0, 15).forEach(function(p, i) {
      var pay = p.payload;
      var date = pay.created_at ? pay.created_at.slice(0, 10) : '';
      var proj = pay.project ? ' [' + pay.project + ']' : '';
      lines.push((i+1) + '. ' + pay.claim + proj + (date ? ' (' + date + ')' : ''));
    });
  }
  return [{ json: { ...item, _telescope_reply: lines.join('\\n') } }];
}

// ── /state ────────────────────────────────────────────────────────────────────
if (cmd === '/state') {
  var [dirResp, prefResp, decResp] = await Promise.all([
    _post('qdrant', 6333, '/collections/claims/points/scroll', {
      filter: { must: [{ key: 'claim_type', match: { value: 'direction' } },
                       { key: 'status', match: { any: ['active', 'contested'] } }] },
      limit: 5, with_payload: true
    }),
    _post('qdrant', 6333, '/collections/claims/points/scroll', {
      filter: { must: [{ key: 'claim_type', match: { value: 'preference' } },
                       { key: 'scope', match: { value: 'global' } },
                       { key: 'status', match: { value: 'active' } }] },
      limit: 10, with_payload: true
    }),
    _post('qdrant', 6333, '/collections/claims/points/scroll', {
      filter: { must: [{ key: 'claim_type', match: { value: 'decision' } }],
                must_not: [{ key: 'status', match: { value: 'superseded' } }] },
      limit: 3, with_payload: true
    })
  ]);

  var dirs = (dirResp.result && dirResp.result.points) ? dirResp.result.points : [];
  var prefs = (prefResp.result && prefResp.result.points) ? prefResp.result.points : [];
  var decs = (decResp.result && decResp.result.points) ? decResp.result.points : [];

  var lines = ['<b>System State</b>'];

  lines.push('');
  lines.push('<b>Top Directions</b>');
  if (dirs.length === 0) lines.push('  none yet');
  dirs.forEach(function(p, i) {
    var pay = p.payload;
    var mark = pay.status === 'contested' ? ' ⚠️' : '';
    lines.push('  • ' + pay.claim + mark);
  });

  lines.push('');
  lines.push('<b>Global Preferences</b>');
  if (prefs.length === 0) lines.push('  none yet');
  prefs.forEach(function(p) { lines.push('  • ' + p.payload.claim); });

  lines.push('');
  lines.push('<b>Recent Decisions</b>');
  if (decs.length === 0) lines.push('  none yet');
  decs.forEach(function(p) { lines.push('  • ' + p.payload.claim); });

  return [{ json: { ...item, _telescope_reply: lines.join('\\n') } }];
}

// ── /help ─────────────────────────────────────────────────────────────────────
var helpText = [
  '<b>Telescope Commands</b>',
  '/state — system overview: top directions, preferences, decisions',
  '/directions — all active directions sorted by score',
  '/decisions — recent decisions'
].join('\\n');

return [{ json: { ...item, _telescope_reply: helpText } }];
"""


def make_if_node():
    return {
        "id": IF_NODE_ID,
        "name": "Is Telescope?",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2,
        "position": [2200, 700],
        "parameters": {
            "conditions": {
                "options": {"caseSensitive": False},
                "conditions": [{
                    "id": "cond-telescope-01",
                    "leftValue": "={{ $json._original_message_text || $json.message_text }}",
                    "rightValue": "/",
                    "operator": {
                        "type": "string",
                        "operation": "startsWith",
                    }
                }],
                "combinator": "and"
            }
        },
    }


def make_handler_node():
    return {
        "id": HANDLER_NODE_ID,
        "name": "Telescope Handler",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [2450, 580],
        "parameters": {"jsCode": HANDLER_CODE, "mode": "runOnceForAllItems"},
    }


def make_send_node():
    return {
        "id": SEND_NODE_ID,
        "name": "Send Telescope Reply",
        "type": "n8n-nodes-base.telegram",
        "typeVersion": 1.2,
        "position": [2700, 580],
        "parameters": {
            "text": "={{ $json._telescope_reply }}",
            "chatId": "={{ $('Extract Message').item.json.chat_id }}",
            "additionalFields": {"parse_mode": "HTML"},
        },
        "credentials": {"telegramApi": TELEGRAM_CRED},
    }


def set_output(conns, src, slot, targets):
    if src not in conns:
        conns[src] = {"main": []}
    main = conns[src].setdefault("main", [])
    while len(main) <= slot:
        main.append([])
    main[slot] = targets if isinstance(targets, list) else [targets]


nodes, conns = (
    json.loads(psql_query(f"SELECT nodes::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';")),
    json.loads(psql_query(f"SELECT connections::text FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';")),
)

if any(n["id"] in (IF_NODE_ID, HANDLER_NODE_ID, SEND_NODE_ID) for n in nodes):
    print("Telescope nodes already present — exiting.")
    sys.exit(0)

nodes += [make_if_node(), make_handler_node(), make_send_node()]
print("Added: Is Telescope? (IF), Telescope Handler (Code), Send Telescope Reply (Telegram)")

# Rewire:
# Load Memory Context[0] was → Build Route Request
# Now: Load Memory Context[0] → Is Telescope?
#      Is Telescope? true[0]  → Telescope Handler
#      Is Telescope? false[1] → Build Route Request
#      Telescope Handler[0]   → Send Telescope Reply
set_output(conns, "Load Memory Context", 0, [{"node": "Is Telescope?", "type": "main", "index": 0}])
set_output(conns, "Is Telescope?", 0, [{"node": "Telescope Handler", "type": "main", "index": 0}])  # true
set_output(conns, "Is Telescope?", 1, [{"node": "Build Route Request", "type": "main", "index": 0}])  # false
set_output(conns, "Telescope Handler", 0, [{"node": "Send Telescope Reply", "type": "main", "index": 0}])

print("Load Memory Context → Is Telescope?")
print("Is Telescope? [true]  → Telescope Handler → Send Telescope Reply")
print("Is Telescope? [false] → Build Route Request")

version_id = psql_query(f"SELECT \"activeVersionId\" FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';")
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
