#!/usr/bin/env node
/**
 * MCP stdio server — exposes bash execution and Claude Code CLI (antigravity).
 * Spawned per-connection by mcp-sse-bridge.mjs; runs as root in the mcp-shell container.
 *
 * Tools:
 *   bash        — run any shell command with access to /mnt, /workspace, docker CLI, git, etc.
 *   claude_code — run `claude -p <prompt>` (print mode) in a given directory.
 */
import { spawnSync } from 'child_process';
import { createInterface } from 'readline';

const TOOLS = [
  {
    name: 'bash',
    description:
      'Execute a bash command inside the mcp-shell container. ' +
      'Has access to: /mnt (all project data), /workspace, git, python3, ' +
      'ripgrep, jq, curl, docker CLI (read-only inspection). ' +
      'SSH keys are at /root/.ssh. Timeout max 120 s.',
    inputSchema: {
      type: 'object',
      properties: {
        command: { type: 'string', description: 'Bash command to run' },
        cwd: {
          type: 'string',
          description: 'Working directory (default: /workspace)',
        },
        timeout: {
          type: 'number',
          description: 'Timeout in seconds (default 30, max 120)',
        },
      },
      required: ['command'],
    },
  },
  {
    name: 'claude_code',
    description:
      'Run the Claude Code CLI (antigravity) in non-interactive print mode. ' +
      'Give it a prompt describing a coding task or question; it will reason over ' +
      'the files in `cwd` and return its answer. Good for code review, explanation, ' +
      'refactoring suggestions, or generating a file.',
    inputSchema: {
      type: 'object',
      properties: {
        prompt: {
          type: 'string',
          description: 'Task or question for Claude Code',
        },
        cwd: {
          type: 'string',
          description:
            'Directory for Claude Code to operate in, e.g. /mnt/data/projects/my-repo',
        },
      },
      required: ['prompt'],
    },
  },
];

// ── JSON-RPC helpers ──────────────────────────────────────────────────────────

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

function reply(id, result) {
  send({ jsonrpc: '2.0', id, result });
}

function rpcError(id, code, message) {
  send({ jsonrpc: '2.0', id, error: { code, message } });
}

function ok(id, text, isError = false) {
  reply(id, { content: [{ type: 'text', text }], isError });
}

// ── Tool execution ────────────────────────────────────────────────────────────

function handleToolCall(id, { name, arguments: args }) {
  try {
    if (name === 'bash') {
      const { command, cwd = '/workspace', timeout = 30 } = args;
      const r = spawnSync('bash', ['-c', command], {
        cwd,
        timeout: Math.min(timeout, 120) * 1000,
        encoding: 'utf8',
        env: { ...process.env, TERM: 'xterm-256color' },
      });

      const stdout = r.stdout?.trim() || '';
      const stderr = r.stderr?.trim() || '';
      const parts = [];
      if (stdout) parts.push(stdout);
      if (stderr) parts.push(`--- stderr ---\n${stderr}`);
      const out = parts.join('\n') || '(no output)';

      const isError = r.status !== 0 || !!r.error;
      const prefix = isError ? `Exit ${r.status ?? 'ERR'}\n` : '';
      ok(id, prefix + out, isError);

    } else if (name === 'claude_code') {
      const { prompt, cwd = '/workspace' } = args;
      const r = spawnSync('claude', ['-p', prompt, '--no-color'], {
        cwd,
        timeout: 90_000,
        encoding: 'utf8',
        env: { ...process.env },
      });

      const out = r.stdout?.trim() || r.stderr?.trim() || '(no output)';
      const isError = r.status !== 0 || !!r.error;
      ok(id, out, isError);

    } else {
      rpcError(id, -32602, `Unknown tool: ${name}`);
    }
  } catch (e) {
    ok(id, `Internal error: ${e.message}`, true);
  }
}

// ── MCP message loop ──────────────────────────────────────────────────────────

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });

rl.on('line', (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;

  let req;
  try { req = JSON.parse(trimmed); } catch { return; }

  const { id, method, params } = req;

  switch (method) {
    case 'initialize':
      reply(id, {
        protocolVersion: '2024-11-05',
        capabilities: { tools: {} },
        serverInfo: { name: 'mcp-shell', version: '1.0.0' },
      });
      break;

    case 'notifications/initialized':
      break; // no response required

    case 'tools/list':
      reply(id, { tools: TOOLS });
      break;

    case 'tools/call':
      handleToolCall(id, params);
      break;

    default:
      if (id !== undefined) rpcError(id, -32601, `Method not found: ${method}`);
  }
});
