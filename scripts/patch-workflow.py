#!/usr/bin/env python3
"""Patch the Telegram AI Assistant n8n workflow to add doc routing via Telegram inline buttons."""

import json, sys

SRC = '/mnt/data/projects/telegram-agent/n8n/exported-workflows.json'
OUT = '/mnt/data/projects/telegram-agent/n8n/main-agent-patched.json'

with open(SRC) as f:
    wfs = json.load(f)

wf = next(w for w in wfs if w['name'] == 'Telegram AI Assistant')

nodes = wf['nodes']
connections = wf['connections']

# ── Helper ────────────────────────────────────────────────────────────────────

def find_node(name):
    return next(n for n in nodes if n['name'] == name)

# ── 1. Modify Send Doc → Send Routing Keyboard ────────────────────────────────

send_doc = find_node('Send Doc')
send_doc['name'] = 'Send Routing Keyboard'
send_doc['parameters']['jsCode'] = r"""
const https = require('https');

const chat_id = $('Handle Relay Result').item.json.chat_id;
const filePath = $('Handle Relay Result').item.json.filePath;
const reply = ($('Handle Relay Result').item.json.reply || '').trim();
const token = $env.TELEGRAM_BOT_TOKEN;

const filename = filePath.split('/').pop();
const description = reply.replace(/\[FILE:[^\]]+\]\s*$/, '').trim();
const text = (description ? description + '\n\n' : '') +
  '\u{1F4C4} "' + filename + '" is ready — where should I send it?';

const keyboard = {
  inline_keyboard: [[
    { text: '\u{1F4F1} Telegram', callback_data: 'doc_tg::' + filePath },
    { text: '\u{1F4E7} Email',    callback_data: 'doc_email::' + filePath },
    { text: '\u{1F4BE} Save only', callback_data: 'doc_save::' + filePath }
  ]]
};

const body = JSON.stringify({ chat_id, text, reply_markup: keyboard });

return new Promise((resolve, reject) => {
  const req = https.request({
    hostname: 'api.telegram.org',
    path: '/bot' + token + '/sendMessage',
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) }
  }, (res) => {
    let data = '';
    res.on('data', c => data += c);
    res.on('end', () => {
      try {
        const json = JSON.parse(data);
        if (!json.ok) return reject(new Error('Telegram error: ' + data));
        resolve([{ json: { sent: true, file: filePath, chat_id } }]);
      } catch(e) { reject(e); }
    });
  });
  req.on('error', reject);
  req.write(body);
  req.end();
});
""".strip()

# Update connections that referenced 'Send Doc'
if 'Send Doc' in connections:
    connections['Send Routing Keyboard'] = connections.pop('Send Doc')

for src, outs in connections.items():
    for type_key, output_list in outs.items():
        for targets in output_list:
            for t in targets:
                if t.get('node') == 'Send Doc':
                    t['node'] = 'Send Routing Keyboard'

# ── 2. Add Route Callback switch node ────────────────────────────────────────

route_callback = {
    "id": "rc000001-0000-4000-0000-000000000001",
    "name": "Route Callback",
    "type": "n8n-nodes-base.switch",
    "typeVersion": 3.2,
    "position": [60, 300],
    "parameters": {
        "rules": {
            "values": [
                {
                    "outputKey": "doc",
                    "renameOutput": True,
                    "conditions": {
                        "options": {
                            "version": 2,
                            "leftValue": "",
                            "caseSensitive": False,
                            "typeValidation": "loose"
                        },
                        "combinator": "and",
                        "conditions": [{
                            "id": "cond-doc-callback",
                            "operator": {"type": "string", "operation": "startsWith"},
                            "leftValue": "={{ $json.callback_query.data }}",
                            "rightValue": "doc_"
                        }]
                    }
                },
                {
                    "outputKey": "email",
                    "renameOutput": True,
                    "conditions": {
                        "options": {
                            "version": 2,
                            "leftValue": "",
                            "caseSensitive": False,
                            "typeValidation": "loose"
                        },
                        "combinator": "and",
                        "conditions": [{
                            "id": "cond-email-callback",
                            "operator": {"type": "string", "operation": "startsWith"},
                            "leftValue": "={{ $json.callback_query.data }}",
                            "rightValue": "email_"
                        }]
                    }
                }
            ]
        },
        "options": {"fallbackOutput": "extra"}
    }
}
nodes.append(route_callback)

# ── 3. Add Handle Doc Callback code node ──────────────────────────────────────

handle_doc_callback_code = r"""
const fs = require('fs');
const https = require('https');
const path = require('path');

const cq = $input.first().json.callback_query;
const data = cq?.data || '';
const callbackId = cq?.id;
const chatId = cq?.message?.chat?.id;
const token = $env.TELEGRAM_BOT_TOKEN;

const sepIdx = data.indexOf('::');
const action = sepIdx >= 0 ? data.substring(0, sepIdx) : data;
const filePath = sepIdx >= 0 ? data.substring(sepIdx + 2) : '';

if (action === 'doc_save') {
  return [{ json: { callbackId, chatId, ackText: '✓ Saved to /memory/tmp/' } }];
}

if (action === 'doc_email') {
  return [{ json: { callbackId, chatId, ackText: '\u{1F4E7} Email delivery not yet configured' } }];
}

if (action === 'doc_tg') {
  if (!filePath || !fs.existsSync(filePath)) {
    return [{ json: { callbackId, chatId, ackText: '⚠️ File not found' } }];
  }

  const fileContent = fs.readFileSync(filePath);
  const filename = path.basename(filePath);
  const boundary = '----TGBoundary' + Date.now();
  const CRLF = '\r\n';

  const part = (name, val) =>
    '--' + boundary + CRLF +
    'Content-Disposition: form-data; name="' + name + '"' + CRLF + CRLF + val + CRLF;

  const prologue = Buffer.from([
    part('chat_id', String(chatId)),
    '--' + boundary + CRLF +
    'Content-Disposition: form-data; name="document"; filename="' + filename + '"' + CRLF +
    'Content-Type: text/html; charset=utf-8' + CRLF + CRLF
  ].join(''));
  const epilogue = Buffer.from(CRLF + '--' + boundary + '--' + CRLF);
  const body = Buffer.concat([prologue, fileContent, epilogue]);

  return new Promise((resolve, reject) => {
    const req = https.request({
      hostname: 'api.telegram.org',
      path: '/bot' + token + '/sendDocument',
      method: 'POST',
      headers: {
        'Content-Type': 'multipart/form-data; boundary=' + boundary,
        'Content-Length': body.length
      },
      timeout: 30000
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try {
          const json = JSON.parse(d);
          if (!json.ok) return reject(new Error('Telegram: ' + d));
          // Clean up temp file after successful send
          try { fs.unlinkSync(filePath); } catch(e) {}
          resolve([{ json: { callbackId, chatId, ackText: '\u{1F4CE} Sent!' } }]);
        } catch(e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

return [{ json: { callbackId, chatId, ackText: 'Unknown callback' } }];
""".strip()

handle_doc_callback = {
    "id": "rc000002-0000-4000-0000-000000000002",
    "name": "Handle Doc Callback",
    "type": "n8n-nodes-base.code",
    "typeVersion": 2,
    "position": [300, 300],
    "parameters": {
        "jsCode": handle_doc_callback_code
    }
}
nodes.append(handle_doc_callback)

# ── 4. Add Answer Doc Callback HTTP Request node ───────────────────────────────

answer_doc_callback = {
    "id": "rc000003-0000-4000-0000-000000000003",
    "name": "Answer Doc Callback",
    "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4.2,
    "position": [540, 300],
    "parameters": {
        "url": "=https://api.telegram.org/bot{{ $env.TELEGRAM_BOT_TOKEN }}/answerCallbackQuery",
        "method": "POST",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": '={{ JSON.stringify({ callback_query_id: $json.callbackId, text: $json.ackText, show_alert: false }) }}',
        "options": {}
    }
}
nodes.append(answer_doc_callback)

# ── 5. Update connections ─────────────────────────────────────────────────────

# Is Callback? YES (output 0) used to go to Handle Email Callback.
# Change it to Route Callback.
is_cb_conns = connections['Is Callback?']['main']
# output 0 = YES branch (first list)
is_cb_conns[0] = [{"node": "Route Callback", "type": "main", "index": 0}]

# Route Callback: output 0 (doc) → Handle Doc Callback
#                 output 1 (email) → Handle Email Callback
connections['Route Callback'] = {
    "main": [
        [{"node": "Handle Doc Callback", "type": "main", "index": 0}],
        [{"node": "Handle Email Callback", "type": "main", "index": 0}]
    ]
}

# Handle Doc Callback → Answer Doc Callback
connections['Handle Doc Callback'] = {
    "main": [
        [{"node": "Answer Doc Callback", "type": "main", "index": 0}]
    ]
}

# ── 6. Write output ───────────────────────────────────────────────────────────

with open(OUT, 'w') as f:
    json.dump(wf, f, indent=2)

print(f'Written: {OUT}')
print(f'Total nodes: {len(nodes)}')
