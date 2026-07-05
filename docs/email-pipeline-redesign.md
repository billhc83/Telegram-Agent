# Email pipeline redesign — current state, failures, and proposal

Written 2026-07-05. Source of truth for everything below: live `workflow_entity`/`workflow_history`
rows for `13329864-5514-4f83-acf9-a4610cd1e903`, pulled directly from Postgres the same day, plus
real execution data (`execution_data` table) for the incidents cited, plus a live `tools/list` call
against the Gmail MCP server at `mcp-gmail:3003`. Nothing here is from memory of what was built —
it's what's actually deployed right now.

---

## Part 1 — What the system does right now, in full

### 1.1 High-level path a message takes

```
Telegram Trigger
  -> Is Callback? (button tap vs typed message)
       [button]  -> Route Callback (switch on callback_data prefix)
                      doc_*          -> Handle Doc Callback
                      email_*        -> Handle Email Callback
                      show_email::   -> Handle Show Email        (re-injects synthetic "Show full body of Gmail message ID: X")
                      confirm_action:: -> Handle Confirm Action  (re-injects synthetic "yes")
                      cancel_action::  -> Handle Cancel Action   (re-injects synthetic "no")
       [typed]   -> Extract Message
                      -> Check Pending Confirmation (regex-matches yes/no against pending_confirmations.json)
                           -> Is Pending Confirmation Reply? (switch: confirm / cancel / neither)
                                confirm -> Handle Pending Confirm  -> rewrites message_text to "Confirm action: call X with Y..." -> rejoins at Is Command?
                                cancel  -> Handle Pending Cancel   -> Send Pending Cancel Ack, stop
                                neither -> Is Command? (slash-command check)
                                             -> Route Command (/listprojects, /project, /testemail, etc.)
                                             -> [not a command] Load Project State
                                                  -> Load Memory Context
                                                  -> Extract Conversation State  (LLM call #1: qwen3-14b-nothink, classifies intent+goal+project)
                                                       -> Route by Intent (switch: email / research / knowledge / code / drafting / workspace / general)
                                                            [email] -> Load Queue State -> Classify Message (LLM call #2: qwen3-14b-nothink,
                                                                       message_type task/conversation/ack) -> Schedule Decision -> Cerebral Router
                                                                       -> Start Task -> Email Agent (LLM call #3: qwen3-14b-nothink, full agent loop)
```

Three separate LLM calls happen before the Email Agent even starts running: conversation-state
extraction, message classification, and then the agent itself. The agent is the only one with tool
access.

### 1.2 The Email Agent node itself

`@n8n/n8n-nodes-langchain.agent`, typeVersion 1.7, id `d4e5f6a7-b8c9-0123-defa-234567890123`.

**Tools wired in (`ai_tool` connections):**
- `Gmail MCP` (`@n8n/n8n-nodes-langchain.mcpClientTool`, SSE endpoint `http://mcp-gmail:3003/sse`) — exposes all 19 Gmail MCP tools to the agent (see 1.4 below) with zero restriction at the node level. The system prompt is the only thing stopping it from calling `delete_email`/`batch_delete_emails` directly.
- `Request Confirmation` (`@n8n/n8n-nodes-langchain.toolCode`) — see 1.3.
- `Ollama — Email` (`lmChatOllama`, model `qwen3-14b-nothink:latest`) as the language model.
- `Memory — Email` (`vectorStoreQdrant`) as a memory tool.

**System prompt — full text, character count ~4700, reproduced exactly as deployed:**

> You are an email assistant. Help the user read, reply, compose, and organize their Gmail. Be concise — responses go to Telegram. Use the Gmail MCP tool for all email operations.
>
> HARD RULE — READ THIS FIRST: You must NEVER modify the mailbox (delete, send, label, archive) without calling the request_confirmation tool first and getting an explicit "Confirm action:" message back. This applies even when you are just about to describe a single matching email and ask "is this the one?" — that description must be delivered THROUGH request_confirmation's summary argument, not as your own reply text. If you catch yourself typing "Is this the email you want to delete?" or "Should I send this?" as plain reply text, stop — that sentence belongs inside a request_confirmation call, not in your response.
>
> - Deleting: search for the email first, do not modify anything yet.
>   - Exactly one clear match → call request_confirmation with action_type="delete_email", summary="Is this the email you want to delete?\n\nFrom: <sender>\nSubject: <subject>\nDate: <date>", payload={"messageId": "<the real Gmail id you found>"}. Do not also write your own text reply — the tool's return value IS your entire reply.
>   - Multiple matches → list each one's sender, subject, and date as your own plain reply, and ask the user to clarify. Do not call request_confirmation for this case.
>   - No matches → say so plainly as your own reply. Do not call request_confirmation.
> - Sending or replying: draft the full message first (recipient, subject, body). Then call request_confirmation with action_type="send_email", summary="Send this email?\n\nTo: <to>\nSubject: <subject>\n\n<body preview>", payload={"to": "...", "subject": "...", "body": "..."}.
> - Immediately after calling request_confirmation, your entire reply for this turn must be exactly the string it returns — nothing added, nothing removed.
> - When you receive a message starting with "Confirm action:", it names the exact tool and arguments to call — call it immediately (already confirmed, do not ask again), then reply confirming what was done in plain language.
> - When you receive a message starting with "Action cancelled", just briefly acknowledge it and take no further action.
> - Only modify_email with addLabelIds: ["TRASH"], removeLabelIds: ["INBOX"] is permitted for deletion (moves to Trash, recoverable for 30 days). Never call delete_email or batch_delete_emails — those are permanent and this integration does not have that permission.
>
> Guidelines:
> - To fetch the LATEST email: call search_emails with query="in:inbox" and maxResults=1. Gmail returns results newest-first, so the first result is always the most recent.
> - To show a summary of recent emails: use maxResults=5 with query="in:inbox".
> - Always show: sender, subject, date, and a brief summary of the body.
> - Use the memory tool to recall relevant context from past conversations.
>
> Email body formatting rules (CRITICAL): [paragraph/apostrophe/proofreading/em-dash rules, ~5 lines]
>
> When asked to retrieve or show a specific email by its Gmail message ID (e.g. 'Show full body of Gmail message ID: 19eea13d98369265'), use the read_email tool with that exact messageId. Do not search for the latest email — fetch the specific message directly.
>
> When displaying email content that contains HTML: extract and quote the actual text content directly — do not describe the visual structure, layout, buttons, images, or design elements. [...]
>
> When your response includes a specific email result for READING ONLY (the user asked to see or check an email, not delete or send anything), append a metadata block as the FINAL content of your response — nothing may appear after it:
> `[[ACTION_DATA]] {"version":1,"entities":[{"type":"email","id":"GMAIL_MESSAGE_ID_HERE"}]} [[/ACTION_DATA]]`
> Replace GMAIL_MESSAGE_ID_HERE with the actual Gmail message ID. Only include IDs you actually retrieved. Do not invent IDs. Never use this ACTION_DATA block for delete or send confirmations — those always go through request_confirmation instead.

This one agent is currently responsible for: deciding whether a request is read or write, building
Gmail search queries, judging match count (0/1/many), drafting outgoing email bodies, deciding when
to call `request_confirmation` vs. reply directly, executing the actual mutation after confirmation,
narrating the result back to the user in prose, AND remembering to append a structured metadata
block for read results. Every one of these is a separate judgment call made by a single LLM turn
with no checkpoints between them.

### 1.3 `Request Confirmation` tool — exact code as deployed

`@n8n/n8n-nodes-langchain.toolCode`, id `toolcnf1-0001-4000-0000-000000000001`. Schema (manual):
`{action_type: enum[delete_email, send_email], summary: string, payload: object}`.

```js
var fs = require('fs');
var https = require('https');

function postHttps(hostname, path, body) {
  return new Promise(function(resolve, reject) {
    var payload = JSON.stringify(body);
    var req = https.request(
      { hostname: hostname, path: path, method: 'POST',
        headers: {'Content-Type':'application/json','Content-Length': Buffer.byteLength(payload,'utf8')} },
      function(res) { var data=''; res.on('data', function(c){data+=c;}); res.on('end', function(){ resolve(data); }); }
    );
    req.on('error', reject); req.write(payload); req.end();
  });
}

var chatId = String($('Load Project State').item.json.chat_id);
var confirmationId = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

var pendingPath = '/memory/pending_confirmations.json';
var pendingAll = {};
try { pendingAll = JSON.parse(fs.readFileSync(pendingPath, 'utf8')); } catch(e) {}
pendingAll[chatId] = {
  confirmation_id: confirmationId,
  execution_id: $execution.id,
  action_type: query.action_type,
  payload: query.payload,
  summary: query.summary,
  asked_at: new Date().toISOString()
};
fs.writeFileSync(pendingPath, JSON.stringify(pendingAll));

try {
  var queuePath = '/memory/cerebral_queue.json';
  var cq = JSON.parse(fs.readFileSync(queuePath, 'utf8'));
  var runningIdx = cq.findIndex(function(t) { return t.status === 'running' && String(t.chat_id) === chatId; });
  if (runningIdx >= 0) {
    cq[runningIdx].status = 'waiting_for_user';
    cq[runningIdx].waiting_since = new Date().toISOString();
    fs.writeFileSync(queuePath, JSON.stringify(cq, null, 2));
  }
} catch (e) {}

await postHttps('api.telegram.org', '/bot.../sendMessage', {
  chat_id: Number(chatId), text: String(query.summary), parse_mode: 'HTML',
  reply_markup: { inline_keyboard: [[
    { text: '✅ Confirm', callback_data: 'confirm_action::' + confirmationId },
    { text: '❌ Cancel', callback_data: 'cancel_action::' + confirmationId }
  ]] }
});

return '__CONFIRMATION_SENT__';
```

This is a *tool the agent chooses to call*. Nothing in the framework forces exactly-once invocation
per turn, and nothing stops the agent's own subsequent narration text from disagreeing with what
actually happened.

### 1.4 Resolution path (confirm/cancel, shared between button-tap and typed reply)

`Check Pending Confirmation` regex-matches the raw message against yes/no word lists, checks
freshness (< 15 min), and sets `pending_action`. `Is Pending Confirmation Reply?` switches on that.
`Handle Pending Confirm` marks the pending record resolved and rewrites `message_text` to a
synthetic instruction, built from a small per-`action_type` table:

```js
var builders = {
  delete_email: function(p) {
    return 'Confirm action: call modify_email with messageId="' + p.messageId +
      '", addLabelIds=["TRASH"], removeLabelIds=["INBOX"]. The user already confirmed, proceed now and then tell them it is done.';
  },
  send_email: function(p) {
    var body = String(p.body || '').replace(/"/g, '\\"');
    return 'Confirm action: call send_email with to="' + p.to + '", subject="' + p.subject +
      '", body="' + body + '". The user already confirmed, proceed now and then tell them it is done.';
  }
};
```

This rewritten text re-enters the pipeline at `Is Command?` and flows all the way back through
`Extract Conversation State` → `Route by Intent` → `Email Agent` — a full second agent turn, with
its own LLM call, its own tool-choice decision, and its own free-text narration, is what actually
executes the confirmed mutation. Nothing about the "confirmation already happened" fact is enforced
mechanically at this step beyond the wording of the instruction text.

### 1.5 `Extract Metadata` — the deterministic side-channel that already exists

Runs after `Email Agent` → `Merge Responses`. Checks `pending.execution_id === $execution.id`
(stamped by `Request Confirmation`) to detect "this execution just asked for confirmation" and, if
true, overwrites the agent's own narration text before it reaches the user or conversation memory.
This is the one part of the current design that is already fully deterministic and already doesn't
trust the model's text — it was added after the first hallucination incident (see 2.2). It only
covers the *ask* turn, not the *confirmed-execution* turn.

### 1.6 Gmail MCP server — real tool inventory (queried live just now)

`http://mcp-gmail:3003/sse`, 19 tools total: `send_email`, `draft_email`, `read_email`,
`search_emails`, `modify_email`, `delete_email`, `list_email_labels`, `batch_modify_emails`,
`batch_delete_emails`, `create_label`, `update_label`, `delete_label`, `get_or_create_label`,
`create_filter`, `list_filters`, `get_filter`, `delete_filter`, `create_filter_from_template`,
`download_attachment`.

The four relevant to this pipeline, exact schemas:

- **`search_emails({query: string, maxResults?: number})`** — returns a single text block, NOT
  structured JSON. Confirmed by a live call (`query: "in:inbox", maxResults: 2`):
  ```
  ID: 19f2cdfa3d41c30d
  Subject: Your receipt from Anthropic, PBC #2667-2673-7789
  From: "Anthropic, PBC" <invoice+statements@mail.anthropic.com>
  Date: Sat, 4 Jul 2026 11:24:40 +0000

  ID: 19f2a3d15af203d1
  Subject: We've updated our Privacy Notice
  From: 407 ETR <info@407etr.com>
  Date: Fri, 03 Jul 2026 17:05:45 -0600
  ```
  Fixed field labels (`ID:`, `Subject:`, `From:`, `Date:`), blank-line-separated records — this is
  regex-parseable with a single fixed pattern, not something that needs an LLM to interpret.

- **`read_email({messageId: string})`** — returns full message content (headers + body).

- **`modify_email({messageId: string, addLabelIds?: string[], removeLabelIds?: string[]})`** —
  the only delete mechanism this system uses (moves to Trash via label change, never calls the
  actually-destructive `delete_email`/`batch_delete_emails`).

- **`send_email({to: string[], subject: string, body: string, htmlBody?, mimeType?, cc?, bcc?, threadId?, inReplyTo?, attachments?})`**.

None of these four require any judgment to invoke correctly once their arguments are known — they
are pure mechanical calls. The only genuinely fuzzy input across all of them is: (a) what search
query captures what the user means by "the Indeed email" or "that thing from Sarah last week", and
(b) what a drafted email's subject/body should say.

---

## Part 2 — Where it fails, in detail, with evidence

### 2.1 Failure — schema-validated tool does not guarantee the model calls it (fixed, but documents a real limitation)

First test of `Request Confirmation`: model had the tool available, valid schema, and simply did
not call it — fell back to free prose ("Is this the email you want to delete?") despite the system
prompt instructing otherwise at the time. Function-calling schemas only constrain *arguments*, never
whether the model decides to invoke the tool in the first place. Fixed by moving the hard rule to
the first paragraph and naming the exact wrong behavior. This fix is prompt-order-dependent and
therefore fragile — it depends on the model continuing to read and prioritize the first paragraph
over the accumulating instructions after it, which gets harder as the prompt grows.

### 2.2 Failure — model hallucinates completion narration after a *different* tool call (fixed for the ask-turn, not generalized)

Model correctly called `request_confirmation`, then, in its own final reply text, wrote "has been
moved to Trash" — without ever calling `modify_email` in that turn. Verified false via a direct
Gmail query. Root cause: the framework does not gate the agent's final narration on which tools it
actually invoked; narration and tool invocation are independent generation steps the model can get
out of sync between. Fixed narrowly: `Extract Metadata` now checks `pending.execution_id ===
$execution.id` and overwrites the text if this execution is the one that just asked for
confirmation. **This only suppresses the false claim on the ask turn.** It does nothing for the
resolution turn — see 2.4, where the exact same failure mode recurs on the turn that matters most
(the one that's supposed to have actually deleted something).

### 2.3 Failure — chat-scoped race between original request and fast "yes" reply (real production incident, fixed)

Executions `8932` (original delete request for a 407 ETR email) and `8934` (the user's "yes",
arriving while 8932 was still finishing) — confirmed from the actual `execution_data` rows.

- `8932`'s `runData` shows the full chain through `Request Confirmation` → `Email Agent` →
  `Extract Metadata` → ... → `Complete Task`, i.e. it was still mid-flight.
- `8934`'s `runData` is short: `Check Pending Confirmation` → `Is Pending Confirmation Reply?` →
  `Handle Pending Confirm` → `Is Command?` → `Load Queue State` → `Classify Message` →
  `Schedule Decision` → `Cerebral Router` → **`Queue Task`** → **`Send Queued`**.

Root cause chain: (a) `is_busy` was computed globally, not per-chat, so 8934 saw 8932 as "busy" and
routed to the queue instead of executing; (b) `Handle Pending Confirm`'s original code did
`delete all[chatId]` on the pending record immediately, before 8932's own `Extract Metadata` could
still check `execution_id` against it, so 8932's hallucinated "has been moved to Trash" text was
never suppressed and reached the user; (c) the real confirmed delete instruction sat `queued`
forever and never ran. Verified via direct Gmail query that the 407 ETR email was never moved to
Trash, despite the bot's message claiming it was. Fixed by the 5-part chat-scoped queue redesign
(chat-scoped `is_busy`, `waiting_for_user` status, task reactivation instead of duplicate queue
entries, unconditional now-scheduling for confirmation replies, resolved-not-deleted pending state).
Verified fixed under the identical race condition in a later retest.

### 2.4 Failure — duplicate tool call within a single agent turn (UNFIXED, currently live)

Execution `8949`, delete request for an Indeed job-posting email. `Request Confirmation` node
`runData` shows **two separate runs**:

| run | startTime (epoch ms) | source |
|---|---|---|
| 0 | 1783264469464 | `Email Agent` run 0 |
| 1 | 1783264489117 | `Email Agent` run 0 |

19.65 seconds apart, **both sourced from the same Email Agent run** (not two separate user turns —
one agent turn called the tool twice). Both calls carried byte-identical arguments:

```json
{"action_type": "delete_email",
 "summary": "Is this the email you want to delete?\n\nFrom: Indeed <donotreply@match.indeed.com>\nSubject: Product Support Technician @ MACLEAN ENGINEERING & MARKETING CO. LIMITED\nDate: Fri, 03 Jul 2026 22:13:47 +0000",
 "payload": {"messageId": "19f2a0b8e9dcb23e"}}
```

In production this sends the user two identical sets of Confirm/Cancel Telegram messages for the
same email, with two different `confirmation_id`s racing to overwrite the same
`pending_confirmations.json[chatId]` entry. Whichever write lands last is the one that survives;
tapping a button generated from the other becomes a stale/expired confirmation. This is not a
prompt-wording problem — the instructions already say "call request_confirmation" (singular) — it's
the model non-deterministically re-invoking a tool it already successfully called earlier in the
same generation.

Separately, in this same execution, the Email Agent's own final narration text (captured from
`Email Agent`'s output in `8949`) was: *"The email from Indeed with subject 'Product Support
Technician...' has been moved to Trash. Let me know if you need to delete anything else."* — a
false completion claim on the **ask** turn, before any user confirmation and before `modify_email`
was ever called in this execution. This instance was successfully suppressed by the 2.2 fix
(`confirmation_just_requested` check), so the user never saw it — but it demonstrates the
hallucination-after-tool-call failure mode is still happening on every single ask turn; it is
currently being caught, not prevented.

### 2.5 Failure — silent non-execution with false completion claim on the confirmed-execution turn (UNFIXED, most severe, currently live)

Execution `8950` — the confirmed-execution turn following `8949`/the user's "yes". `Handle Pending
Confirm` built the exact instruction: `'Confirm action: call modify_email with
messageId="19f2a0b8e9dcb23e", addLabelIds=["TRASH"], removeLabelIds=["INBOX"]. The user already
confirmed, proceed now and then tell them it is done.'`

`8950`'s full `runData` node list:

```
Telegram Trigger, Is Callback?, Extract Message, Check Pending Confirmation,
Is Pending Confirmation Reply?, Handle Pending Confirm, Is Command?, Load Queue State,
Classify Message, Schedule Decision, Cerebral Router, Start Task, Pick Ack Message, Send Ack,
Load Project State, Load Memory Context, Is Telescope?, Extract Conversation State,
Needs Clarification?, Route by Intent, Ollama — Email, Email Agent, Merge Responses,
Extract Metadata, Write Agent Verbatim, Document Loader — Store, Text Splitter — Store,
Embeddings — Store, Store Conversation, Sanitize Reply, Is Silent Confirmation?, Has Entities?,
Send Reply, Complete Task, Has Next Task?, Write Recent Context
```

**`Gmail MCP` does not appear anywhere in this list.** The tool node has zero runs in this
execution — not "ran and failed," not "ran and returned an error" — it was never invoked at all.
And yet `Email Agent`'s captured output for this run was:

> "The email from Indeed with subject "Job Opportunity: Senior Software Engineer" has been moved to Trash. Let me know if you need to delete anything else."

Two things are notable: (1) given a maximally explicit, unambiguous, already-confirmed instruction
naming the exact tool and exact arguments to call, the model still didn't call it; (2) it reported
success anyway, and the subject line in its narration ("Job Opportunity: Senior Software Engineer")
doesn't even match the subject line that was actually in the confirmation prompt ("Product Support
Technician @ MACLEAN ENGINEERING & MARKETING CO. LIMITED" from `8949`) — the model appears to have
partially fabricated the email identity itself. This is not caught by the existing
`confirmation_just_requested` deterministic gate, because that gate only fires on the *ask* turn
(`Request Confirmation` stamping `execution_id`) — there is currently no equivalent deterministic
check on the *execution* turn, so this false "done" message reaches the user, and the email in
question was never actually moved to Trash.

### 2.6 Root cause synthesis

Every failure in 2.1-2.5 is an instance of the same underlying issue: **the pipeline asks an LLM to
make an autonomous judgment call at a point where the correct action is already fully determined by
preceding deterministic state**, and trusts the LLM's own subsequent narration as a proxy for
whether that action actually happened. Specifically:

- Whether to call `request_confirmation` (2.1) is not actually a judgment call — the deleting/
  sending code path is already known once intent classification says `delete_email`/`send_email`.
- Whether the narration matches reality (2.2, 2.4, 2.5) is unenforceable as long as narration
  generation and tool invocation are two independent, only loosely-coupled steps in the same
  free-running agent loop.
- Whether the confirmed action actually executes (2.5) is currently 100% dependent on the model
  choosing, for the *n*th time in a row, to call a tool correctly — even when the instruction has
  already had every ounce of ambiguity removed from it ("call modify_email with messageId=X,
  addLabelIds=[...]"). At that point there is no remaining reason for an LLM to be in the loop at
  all; it is doing zero interpretation work and is still the single point of failure.

The user's framing from this session is the accurate one: the model should function as a narrow
message-relayer for the one step that's genuinely fuzzy (turning natural language into a structured
query or draft), not as an autonomous agent making repeated tool-invocation judgment calls for
mechanical steps that have no ambiguity left in them by the time they're reached.

---

## Part 3 — Proposed redesign, in detail

### 3.1 Principle

Every email operation is split into exactly two kinds of step, and they never share a runtime:

1. **One narrow LLM call per operation**, plain text/JSON generation only — **no tool access, no
   agent loop, no multi-step reasoning**. Its only job is to turn the user's natural-language
   request into a fixed-shape structured output (a search query string, or a `{to, subject, body}`
   draft). It cannot call Gmail. It cannot decide whether to ask for confirmation. It cannot narrate
   a result. It cannot fail "silently" in the way 2.5 does, because it has no side effects to skip —
   its only possible failure is returning malformed JSON, which is trivially detectable in code.
2. **Deterministic code for everything else**: calling the Gmail MCP server directly via the plain
   `@n8n/n8n-nodes-langchain.mcpClient` node (not `mcpClientTool` — the plain, non-agent-bound
   variant that takes explicit parameters and has ordinary `main` input/output), branching on
   result count, building confirmation text and outgoing Telegram messages from the actual API
   response fields, writing pending-confirmation state, and executing the confirmed mutation.

The `Email Agent` LangChain agent node, the `Request Confirmation` tool, and the entire
"agent decides when to ask, agent narrates the result" model are removed for every operation this
covers. There is no more free-running tool loop anywhere in the email path.

### 3.2 Per-operation pipelines

**`delete_email`:**
1. Narrow LLM call ("query extractor"): input = user message + recent conversation context, output
   = `{"query": "<gmail search syntax>", "maxResults": <int>}`. Plain `httpRequest` node to Ollama,
   `format: 'json'`, no tools — structurally identical to the existing `Classify Message` /
   `Extract Conversation State` nodes already in this workflow, just a new prompt.
2. Deterministic: `MCP Client` node calls `search_emails` with that query.
3. Deterministic (Code node): parse the fixed-format text response (regex on `ID:`/`Subject:`/
   `From:`/`Date:` — confirmed stable format in 1.6) into a list of `{id, subject, from, date}`.
4. Branch in code on `list.length`:
   - `0` → send a canned "No matching email found for '<query>'." Done, no LLM narration needed.
   - `1` → build the confirm prompt directly from the parsed fields (no LLM), write
     `pending_confirmations.json`, send Confirm/Cancel buttons — this is exactly what
     `Request Confirmation`'s code already does, minus being wrapped in a tool the model has to
     remember to call.
   - `>1` → build a numbered list from the parsed fields (no LLM) and ask the user to pick one or
     narrow the query. No confirmation state is written yet.
5. On confirm (button tap or typed "yes", same shared resolution code as today): deterministic
   `MCP Client` call to `modify_email` with the stored `messageId`, `addLabelIds: ["TRASH"]`,
   `removeLabelIds: ["INBOX"]`. Reply built from a canned template referencing the stored
   subject/sender — not generated by an LLM, so there is nothing for it to hallucinate.
6. On cancel: unchanged from today (canned "Cancelled — email kept.").

**`send_email` / `write_email`:**
1. Narrow LLM call ("draft composer"): input = user's instructions (+ original email content,
   fetched deterministically first via `read_email`/`search_emails`, if this is a reply), output =
   `{"to": [...], "subject": "...", "body": "..."}`. Still no tools — pure generation.
2. Deterministic: build the confirm prompt directly from the drafted fields, write pending state,
   send buttons.
3. On confirm: deterministic `MCP Client` call to `send_email` with the exact stored `to`/
   `subject`/`body` — the exact same struct the drafting call produced, not re-derived or
   re-generated at execution time, so there is no opportunity for the executed content to drift
   from what the user actually approved.
4. On cancel: canned acknowledgement, draft discarded.

**`check_email` / `read_email` (search-based — "what's my latest email", "show me emails from
Sarah", inbox summaries):**
1. Narrow LLM call ("query extractor," can reuse the same prompt/node as delete's step 1 — same
   shape, `{query, maxResults}`).
2. Deterministic `MCP Client` call to `search_emails`.
3. Deterministic parse of the fixed-format response (same parser as delete's step 3).
4. `0` results → canned "no matching emails" reply, no LLM involved.
5. `1+` results → **this is the one place a second narrow LLM call is justified**: summarizing
   arbitrary email content (especially HTML bodies) into readable Telegram text is genuine language
   work, not mechanical formatting. Still no tools — plain text in, plain text out, given the
   already-fetched sender/subject/date/snippet fields as input.
6. Deterministic: build the "show full email" button directly from the **known** message ID
   captured in step 3, in code — this fully replaces the `[[ACTION_DATA]]` convention. There is no
   more reliance on the model remembering to append a metadata block; the ID was never something
   the model needed to echo back in the first place, since code already had it from the search
   result.

**`read_email` (direct ID — "show full body of message X," including the existing "show full
email" button follow-up flow):**
1. No search, no query extraction — deterministic `MCP Client` call to `read_email` with the known
   `messageId` straight away.
2. Narrow LLM call to summarize/clean the HTML body for Telegram display (same justification as
   above — the only genuinely fuzzy part is turning arbitrary HTML into readable text).
3. Deterministic reply send.

### 3.3 What this removes / replaces

| Current | Becomes |
|---|---|
| `Email Agent` (LangChain agent, full tool loop) | Removed. Replaced by per-operation Code/httpRequest node chains. |
| `Request Confirmation` (toolCode, agent-invoked) | Removed as a tool. Its body (write pending state, send buttons) becomes a plain Code node called directly from the delete/send pipelines — no longer something an LLM decides whether to invoke. |
| `Gmail MCP` (`mcpClientTool`, agent-bound) | Replaced by one or more plain `mcpClient` nodes (not agent-bound), called directly from code with explicit tool name + arguments per step. |
| `Handle Pending Confirm`'s "Confirm action: ..." text-instruction rewrite, which re-enters the whole agent pipeline for a second LLM turn | Replaced by a direct deterministic dispatch: confirmed action type + stored payload → the matching `MCP Client` execute call, no LLM turn at all. |
| `[[ACTION_DATA]]` metadata-block convention for read results | Replaced entirely — the message ID is known in code from the search/fetch step and never needs to round-trip through model output. |
| `Extract Metadata`'s `confirmation_just_requested` check | No longer needed in its current form once `Request Confirmation` isn't agent-invoked — there's no agent narration to suppress on the ask turn, because the ask turn no longer runs an agent at all. |

### 3.4 What stays unchanged

- `pending_confirmations.json` shape and the resolve-not-delete pattern.
- The chat-scoped `cerebral_queue.json` design (`waiting_for_user`, reactivation, chat-scoped
  `is_busy`) — none of that was email-specific, it was a queue-correctness fix and still applies.
- The shared button-tap/typed-reply resolution mechanism (`Handle Confirm Action`/
  `Handle Cancel Action` re-injecting synthetic messages) — still the right way to keep both entry
  points on one code path, just now dispatching to deterministic execution instead of a second
  agent turn.
- `Extract Conversation State` and `Classify Message` upstream — intent routing into "this is an
  email operation" stays exactly as-is; only what happens *after* `Route by Intent` picks `email`
  changes.
- The Gmail MCP server itself and its 19 tools — no server-side changes.

### 3.5 Net effect on prompt size / hallucination surface

The current single ~4700-character system prompt, carrying instructions for four different
operations plus formatting rules plus the confirmation protocol plus the metadata-block convention,
is replaced by four-to-five narrow single-purpose prompts (query extraction, draft composition,
content summarization), each only a few sentences, each with no tool-calling surface at all. This
directly tests the user's hypothesis that prompt size contributes to hallucination — under the new
design there's no longer a single outsized prompt to indict either way, since no individual LLM call
does more than one job.

### 3.6 Decisions (resolved 2026-07-05 — optimizing for reliability/completeness over build effort)

1. **Query extractor**: one shared node (`Extract Search Query`) used by both `delete_email` and
   `check_email`/`read_email`-by-search. This is legitimate reuse — it's the same job (NL → Gmail
   search query) called from two call sites, not one node doing two different jobs. The draft
   composer (a genuinely different job/output shape) stays a separate node.
2. **Disambiguation**: deterministic bare-number match, then substring match against sender/subject,
   against the already-known candidate list; a small LLM call is the fallback only when neither
   deterministic method resolves it.
3. **Reply-to-thread sends**: in scope for the initial build, not deferred.
4. **Migration order**: all four operations (delete, send, search-read, direct-ID-read) built and
   cut over together in one pass — most of the cost is the shared plumbing (search, parser,
   pending-confirmation/disambiguation state, MCP call helper), so a partial rollout doesn't save
   much and would leave the old failure-prone agent path live for some operations while a new path
   exists for others.
5. **Trusting `cs.goal` for dispatch**: yes, with a bounded-risk fallback — if `cs.goal` is anything
   other than a recognized `delete_email`/`write_email` value, the dispatch switch's default output
   is the **read-only search pipeline**, never delete or send. A misclassification can, at worst,
   show the wrong kind of read result; it can never trigger a mutation, because mutations still
   require the user to hit Confirm on a prompt built from real fetched data regardless of how they
   got routed there. `cs.goal` is computed by `Extract Conversation State`, already a `main`-connected
   node, so the dispatch switch reads it via `$('Extract Conversation State').item.json.conversation_state.goal` —
   no need to thread it through every intermediate node's return object.

---

## Part 4 — Implementation spec (exact nodes, code, wiring)

### 4.0 Shared helper: direct Gmail MCP calls from Code nodes

Every deterministic node that needs to call Gmail does so via a plain `n8n-nodes-base.code` node
using the same raw-socket JSON-RPC technique already proven this session for verification queries
(SSE handshake → parse `sessionId` → POST to `/message?sessionId=` → read the async result back off
the same connection). This replaces the originally-considered `@n8n/n8n-nodes-langchain.mcpClient`
node — using it would mean guessing at an undocumented parameter shape (dynamic tool-name/argument
expressions) with no way to verify it works before committing; the raw-socket approach is code we
have already tested to work reliably against this exact server, at `mcp-gmail:3003` in-network (same
hostname the agent's own `Gmail MCP` tool node already uses).

Pasted into every node that needs a Gmail call (`Search Emails`, `Fetch Email By ID`,
`Execute Confirmed Delete`, `Execute Confirmed Send`):

```js
function callGmailMCP(toolName, args) {
  return new Promise(function(resolve, reject) {
    var net = require('net');
    var http = require('http');
    var sock = net.createConnection({ host: 'mcp-gmail', port: 3003 }, function() {
      sock.write('GET /sse HTTP/1.1\r\nHost: mcp-gmail:3003\r\nAccept: text/event-stream\r\n\r\n');
    });
    var buf = '';
    var sessionId = null;
    var done = false;
    var timeout = setTimeout(function() {
      if (!done) { done = true; sock.destroy(); reject(new Error('MCP timeout: ' + toolName)); }
    }, 15000);

    function sendCall() {
      var payload = JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: toolName, arguments: args } });
      var req = http.request({
        hostname: 'mcp-gmail', port: 3003, path: '/message?sessionId=' + sessionId, method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) }
      }, function(res) { res.on('data', function(){}); res.on('end', function(){}); });
      req.on('error', function(e) { if (!done) { done = true; clearTimeout(timeout); reject(e); } });
      req.write(payload); req.end();
    }

    sock.on('data', function(chunk) {
      buf += chunk.toString();
      if (!sessionId) {
        var m = buf.match(/sessionId=([a-zA-Z0-9\-]+)/);
        if (m) { sessionId = m[1]; sendCall(); }
      }
      var lines = buf.split('\n');
      for (var i = 0; i < lines.length; i++) {
        if (lines[i].indexOf('data: ') === 0) {
          try {
            var d = JSON.parse(lines[i].slice(6));
            if (d && d.id === 1 && !done) { done = true; clearTimeout(timeout); sock.destroy(); resolve(d); }
          } catch(e) {}
        }
      }
    });
    sock.on('error', function(e) { if (!done) { done = true; clearTimeout(timeout); reject(e); } });
  });
}

function parseSearchResults(mcpResult) {
  var text = (mcpResult && mcpResult.result && mcpResult.result.content && mcpResult.result.content[0] && mcpResult.result.content[0].text) || '';
  var blocks = text.split(/\n\n+/).filter(function(b) { return b.trim().length > 0; });
  return blocks.map(function(b) {
    var id = (b.match(/^ID:\s*(.+)$/m) || [])[1] || '';
    var subject = (b.match(/^Subject:\s*(.+)$/m) || [])[1] || '(no subject)';
    var from = (b.match(/^From:\s*(.+)$/m) || [])[1] || '(unknown sender)';
    var date = (b.match(/^Date:\s*(.+)$/m) || [])[1] || '';
    return { id: id, subject: subject, from: from, date: date };
  }).filter(function(c) { return c.id; });
}
```

### 4.1 New dispatch node: `Route Email Goal`

Type `n8n-nodes-base.switch`. Replaces the direct wire from `Start Task` into `Email Agent` (for the
`email`-intent branch only — everything else `Start Task` currently feeds is untouched). Rules,
evaluated in order:

1. `direct_read` — `{{ /^Show full body of Gmail message ID: /.test($json.message_text) }}` is true.
   (Deterministic bypass: this exact string is only ever produced by `Handle Show Email`'s own
   synthetic re-injection, so there's no need to trust `cs.goal` for it.)
2. `delete_email` — `{{ $('Extract Conversation State').item.json.conversation_state.goal }}` equals
   `"delete_email"`.
3. `send_email` — same field equals `"write_email"`.
4. **fallback/default** `check_email` — anything else (`check_email`, `read_email`, or an
   unrecognized value). Per decision 5, this is intentionally the safe default.

### 4.2 Pipeline: `delete_email`

- **`Extract Search Query`** (`n8n-nodes-base.httpRequest`, same shape as the existing
  `Classify Message` node — POST to `http://host.docker.internal:11434/api/chat`,
  `model: qwen3-14b-nothink:latest`, `format: 'json'`, `options.temperature: 0`). System prompt:
  > You are a Gmail search query extractor for a personal assistant. Read the user's message and
  > recent conversation context and output ONLY JSON: {"query": "<gmail search syntax>", "maxResults": <int>}.
  > Use Gmail search operators: from:, subject:, in:inbox, newer_than:Nd, older_than:Nd, has:attachment.
  > Default maxResults to 3 unless the user clearly wants one specific email (use 1) or a broader
  > list (use 10). Output ONLY the JSON object.
  User content: recent conversation turns (same slice already computed in `Extract Conversation
  State`) + the current message. Shared by both this pipeline and 4.4.

- **`Search Emails`** (Code) — `callGmailMCP('search_emails', {query, maxResults})` +
  `parseSearchResults()`. Output: `{candidates: [...], query}`. Shared by 4.2 and 4.4.

- **`Delete Match Count`** (Switch on `candidates.length`): `0` / `1` / fallback (`many`).

- **`0`** → Telegram send `"No email found matching '<query>'."` → done.

- **`1`** → **`Build Delete Confirmation`** (Code) — the same body `Request Confirmation` already
  has today (write `pending_confirmations.json[chatId]`, flip the running queue task to
  `waiting_for_user`, send the Confirm/Cancel Telegram buttons via `postHttps`), just as a plain
  Code node in the main chain instead of an agent-invoked tool:
  ```js
  var fs = require('fs'); var https = require('https');
  function postHttps(hostname, path, body) { /* unchanged from Request Confirmation */ }
  var item = $input.first().json;
  var chatId = String(item.chat_id);
  var c = item.candidates[0];
  var confirmationId = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  var summary = 'Is this the email you want to delete?\n\nFrom: ' + c.from + '\nSubject: ' + c.subject + '\nDate: ' + c.date;
  var pendingPath = '/memory/pending_confirmations.json';
  var all = {}; try { all = JSON.parse(fs.readFileSync(pendingPath, 'utf8')); } catch(e) {}
  all[chatId] = { confirmation_id: confirmationId, action_type: 'delete_email',
    payload: { messageId: c.id, subject: c.subject, from: c.from }, summary: summary, asked_at: new Date().toISOString() };
  fs.writeFileSync(pendingPath, JSON.stringify(all));
  try {
    var cq = JSON.parse(fs.readFileSync('/memory/cerebral_queue.json', 'utf8'));
    var idx = cq.findIndex(function(t) { return t.status === 'running' && String(t.chat_id) === chatId; });
    if (idx >= 0) { cq[idx].status = 'waiting_for_user'; cq[idx].waiting_since = new Date().toISOString(); fs.writeFileSync('/memory/cerebral_queue.json', JSON.stringify(cq, null, 2)); }
  } catch(e) {}
  await postHttps('api.telegram.org', '/botYOUR_BOT_TOKEN/sendMessage', {
    chat_id: Number(chatId), text: summary, parse_mode: 'HTML',
    reply_markup: { inline_keyboard: [[
      { text: '✅ Confirm', callback_data: 'confirm_action::' + confirmationId },
      { text: '❌ Cancel', callback_data: 'cancel_action::' + confirmationId } ]] }
  });
  return [{ json: { chat_id: chatId } }];
  ```
  Note there is no `execution_id` stamp and no LLM narration here at all — nothing to suppress,
  because there's no agent turn producing text that could disagree with what this code just did.

- **`many`** → **`Build Delete Disambiguation`** (Code) — writes
  `/memory/pending_disambiguation.json[chatId] = {type: 'delete_email', candidates, query, asked_at}`,
  sends a plain numbered-list Telegram message built from `candidates` (sender/subject/date), no
  buttons. Done.

### 4.3 Pipeline: `send_email`

- **`Classify Send Type`** (Code, deterministic regex on `message_text` for
  `/\b(reply|respond|reply back)\b/i` plus a reference cue) → `is_reply: bool`.
- If `is_reply`: reuse `Extract Search Query` + `Search Emails` (4.2) to find the target thread. `0`
  or `many` results get the same not-found/disambiguation handling as delete, except the resolved
  candidate feeds into `read_email` (via `callGmailMCP`) to fetch the original body, which becomes
  reply context for the composer below. `1` result → straight to `read_email`.
- If not a reply: skip search, go straight to composing.
- **`Draft Composer`** (`httpRequest`, same shape as `Extract Search Query`). System prompt:
  > You draft emails for a personal assistant. Given the user's instructions (and, if replying, the
  > original email's sender/subject/body), output ONLY JSON: {"to": ["<email address>"], "subject":
  > "<subject>", "body": "<plain text body>", "needs_clarification": <bool>, "clarification_question":
  > "<string or null>"}. Rules: "to" must be a literal email address mentioned in the message, or the
  > original sender's address if this is a reply. NEVER invent or guess an address you were not
  > given — if you cannot determine a real one, set needs_clarification=true and ask for it. Body:
  > one paragraph per line, blank line between paragraphs, correct apostrophes, no em/en dashes,
  > proofread before returning.
- **`Needs Clarification?`** (IF on `needs_clarification`) → if true, send
  `clarification_question` as a plain reply, done (no pending state written). If false, continue.
- **`Build Send Confirmation`** (Code) — same pattern as `Build Delete Confirmation`, `action_type:
  'send_email'`, `payload: {to, subject, body, threadId?, inReplyTo?}`.

### 4.4 Pipeline: `check_email` / `read_email` (search-based)

- `Extract Search Query` + `Search Emails` (shared with 4.2).
- `0` results → canned `"No email found matching '<query>'."`.
- `1+` results → **`Summarize Email Results`** (`httpRequest`, plain text output not JSON): given
  the candidate list (and, if `maxResults` was 1 — a singular request — the full body of that one
  candidate via an extra `read_email` call first), produce Telegram-ready text: sender, subject,
  date, and a brief content summary per email. Same HTML-stripping/no-visual-description rule the
  current system prompt already has.
- **`Send Check Reply With Button`** (Code + Telegram) — sends the summary text with a "Show full
  email" button built directly from `candidates[0].id` in code (`callback_data:
  'show_email::' + candidates[0].id`) — no `[[ACTION_DATA]]` block, nothing for a model to remember
  to append. Routes to the existing `Handle Show Email` callback handler unchanged.

### 4.5 Pipeline: direct-ID read

- **`Fetch Email By ID`** (Code) — `callGmailMCP('read_email', {messageId})`, where `messageId` is
  regex-captured from `message_text` in `Route Email Goal`'s `direct_read` branch (deterministic —
  we generated this exact string ourselves in `Handle Show Email`).
- **`Summarize Full Email`** (`httpRequest`, plain text) — same HTML-stripping rules as 4.4.
- Telegram send. No button needed (already showing full content).

### 4.6 Disambiguation resolution (new, parallel to the existing confirm/cancel check)

New state file `/memory/pending_disambiguation.json`, shape `{[chat_id]: {type, candidates, query,
asked_at}}`. New node **`Check Pending Disambiguation`** (Code), running after `Check Pending
Confirmation` finds nothing actionable:

```js
var fs = require('fs');
var item = $input.first().json;
var chatId = String(item.chat_id);
var rawMsg = (item.message_text || '').trim();
var pending = null;
try { var all = JSON.parse(fs.readFileSync('/memory/pending_disambiguation.json','utf8')); pending = all[chatId] || null; } catch(e) {}
var resolved = null;
if (pending) {
  var ageMs = Date.now() - new Date(pending.asked_at).getTime();
  if (ageMs >= 0 && ageMs < 15 * 60 * 1000) {
    var numMatch = rawMsg.match(/^(\d+)\b/);
    if (numMatch) {
      var idx = parseInt(numMatch[1], 10) - 1;
      if (idx >= 0 && idx < pending.candidates.length) resolved = pending.candidates[idx];
    }
    if (!resolved) {
      var lower = rawMsg.toLowerCase();
      var matches = pending.candidates.filter(function(c) {
        return lower.indexOf((c.from || '').toLowerCase().split('<')[0].trim()) >= 0 ||
               lower.indexOf((c.subject || '').toLowerCase().slice(0, 15)) >= 0;
      });
      if (matches.length === 1) resolved = matches[0];
    }
    // else: neither deterministic method resolved it -> fall back to a small LLM call
    // (given the candidate list + rawMsg, output {"index": <int or null>}), per decision 2.
  }
}
return [{ json: Object.assign({}, item, {
  disambiguation_pending: !!pending,
  disambiguation_type: pending ? pending.type : null,
  disambiguation_resolved: resolved,
  is_disambiguation_resolution: !!resolved
}) }];
```

If resolved: `delete_email` type → feed the resolved single candidate directly into
`Build Delete Confirmation` (search already done, skip straight to confirmation). `check_email` type
→ feed into `Summarize Email Results` for that one candidate.

### 4.7 Confirmation resolution — deterministic dispatch replacing the agent re-entry

`Handle Pending Confirm` is rewritten to stop producing a `"Confirm action: ..."` text instruction
and stop re-entering the whole pipeline for a second agent turn. New body:

```js
var fs = require('fs');
var item = $input.first().json;
var chatId = String(item.chat_id);
var pendingPath = '/memory/pending_confirmations.json';
var all = {};
try { all = JSON.parse(fs.readFileSync(pendingPath, 'utf8')); } catch(e) {}
var pending = all[chatId];
if (pending) { pending.resolved = true; pending.resolved_at = new Date().toISOString(); fs.writeFileSync(pendingPath, JSON.stringify(all)); }
return [{ json: Object.assign({}, item, { resolved_pending: pending || null }) }];
```

Feeds into new Switch **`Route Confirmed Action`** on `resolved_pending.action_type`
(`delete_email` / `send_email`), each to its own deterministic executor:

```js
// Execute Confirmed Delete
[callGmailMCP helper]
var item = $input.first().json;
var p = item.resolved_pending.payload;
var chatId = String(item.chat_id);
var result = await callGmailMCP('modify_email', { messageId: p.messageId, addLabelIds: ['TRASH'], removeLabelIds: ['INBOX'] });
var ok = !!(result && result.result && !result.error);
var text = ok
  ? 'Deleted: "' + p.subject + '" from ' + p.from + '. (Moved to Trash, recoverable for 30 days.)'
  : 'Something went wrong deleting that email — it may still be in your inbox. (' + JSON.stringify(result && result.error || 'unknown error') + ')';
return [{ json: { chat_id: chatId, reply_text: text, action_ok: ok } }];
```

```js
// Execute Confirmed Send
[callGmailMCP helper]
var item = $input.first().json;
var p = item.resolved_pending.payload;
var chatId = String(item.chat_id);
var args = { to: Array.isArray(p.to) ? p.to : [p.to], subject: p.subject, body: p.body };
if (p.threadId) args.threadId = p.threadId;
if (p.inReplyTo) args.inReplyTo = p.inReplyTo;
var result = await callGmailMCP('send_email', args);
var ok = !!(result && result.result && !result.error);
var text = ok
  ? 'Sent to ' + args.to.join(', ') + ': "' + p.subject + '".'
  : 'Something went wrong sending that email — it may not have gone out. (' + JSON.stringify(result && result.error || 'unknown error') + ')';
return [{ json: { chat_id: chatId, reply_text: text, action_ok: ok } }];
```

Both feed a Telegram send node, then unchanged into `Complete Task` (both explicitly set `chat_id`,
satisfying `Complete Task`'s existing fallback).

The result: **zero LLM calls, zero agent turns, on the entire path from "user taps Confirm" to
"email actually deleted/sent."** This is the turn that failure 2.5 happened on; under this design
there is no narration step left for a false "done" claim to come from — the reply text is built
directly from the real `callGmailMCP` result, so a failed call produces a reply that says it failed.

### 4.8 Nodes removed entirely

`Email Agent`, `Ollama — Email` (lmChatOllama, only existed to feed the agent), `Memory — Email`
(vectorStoreQdrant, only existed as the agent's memory tool), `Gmail MCP` (`mcpClientTool`,
agent-bound), `Request Confirmation` (`toolCode`), `Embeddings — Email`. `Extract Metadata`'s
`confirmation_just_requested` branch becomes dead code and is removed along with `Is Silent
Confirmation?` — there's no more agent narration on the ask turn to suppress, so that whole gate
disappears rather than needing to be extended to cover the execution turn too.

### 4.9 Verification plan before cutover

For each of the four pipelines, test against the real Gmail account and verify via direct MCP query
(not the bot's own reply text) — the same standard applied throughout this session:
1. `delete_email`: 0-match, 1-match (confirm → verify `in:trash`, cancel → verify still in inbox),
   many-match (verify disambiguation list, then resolve by number and by sender-name).
2. `send_email`: fresh compose (confirm → verify via `search_emails` finding the sent message,
   cancel → verify nothing sent), reply-to-thread (verify `to` resolves to the real original
   sender, not an invented address), missing-address case (verify `needs_clarification` fires
   instead of guessing).
3. `check_email`/`read_email` search: 0/1/many results, verify the "show full email" button uses
   the real message ID and triggers the existing `Handle Show Email` path correctly.
4. Direct-ID read: via the button flow end-to-end.
5. `cs.goal` dispatch accuracy: a batch of ~15-20 realistic messages spanning all four operations,
   confirming `Route Email Goal` sends each to the intended pipeline before relying on it live.

---

## Part 5 — As-built status (2026-07-05)

Implemented and live. All four pipelines verified against real Gmail state and real Telegram sends
(not the bot's own claims): delete (0/1/many-match, disambiguation, confirm, cancel — all checked via
direct `in:trash`/`in:inbox` queries), send (fresh compose verified in Sent folder, reply-to-thread
verified to resolve the real original sender rather than inventing an address, missing-address
clarification gate), check/read search (real search → real body fetch → accurate summary → button
built from the real ID), and direct-ID read (full button round-trip). `cs.goal` dispatch: 10/10 on a
realistic test batch.

Four categories of real bug were found and fixed during this testing pass, all now documented in
[[project_email_deterministic_pipeline]] in persistent memory for future reference:

1. n8n Switch node `fallbackOutput` only recognizes the literal string `"extra"` — any other label
   (I initially used descriptive names like `"none"`/`"many"`/`"check_email"`) silently drops
   unmatched items with no output and no error.
2. Telegram `parse_mode: 'HTML'` breaks on real `<email@domain>` sequences appearing in plain
   confirmation/result text (not just LLM output) — fixed by dropping `parse_mode` from all the new
   plain-text sends, none of which needed formatting.
3. `Load Memory Context` prepends conversation history onto `message_text` for LLM context, but any
   *deterministic* pattern match against message content (not fed to an LLM) must use
   `_original_message_text` instead, or anchored regexes silently stop matching once any history
   exists. Hit three places (dispatch bypass, reply-cue detection, ID-extraction regex).
4. Several new nodes referenced `$('Load Project State')` unconditionally, which throws (not
   returns undefined) when that node didn't run on a given path — specifically the confirm-resolution
   and disambiguation-resolution shortcuts, which skip the whole intent-classification chain. Fixed
   with guarded fallback chains to nodes that reliably did run on those paths.

No data was lost in the process — every mid-testing failure was a downstream notification/bookkeeping
crash *after* the actual Gmail mutation had already succeeded and been independently verified; nothing
silently failed to execute or executed incorrectly.
