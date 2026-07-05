#!/usr/bin/env python3
"""
Patch Write Agent Verbatim (Node B):
- chat_id not in Merge Responses output; pull from $('Extract Message') reference
- Buffer.byteLength for correct Content-Length
- Surface errors in _memory_errors field
"""
import json, subprocess, sys

MAIN_AGENT_ID = "13329864-5514-4f83-acf9-a4610cd1e903"
NODE_B_ID     = "writevbm-0001-4000-0000-000000000002"
EMBED_MODEL   = "qwen3-embedding:4b"

NEW_CODE = r"""
function _post(hostname, port, path, body) {
  return new Promise(function(resolve, reject) {
    var http = require('http');
    var payload = JSON.stringify(body);
    var byteLen = Buffer.byteLength(payload, 'utf8');
    var req = http.request(
      { hostname: hostname, port: port, path: path, method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': byteLen } },
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

function _put(hostname, port, path, body) {
  return new Promise(function(resolve, reject) {
    var http = require('http');
    var payload = JSON.stringify(body);
    var byteLen = Buffer.byteLength(payload, 'utf8');
    var req = http.request(
      { hostname: hostname, port: port, path: path, method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'Content-Length': byteLen } },
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
var reply = item.reply || item.output || '';
var errors = [];

// chat_id is not in Merge Responses output — pull from Extract Message node
var sessionId = '';
try {
  sessionId = String($('Extract Message').item.json.chat_id || '');
} catch(e) {
  errors.push('chat_id_ref_failed: ' + e.message);
}

// Also grab project/agent context from Load Memory Context output
var projectHint = '';
var agentName = item.intent || '';
try {
  var memCtx = $('Load Memory Context').item.json;
  projectHint = memCtx.currentProject || '';
  if (!agentName) agentName = memCtx.intent || '';
} catch(e) {
  // non-fatal
}

if (!reply || !sessionId) {
  errors.push('early_exit: reply_len=' + reply.length + ' sessionId=' + JSON.stringify(sessionId));
  return [{ json: { ...item, _memory_errors: errors } }];
}

try {
  var embedData = await _post('host.docker.internal', 11434, '/api/embeddings', {
    model: '""" + EMBED_MODEL + r"""', prompt: reply
  });
  var vector = (embedData && embedData.embedding) ? embedData.embedding : [];
  if (vector.length === 0) {
    errors.push('embed_failed: keys=' + Object.keys(embedData || {}).join(','));
  } else {
    var now = new Date();
    var upsertResp = await _put('qdrant', 6333, '/collections/verbatim/points', {
      points: [{
        id: require('crypto').randomUUID(),
        vector: vector,
        payload: {
          role: 'assistant',
          content: reply,
          session_id: sessionId,
          agent: agentName,
          project_hint: projectHint,
          timestamp: now.toISOString(),
          timestamp_unix: now.getTime() / 1000,
          extracted: false,
          extraction_batch_id: null
        }
      }]
    });
    var status = (upsertResp && upsertResp.result && upsertResp.result.status) || 'no_status';
    if (status !== 'ok') {
      errors.push('upsert: ' + JSON.stringify(upsertResp).slice(0, 100));
    }
  }
} catch(e) {
  errors.push('exception: ' + e.message);
}

return [{ json: { ...item, _memory_errors: errors } }];
"""


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

patched = False
for n in nodes:
    if n["id"] == NODE_B_ID:
        n["parameters"]["jsCode"] = NEW_CODE
        patched = True
        print("Patched Write Agent Verbatim.")
        break

if not patched:
    print("ERROR: Node B not found"); sys.exit(1)

version_id = psql_query(
    f"SELECT \"activeVersionId\" FROM workflow_entity WHERE id = '{MAIN_AGENT_ID}';"
)
nodes_json = json.dumps(nodes)

out, err = psql(f"""
UPDATE workflow_entity SET nodes=$NN${nodes_json}$NN$,"updatedAt"=NOW()
WHERE id='{MAIN_AGENT_ID}';
UPDATE workflow_history SET nodes=$HN${nodes_json}$HN$
WHERE "versionId"='{version_id}';
""")
if "ERROR" in err:
    print("DB ERROR:", err); sys.exit(1)

print(f"Saved (version {version_id}). Restarting n8n...")
