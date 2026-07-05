/**
 * Minimal SSE bridge: spawns a fresh stdio MCP child process for every
 * SSE connection, then tears it down when the connection closes.
 * This avoids the "Already connected to a transport" crash in supergateway.
 *
 * Usage: node mcp-sse-bridge.mjs <port> <command> [args...]
 *   e.g. node mcp-sse-bridge.mjs 3003 npx -y @gongrzhe/server-gmail-autoauth-mcp
 */

import http from "http";
import { spawn } from "child_process";
import { randomUUID } from "crypto";

const [, , portStr, ...cmd] = process.argv;
const PORT = parseInt(portStr, 10) || 3003;

const server = http.createServer((req, res) => {
  // CORS pre-flight
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }

  if (req.method === "GET" && req.url === "/sse") {
    const sessionId = randomUUID();

    // SSE headers
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
      "Access-Control-Allow-Origin": "*",
    });

    // Immediately send the endpoint URL so the client knows where to POST
    const host = req.headers.host || `localhost:${PORT}`;
    res.write(`event: endpoint\ndata: /message?sessionId=${sessionId}\n\n`);

    // Spawn a fresh child process for this connection
    const child = spawn(cmd[0], cmd.slice(1), {
      env: { ...process.env },
      stdio: ["pipe", "pipe", "pipe"],
    });

    child.stderr.on("data", (d) => process.stderr.write(d));

    // Child stdout → SSE events
    let buf = "";
    child.stdout.on("data", (chunk) => {
      buf += chunk.toString();
      const lines = buf.split("\n");
      buf = lines.pop(); // keep incomplete last line
      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed) {
          res.write(`event: message\ndata: ${trimmed}\n\n`);
        }
      }
    });

    child.on("exit", () => {
      try { res.end(); } catch {}
    });

    req.on("close", () => {
      try { child.kill(); } catch {}
    });

    // Store child keyed by sessionId for POST routing
    sessions.set(sessionId, child);
    req.on("close", () => sessions.delete(sessionId));

  } else if (req.method === "POST" && req.url?.startsWith("/message")) {
    const params = new URL(req.url, `http://localhost`).searchParams;
    const sessionId = params.get("sessionId");
    const child = sessions.get(sessionId);
    if (!child) { res.writeHead(404); res.end("session not found"); return; }

    let body = "";
    req.on("data", (d) => (body += d));
    req.on("end", () => {
      child.stdin.write(body + "\n");
      res.writeHead(202); res.end();
    });
  } else {
    res.writeHead(404); res.end();
  }
});

const sessions = new Map();

server.listen(PORT, "0.0.0.0", () => {
  console.log(`MCP SSE bridge listening on port ${PORT}`);
  console.log(`SSE endpoint: http://localhost:${PORT}/sse`);
  console.log(`POST messages: http://localhost:${PORT}/message?sessionId=<id>`);
  console.log(`Child command: ${cmd.join(" ")}`);
});
