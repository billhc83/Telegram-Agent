#!/usr/bin/env python3
"""
Create a new n8n workflow: "Extract Claims (Scheduled)"

Schedule: every 30 minutes
Single Code node that:
  1. Scrolls verbatim for unextracted messages (limit 30)
  2. Checks idempotency via batch_id
  3. Calls qwen3-14b-nothink via Ollama to extract typed claims
  4. Embeds each claim and upserts to the claims Qdrant collection
  5. Marks verbatim messages as extracted
"""
import json, subprocess, sys, uuid

WORKFLOW_ID   = "ec1a1m00-0000-4000-0000-000000000001"
WORKFLOW_NAME = "Extract Claims (Scheduled)"
EMBED_MODEL   = "qwen3-embedding:4b"
EXTRACT_MODEL = "qwen3-14b-nothink:latest"


def psql(sql: str):
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
        raise RuntimeError(err)
    return r.stdout.decode().strip()


SYSTEM_PROMPT = r"""You are a memory extraction engine. You receive a sequence of conversation messages between a user and an AI assistant. Extract typed epistemic claims.

Return a JSON array. Each element:
{
  "claim_type": "decision|proposal|hypothesis|contradiction|preference|discussion|direction",
  "claim": "<one-sentence statement>",
  "stance": "supports|contradicts|neutral",
  "confidence": 0.0-1.0,
  "status": "unresolved|confirmed|rejected|active",
  "scope": "project|global|cross_project",
  "topics": ["<topic>"],
  "project": "<project name or empty>",
  "applies_if": {}
}

Claim type rules:
- decision: explicitly decided or agreed upon
- proposal: suggested but not decided
- hypothesis: a theory being tested
- contradiction: conflicts with a prior belief
- preference: persistent user preference (scope=global if applies everywhere)
- discussion: topic under active discussion, no resolution
- direction: guiding principle or intended system state

For applies_if predicates: {"project":"x"}, {"agent":"y"}, {"topic":"z"}, {"or":[...]}, {"and":[...]}, {"not":{...}}
Leave applies_if as {} if globally applicable.

Do NOT invent claims. Return ONLY the JSON array, no markdown, no explanation."""


EXTRACT_CODE = """
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

function _put(hostname, port, path, body) {
  return new Promise(function(resolve, reject) {
    var http = require('http');
    var payload = JSON.stringify(body);
    var byteLen = Buffer.byteLength(payload, 'utf8');
    var req = http.request(
      { hostname: hostname, port: port, path: path, method: 'PUT',
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

// ── 1. Scroll unextracted verbatim messages ──────────────────────────────────
var scrollResp = await _post('qdrant', 6333, '/collections/verbatim/points/scroll', {
  filter: { must: [{ key: 'extracted', match: { value: false } }] },
  limit: 30,
  order_by: { key: 'timestamp_unix', direction: 'asc' },
  with_payload: true
});
var points = (scrollResp.result && scrollResp.result.points) ? scrollResp.result.points : [];

if (points.length === 0) {
  return [{ json: { status: 'nothing_to_extract', extracted: 0, claims_written: 0 } }];
}

// ── 2. Idempotency: batch_id = SHA256(sorted IDs)[:16] ───────────────────────
var crypto = require('crypto');
var messageIds = points.map(function(p) { return String(p.id); }).sort();
var batchId = 'batch_' + crypto.createHash('sha256').update(messageIds.join(',')).digest('hex').slice(0, 16);

// Check if already processed
var dedupCheck = await _post('qdrant', 6333, '/collections/claims/points/scroll', {
  filter: { must: [{ key: 'verbatim_ids', match: { any: [batchId] } }] },
  limit: 1,
  with_payload: false
});
if (dedupCheck.result && dedupCheck.result.points && dedupCheck.result.points.length > 0) {
  // Mark as extracted anyway (cleanup)
  for (var mid of messageIds) {
    await _post('qdrant', 6333, '/collections/verbatim/points/payload', {
      payload: { extracted: true, extraction_batch_id: batchId },
      points: [mid]
    });
  }
  return [{ json: { status: 'already_processed', batch_id: batchId } }];
}

// ── 3. Format conversation for LLM ───────────────────────────────────────────
var conversation = points.map(function(p) {
  var role = (p.payload.role || 'user').toUpperCase();
  var content = p.payload.content || '';
  return '[' + role + ']: ' + content;
}).join('\\n');

var sysPrompt = """ + json.dumps(SYSTEM_PROMPT) + """;
var fullPrompt = sysPrompt + '\\n\\nConversation:\\n' + conversation + '\\n\\nExtracted claims:';

// ── 4. Call LLM ──────────────────────────────────────────────────────────────
var llmResp = await _post('host.docker.internal', 11434, '/api/generate', {
  model: '""" + EXTRACT_MODEL + """',
  prompt: fullPrompt,
  stream: false,
  options: { temperature: 0.1, num_predict: 2048 }
});

var rawOutput = (llmResp.response || '').trim();

// Parse JSON array from LLM output (handles markdown fences)
var claims = [];
try {
  var cleaned = rawOutput;
  if (cleaned.startsWith('```')) {
    var lines = cleaned.split('\\n');
    cleaned = lines.slice(1, lines[lines.length-1].trim() === '```' ? -1 : undefined).join('\\n');
  }
  claims = JSON.parse(cleaned);
} catch(e) {
  // Try to find array boundaries
  var start = rawOutput.indexOf('[');
  var end = rawOutput.lastIndexOf(']');
  if (start !== -1 && end !== -1) {
    try { claims = JSON.parse(rawOutput.slice(start, end + 1)); } catch(e2) { claims = []; }
  }
}

if (!Array.isArray(claims)) claims = [];

// ── 5. Embed + upsert each claim to claims collection ────────────────────────
var VALID_CLAIM_TYPES = ['decision','proposal','hypothesis','contradiction','preference','discussion','direction'];
var VALID_STATUS = ['active','contested','superseded','unresolved','confirmed','rejected'];

var claimsWritten = 0;
var claimErrors = [];

for (var c of claims) {
  if (!c.claim) continue;
  var claimType = VALID_CLAIM_TYPES.includes(c.claim_type) ? c.claim_type : 'discussion';
  var status = VALID_STATUS.includes(c.status) ? c.status : 'unresolved';

  try {
    var embedData = await _post('host.docker.internal', 11434, '/api/embeddings', {
      model: '""" + EMBED_MODEL + """',
      prompt: c.claim
    });
    var vector = (embedData && embedData.embedding) ? embedData.embedding : [];
    if (vector.length === 0) {
      claimErrors.push('embed_empty: ' + c.claim.slice(0, 40));
      continue;
    }

    var now = new Date();
    var claimId = crypto.randomUUID();
    await _put('qdrant', 6333, '/collections/claims/points', {
      points: [{
        id: claimId,
        vector: vector,
        payload: {
          claim_type: claimType,
          claim: c.claim,
          stance: c.stance || 'neutral',
          confidence: parseFloat(c.confidence) || 0.7,
          status: status,
          scope: c.scope || 'project',
          applies_if: c.applies_if || {},
          verbatim_ids: messageIds.concat([batchId]),
          linked_claim_ids: [],
          supporting_claim_ids: [],
          contradicting_claim_ids: [],
          superseded_by: null,
          topics: Array.isArray(c.topics) ? c.topics : [],
          project: c.project || '',
          agent: '',
          created_at: now.toISOString(),
          created_at_unix: now.getTime() / 1000,
          last_supported_at: now.toISOString(),
          evidence_count: 1,
          decay_rate: 0.02
        }
      }]
    });
    claimsWritten++;
  } catch(ex) {
    claimErrors.push('claim_err: ' + ex.message);
  }
}

// ── 6. Mark verbatim messages as extracted ────────────────────────────────────
for (var vid of messageIds) {
  await _post('qdrant', 6333, '/collections/verbatim/points/payload', {
    payload: { extracted: true, extraction_batch_id: batchId },
    points: [vid]
  });
}

return [{ json: {
  status: 'done',
  batch_id: batchId,
  messages_processed: points.length,
  claims_extracted: claims.length,
  claims_written: claimsWritten,
  claim_errors: claimErrors,
  raw_output_preview: rawOutput.slice(0, 200)
} }];
"""

# Build the workflow JSON
TRIGGER_NODE_ID = "ec1a1m00-0001-4000-0000-000000000001"
CODE_NODE_ID    = "ec1a1m00-0002-4000-0000-000000000002"
VERSION_ID      = str(uuid.uuid4())

workflow = {
    "id": WORKFLOW_ID,
    "name": WORKFLOW_NAME,
    "active": True,
    "nodes": [
        {
            "id": TRIGGER_NODE_ID,
            "name": "Schedule Trigger",
            "type": "n8n-nodes-base.scheduleTrigger",
            "typeVersion": 1.2,
            "position": [240, 400],
            "parameters": {
                "rule": {
                    "interval": [{"field": "minutes", "minutesInterval": 30}]
                }
            },
        },
        {
            "id": CODE_NODE_ID,
            "name": "Extract Claims",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [500, 400],
            "parameters": {
                "jsCode": EXTRACT_CODE,
                "mode": "runOnceForAllItems",
            },
        },
    ],
    "connections": {
        "Schedule Trigger": {
            "main": [[{"node": "Extract Claims", "type": "main", "index": 0}]]
        }
    },
    "settings": {"executionOrder": "v1"},
    "tags": [],
    "pinData": {},
    "versionId": VERSION_ID,
}

# Check if already exists
existing = psql_query(f"SELECT id FROM workflow_entity WHERE id = '{WORKFLOW_ID}';")
if existing:
    print(f"Workflow {WORKFLOW_ID} already exists — updating.")
    update = True
else:
    update = False

wf_json = json.dumps(workflow)
nodes_json = json.dumps(workflow["nodes"])
conns_json = json.dumps(workflow["connections"])
settings_json = json.dumps(workflow["settings"])

if update:
    sql = f"""
UPDATE workflow_entity
SET name        = '{WORKFLOW_NAME}',
    nodes       = $N${nodes_json}$N$,
    connections = $C${conns_json}$C$,
    settings    = $S${settings_json}$S$,
    active      = true,
    "updatedAt" = NOW()
WHERE id = '{WORKFLOW_ID}';
"""
else:
    sql = f"""
INSERT INTO workflow_entity
  (id, name, nodes, connections, settings, active, "createdAt", "updatedAt", "versionId", "pinData", "meta", "triggerCount", "isArchived")
VALUES (
  '{WORKFLOW_ID}',
  '{WORKFLOW_NAME}',
  $N${nodes_json}$N$,
  $C${conns_json}$C$,
  $S${settings_json}$S$,
  true,
  NOW(), NOW(),
  '{VERSION_ID}',
  '{{}}',
  '{{}}',
  0,
  false
);

INSERT INTO workflow_history ("versionId", "workflowId", nodes, connections, "createdAt", "authors")
VALUES (
  '{VERSION_ID}',
  '{WORKFLOW_ID}',
  $HN${nodes_json}$HN$,
  $HC${conns_json}$HC$,
  NOW(),
  'system'
);
"""

out, err = psql(sql)
if "ERROR" in err:
    print("DB ERROR:", err)
    sys.exit(1)

print(f"{'Updated' if update else 'Created'} workflow: {WORKFLOW_NAME}")
print(f"  ID:        {WORKFLOW_ID}")
print(f"  versionId: {VERSION_ID}")
print(f"  Schedule:  every 30 minutes")
print()
print("Restart n8n to activate:")
print("  docker restart telegram-agent-n8n-1")
