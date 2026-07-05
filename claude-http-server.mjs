import { spawn } from 'child_process';
import http from 'http';
import fs from 'fs';

const PORT = 3007;
const TIMEOUT_MS = 600_000;

// Create a minimal Claude home so the relay runs with no MCP servers.
// Credentials are symlinked from the real home so auth still works.
const REAL_HOME = process.env.HOME || '/root';
const RELAY_HOME = '/tmp/relay-home';
const RELAY_CLAUDE = `${RELAY_HOME}/.claude`;

try {
  fs.mkdirSync(RELAY_CLAUDE, { recursive: true });

  // Symlink credentials so Claude can authenticate
  const credSrc = `${REAL_HOME}/.claude/.credentials.json`;
  const credDst = `${RELAY_CLAUDE}/.credentials.json`;
  if (fs.existsSync(credSrc) && !fs.existsSync(credDst)) {
    fs.symlinkSync(credSrc, credDst);
  }

  // Minimal settings: broad read/write for relay paths, no mcpServers
  const relaySettings = {
    permissions: {
      allow: [
        'Read(**)',
        'Write(/memory/**)',
        'Write(/mnt/data/projects/**)',
        'Bash(mkdir *)'
      ]
    }
  };
  fs.writeFileSync(
    `${RELAY_CLAUDE}/settings.json`,
    JSON.stringify(relaySettings, null, 2)
  );
} catch (e) {
  process.stderr.write(`[relay-home setup] ${e.message}\n`);
}

http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200).end('ok');
    return;
  }

  if (req.method !== 'POST' || req.url !== '/claude') {
    res.writeHead(404).end('Not found');
    return;
  }

  let body = '';
  req.on('data', chunk => body += chunk);
  req.on('end', () => {
    let parsed;
    try {
      parsed = JSON.parse(body);
    } catch (e) {
      res.writeHead(400).end(JSON.stringify({ error: 'invalid JSON' }));
      return;
    }

    const { prompt, cwd = '/workspace', system } = parsed;
    if (!prompt) {
      res.writeHead(400).end(JSON.stringify({ error: 'prompt required' }));
      return;
    }

    const args = ['-p', prompt, '--output-format', 'text'];
    if (system) args.push('--system-prompt', system);

    const child = spawn('claude', args, {
      cwd,
      env: { ...process.env, HOME: RELAY_HOME },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    child.stdout.on('data', d => { stdout += d; });
    child.stderr.on('data', d => { stderr += d; });

    const timer = setTimeout(() => {
      child.kill('SIGTERM');
    }, TIMEOUT_MS);

    child.on('close', (code, signal) => {
      clearTimeout(timer);
      const output = stdout.trim() || stderr.trim() || '(no output)';
      const ok = code === 0 && signal == null;
      res.setHeader('Content-Type', 'application/json');
      res.writeHead(ok ? 200 : 500);
      res.end(JSON.stringify({ output, ok, exitCode: code, signal }));
    });

    child.on('error', err => {
      clearTimeout(timer);
      res.writeHead(500).end(JSON.stringify({ error: err.message }));
    });
  });
}).listen(PORT, '0.0.0.0', () => {
  process.stderr.write(`claude-http-server listening on :${PORT}\n`);
});
