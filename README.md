# Telegram Agent

A self-hosted personal AI agent that lives in a Telegram chat. It answers questions about your projects, writes code, browses the web, manages email, and learns your preferences over time.

---

## Architecture

```
Telegram ──► ngrok ──► n8n (port 5678)
                          │
                ┌─────────┴──────────┐
                │                    │
           Main Agent          Email Cron
           Workflow             (15 min)
                │
    ┌───────────┼───────────────┐
    │           │               │
 Knowledge   Coding         Research
  Agent      Agent           Agent
    │
    ├── Qdrant (semantic search)
    │     verbatim, claims, project_rag, symbol_index
    │
    └── Ollama (local LLM)
          qwen3-14b-nothink (chat)
          qwen3-embedding:4b (embeddings)
```

**All workflows run in n8n.** n8n is the orchestrator — it handles the Telegram webhook, routes messages, calls Ollama, queries Qdrant, and manages state.

---

## Infrastructure

All services run via `docker-compose.yml` plus Ollama and ngrok as host services.

| Service | Port | Purpose |
|---------|------|---------|
| `n8n` | 5678 | Workflow orchestrator + Telegram webhook receiver |
| `postgres` | 5432 | n8n backend database |
| `qdrant` | 6333 | Vector store (4 collections) |
| `mcp-filesystem` | 3004 | Filesystem MCP: exposes `/mnt`, `/workspace`, `/host-home` |
| `mcp-shell` | 3005/3007 | Shell MCP: bash execution + `claude -p` invocation |
| `mcp-playwright` | 3002 | Browser MCP for web research |
| `mcp-github` | 3006 | GitHub API MCP |
| `mcp-gmail` | 3003 | Gmail MCP (OAuth, autoauth) |
| Ollama | 11434 | Local LLM server (host, not Docker) |
| ngrok | 4040 | Tunnel: public HTTPS → localhost:5678 |

**Start everything:**
```bash
docker compose up -d
# ngrok and Ollama are host services — start separately
```

---

## n8n Workflows

### Main Agent (`13329864-5514-4f83-acf9-a4610cd1e903`)

Triggered by the Telegram webhook. Every incoming message goes through:

```
Telegram Trigger
  → Is Callback?          (👍/👎 email feedback)
  → Is Command?           (/listprojects, /project, /setproject, /clearproject,
                           /fresh, /testemail, /telescope)
  → Load Memory Context   (verbatim + claims from Qdrant)
  → Send Typing           (Telegram typing indicator)
  → Extract Conversation State  (intent, goal, project, mode)
  → Needs Clarification?
  → Route by Agent        (knowledge / coding / research / email / drafting / general)
  → [Agent runs]
  → Merge Responses
  → Sanitize Reply        (escapes stray HTML tags)
  → Send Reply
  → Store Conversation    (verbatim memory → Qdrant)
  → Write Agent Verbatim  (hot-path memory write)
```

**Routing logic** (Extract Conversation State produces `intent`, `goal`, `project`):
- `workspace` → Workspace Agent (git log, diff, status, file list)
- `knowledge` → Knowledge Handler (4-pass RAG + symbol index)
- `coding` → Code Agent (MCP shell + filesystem)
- `research` → Research Agent (Playwright + web)
- `email` → Email Agent (Gmail MCP)
- `drafting` → Drafting Agent
- `general` → General Agent

**Commands:**
- `/setproject <name>` — set active project context
- `/listprojects` — list available projects
- `/clearproject` — clear project context
- `/fresh` — start a fresh conversation (clear context)
- `/telescope` — show recent activity across projects
- `/testemail` — trigger email cron immediately (removes last 3 seen_ids, clears pending)

### Email Cron (`00000012-0000-4000-0000-000000000012`)

Runs every 15 minutes. Also triggerable via `/testemail` or `POST /webhook/email-cron-manual`.

```
Trigger (schedule or webhook)
  → Load State            (reads email_signals.json)
  → Fetch Emails Agent    (Gmail MCP: is:unread in:inbox newer_than:30d, 10 results)
  → Parse & Split         (dedup against seen_ids + pending + classified senders)
  → Triage & Score        (LLM: skip newsletters; score 1-10; write seen_ids + pending)
  → Send Email Alert      (Telegram message with 👍/👎 inline keyboard)
```

**State file:** `memory/email_signals.json`
```json
{
  "signals": [{ "from_email": "...", "label": "important|noise", "count": 1 }],
  "pending": { "<gmail_id>": { "subject": "...", "score": 7 } },
  "seen_ids": ["<gmail_id>", ...],
  "metrics": [{ "ts": "...", "mcp_count": 10, "agent_count": 10 }]
}
```

- `signals` — classified senders (used to filter future emails from same sender)
- `pending` — emails with a sent notification awaiting 👍/👎 response
- `seen_ids` — emails already sent as notifications (prevents re-sending)

**Callback flow:** 👍/👎 tap → Handle Email Callback → clears from pending → writes to signals

### RAG Indexer (`3f7a1c9e-8b2d-4e6a-9c1f-7d4e9a2b5c81`)

Runs every 15 minutes. Indexes project source files into Qdrant `project_rag`.

---

## Memory System (Qdrant)

Four collections, all using `qwen3-embedding:4b` (2560 dimensions):

| Collection | Purpose | Content |
|-----------|---------|---------|
| `verbatim` | Short-term conversation memory | Raw message exchanges (hot-path write after each turn) |
| `claims` | Belief/preference memory | Extracted facts about user preferences, decisions |
| `project_rag` | Project knowledge base | Source file chunks from `/mnt/data/projects/` |
| `symbol_index` | Code symbol declarations | CLI commands, functions, routes, config keys |

**Retrieval pipeline** (Knowledge Handler):
1. Enumerative query → symbol_index (list of commands, routes, etc.)
2. Named-entity query → symbol_index lookup + targeted file search
3. Explanatory query → 4-pass semantic RAG (project_rag)

**Symbol extraction:** `workspace/memory/extract_symbols.py`
- Run `python3 workspace/memory/extract_symbols.py --project <name>` to re-index

**RAG test suite:** `workspace/tests/rag_test_suite.py`

---

## Setup

### Prerequisites
- Docker + Docker Compose
- Ollama installed on host with `qwen3-14b-nothink` and `qwen3-embedding:4b`
- ngrok account (for Telegram webhook delivery)
- Telegram bot token from @BotFather
- Gmail OAuth credentials (see `scripts/gmail-auth.mjs`)

### Steps

1. **Clone and configure:**
   ```bash
   cp .env.example .env
   # Edit .env: POSTGRES_PASSWORD, N8N_ENCRYPTION_KEY, TELEGRAM_BOT_TOKEN,
   #            TELEGRAM_CHAT_ID, N8N_WEBHOOK_URL, OPENROUTER_API_KEY
   ```

2. **Start services:**
   ```bash
   docker compose up -d
   ```

3. **Start ngrok:**
   ```bash
   ngrok http 5678 --domain=your-static-domain.ngrok-free.app
   ```

4. **Configure Ollama for AMD GPU (RDNA4):**
   Add to `/etc/systemd/system/ollama.service.d/override.conf`:
   ```ini
   [Service]
   Environment="OLLAMA_MODELS=/mnt/data/ollama/models"
   Environment="OLLAMA_HOST=0.0.0.0:11434"
   Environment="HSA_OVERRIDE_GFX_VERSION=12.0.0"
   Environment="HIP_VISIBLE_DEVICES=0"
   Environment="OLLAMA_NUM_CTX=32768"
   Environment="LD_LIBRARY_PATH=/opt/rocm/lib:/opt/rocm/lib64"
   ExecStartPre=/bin/sleep 10
   ```
   The `LD_LIBRARY_PATH` override is required for RDNA4 — Ollama's bundled ROCm
   doesn't include GFX1200 targets; the system ROCm 7.2.3 does.

5. **Import n8n workflows:**
   Import from `n8n/workflows/` via n8n UI, or workflows are auto-created on first run
   if the Postgres DB is empty.

6. **Authenticate Gmail:**
   ```bash
   node scripts/gmail-auth.mjs
   ```

7. **Initialize Qdrant collections:**
   ```bash
   bash scripts/init-qdrant.sh
   ```

8. **Index projects:**
   ```bash
   python3 workspace/memory/extract_symbols.py
   bash scripts/init-project-rag.sh
   ```

9. **Register Telegram webhook:**
   n8n registers the webhook automatically when the main workflow activates.
   Verify: `curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo`

---

## Key File Paths

| Path | Purpose |
|------|---------|
| `memory/email_signals.json` | Email cron state (signals, pending, seen_ids) |
| `memory/current_project.json` | Active project per Telegram chat |
| `memory/relay-system-prompt.txt` | System prompt for the relay/Claude agent |
| `workspace/memory/extract_symbols.py` | Symbol index extractor |
| `workspace/memory/extract_claims.py` | Claim extractor (cold-path memory) |
| `workspace/tests/rag_test_suite.py` | RAG retrieval benchmark |
| `n8n/workflows/` | Workflow JSON exports (not always in sync with live DB) |

---

## Patching Workflows

**Never use the n8n UI for structural changes** — DB patches are the reliable path.

n8n executes from `workflow_history` (the `activeVersionId` snapshot), not `workflow_entity` directly. **Always update both tables:**

```python
# Update workflow_entity (what n8n shows in UI)
UPDATE workflow_entity SET nodes = ..., connections = ... WHERE id = '<workflow_id>';

# Update workflow_history (what n8n actually executes)
UPDATE workflow_history SET nodes = ..., connections = ... WHERE "versionId" = '<active_version_id>';

# Get activeVersionId:
SELECT "versionId" FROM workflow_entity WHERE id = '<workflow_id>';
```

Scripts that patch workflows live in `scripts/`. New patches are typically done inline in Claude Code sessions — see `CLAUDE.md` for n8n code node gotchas.

---

## Troubleshooting

**Email cron stuck:** Check `memory/email_signals.json` — if `pending` is non-empty and no notification was received, clear it manually or send `/testemail` (which clears pending automatically).

**Ollama on CPU instead of GPU:** Check `journalctl -u ollama | grep offload`. If `offloaded 0/41 layers`, verify `LD_LIBRARY_PATH=/opt/rocm/lib` is in the service override and restart.

**Qdrant "Too many open files":** `docker restart telegram-agent-qdrant-1`

**Telegram webhook 403:** n8n registers a secret token as `workflowId_nodeId` (hyphens kept). If the secret drifts, restart n8n to re-register.

**n8n workflow not picking up DB changes:** Changes to `workflow_history` take effect on next execution. The `workflow_published_version` FK constraint causes a benign pruning error in logs — ignore it.
