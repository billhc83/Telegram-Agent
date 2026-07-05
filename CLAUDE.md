# Telegram Agent — Claude Code Instructions

## Model roles

| Model | Role |
|-------|------|
| Claude (via mcp-shell relay) | Web search and research only — not the conversational layer |
| Qwen 3 14b | Conversation state extraction, cerebral/facing layer (planned) |
| Qwen 2.5 14b / coder | Local task sub-workflows (commit messages, summarization, etc.) |
| OpenRouter | Research Agent fallback when web access is needed |

---

## How this project works

All logic lives in n8n workflows stored in Postgres. There is no application server to run or deploy — changes take effect on the next n8n execution after the DB is updated.

**Source of truth:** Postgres `workflow_entity` + `workflow_history` tables. The JSON files in `n8n/workflows/` are exports and may be stale.

---

## Workflow patching protocol

n8n executes from `workflow_history` (the `activeVersionId` snapshot). **Always patch both tables or changes won't run.**

```python
# 1. Export current nodes/connections to files (avoids psql truncation)
docker exec telegram-agent-postgres-1 psql -U n8n -d n8n -t -A -c \
  "COPY (SELECT nodes::text FROM workflow_entity WHERE id = '<id>') TO '/tmp/nodes.json'"
docker cp telegram-agent-postgres-1:/tmp/nodes.json /tmp/nodes.json

# 2. COPY export doubles backslashes — decode before parsing
with open('/tmp/nodes.json', 'rb') as f:
    nodes = json.loads(f.read().replace(b'\\\\', b'\\'))

# 3. Patch in Python, write back
with open('/tmp/nodes_new.json', 'w') as f:
    json.dump(nodes, f)

# 4. Push to both tables
docker cp /tmp/nodes_new.json telegram-agent-postgres-1:/tmp/nodes_new.json
docker exec telegram-agent-postgres-1 psql -U n8n -d n8n -c \
  "UPDATE workflow_entity SET nodes = (SELECT pg_read_file('/tmp/nodes_new.json'))::jsonb WHERE id = '<id>'"
docker exec telegram-agent-postgres-1 psql -U n8n -d n8n -c \
  "UPDATE workflow_history SET nodes = (SELECT pg_read_file('/tmp/nodes_new.json'))::jsonb WHERE \"versionId\" = '<versionId>'"

# Get versionId: SELECT "versionId" FROM workflow_entity WHERE id = '<id>'
```

---

## Key workflow IDs

| Workflow | ID |
|---------|-----|
| Main Agent | `13329864-5514-4f83-acf9-a4610cd1e903` |
| Email Cron | `00000012-0000-4000-0000-000000000012` |
| RAG Indexer | `3f7a1c9e-8b2d-4e6a-9c1f-7d4e9a2b5c81` |

**Main agent activeVersionId:** `67d65ad5-853d-4cdd-9291-4044df99279b`
**Email cron activeVersionId:** `afbd17f3-cc54-444a-89cb-891ae2e9150d`

---

## n8n Code node gotchas

These will silently break or throw cryptic errors:

**No `$env` in task runner:**
```javascript
// WRONG — access denied in task runner
const token = $env.TELEGRAM_BOT_TOKEN;

// RIGHT — hardcode or use credentials node
const token = 'YOUR_BOT_TOKEN'; // hardcode the real value directly in the node when needed
```

**No destructuring in function parameters:**
```javascript
// WRONG — VM sandbox rejects this
Object.entries(x).forEach(function([key, val]) { ... });

// RIGHT
Object.entries(x).forEach(function(kv) { var key = kv[0], val = kv[1]; ... });
```

**No inline `//` comments before closing `})`:**
```javascript
// WRONG — the }); becomes part of the comment
items.forEach(function(p) { p.x = 1; // comment });

// RIGHT
items.forEach(function(p) { p.x = 1; });
```

**No `fetch` — use `require('http')`:**
```javascript
const http = require('http');
// For HTTPS: require('https')
```

**Ollama inside n8n container:** Use `host.docker.internal:11434`, not `localhost:11434`.

**File paths inside n8n container:**
- `memory/email_signals.json` → `/memory/email_signals.json`
- `/mnt/data/projects/` → `/projects/` (read-only mount)

---

## n8n Telegram node: inline keyboard

The `additionalFields.reply_markup` field is **not** a schema-defined field — n8n ignores it. Use the proper `replyMarkup` parameter:

```json
{
  "replyMarkup": "inlineKeyboard",
  "inlineKeyboard": {
    "rows": [{
      "row": {
        "buttons": [
          { "text": "👍 Yes", "additionalFields": { "callback_data": "={{ 'ok::' + $json.id }}" } },
          { "text": "👎 No",  "additionalFields": { "callback_data": "={{ 'no::' + $json.id }}" } }
        ]
      }
    }]
  }
}
```

---

## n8n Telegram node: HTML sanitization

The main workflow's `Send Reply` node uses `parse_mode: HTML`. LLM output can contain `<tag>` strings (file paths, template syntax, code examples) that cause Telegram to return 400.

A `Sanitize Reply` Code node sits between `Merge Responses` and `Send Reply`. It escapes stray tags while preserving valid Telegram HTML (`<b>`, `<i>`, `<code>`, `<pre>`, `<a>`, `<blockquote>`).

The email cron's `Send Email Alert` uses an inline IIFE in the text expression to escape `from_name`, `subject`, and `snippet`.

---

## Qdrant collections

| Collection | Dimensions | Embedding model |
|-----------|-----------|----------------|
| `verbatim` | 2560 | `qwen3-embedding:4b` |
| `claims` | 2560 | `qwen3-embedding:4b` |
| `project_rag` | 2560 | `qwen3-embedding:4b` |
| `symbol_index` | 2560 | `qwen3-embedding:4b` |

Qdrant upsert returns `{"status": "ok", "result": {"status": "acknowledged"}}` — check the outer `status`, not `result.status`.

If Qdrant starts erroring with "Too many open files": `docker restart telegram-agent-qdrant-1`.

---

## Webhook secrets

n8n Telegram Trigger secret token = `workflowId_nodeId` with only non-`[a-zA-Z0-9_-]` chars stripped (hyphens are kept).

For the main workflow:
```
13329864-5514-4f83-acf9-a4610cd1e903_a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

The email cron webhook (`/webhook/email-cron-manual`) is registered in `webhook_entity` — no secret required.

---

## Ollama / GPU

Ollama runs as a host systemd service, not in Docker. Config: `/etc/systemd/system/ollama.service.d/override.conf`.

The `LD_LIBRARY_PATH=/opt/rocm/lib:/opt/rocm/lib64` override is **required** for RDNA4 (GFX1200 / RX 9060 XT). Ollama's bundled ROCm library doesn't include GFX1200 targets; the system ROCm 7.2.3 does. Without it, Ollama falls back to CPU silently.

Verify GPU is being used:
```bash
journalctl -u ollama -n 5 --no-pager | grep offload
# Should show: offloaded 41/41 layers to GPU
```

---

## Email cron state

`memory/email_signals.json` is the single state file for the email cron:

- **`seen_ids`** — Gmail IDs where a notification has been sent. Written by `Triage & Score` when an email passes triage. Prevents re-sending the same notification. Do NOT write here during fetch/parse (that was the original bug that caused emails to be silently dropped).
- **`pending`** — Gmail IDs with a sent notification awaiting 👍/👎 response. Cleared by the callback handler. If stuck (notification failed to deliver), clear manually or use `/testemail`.
- **`signals`** — Classified senders. Used by `Parse & Split` to permanently filter future emails from the same address.

To reset and re-surface all unclassified emails: clear `seen_ids` and `pending`.

---

## Route Command node

The `Route Command` Switch node in the main workflow uses indexed output slots. Adding a new command requires:
1. Append rule to `parameters.rules.values`
2. Insert the new connection slot at position `len(rules) - 1` (before Unknown Command)
3. Append Unknown Command connection to the end

Slot indices must exactly match rule array indices or commands route to the wrong handler.
