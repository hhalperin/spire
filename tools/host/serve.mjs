#!/usr/bin/env node
/* A minimal MCP Apps host, for developing and validating the client.
 *
 *   node tools/host/serve.mjs [--repo PATH] [--port 8931]
 *
 * Claude Code cannot render MCP Apps, and standing up Claude Desktop with a
 * tunnel just to look at a screen is a slow loop. This is the short one: it
 * speaks the *real* `ui/` postMessage dialect to the *real* bundled app and
 * proxies its `tools/call` requests to the *real* spire-mcp binary over stdio.
 * Nothing in the path is a mock, so if it works here the only thing left to
 * differ is the host's own chrome.
 *
 * It is also what tools/shoot.mjs drives for screenshots — which means the
 * screenshots are of the shipping client, not of a fixture that resembles it.
 */

import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { readFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..', '..');
const BINARY = join(ROOT, 'server', 'target', 'release', 'spire-mcp');

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
};

const PORT = Number(flag('port', '8931'));

/* A scratch repo so a demo never writes into the user's own save. */
function scratchRepo() {
  const dir = mkdtempSync(join(tmpdir(), 'spire-demo-'));
  const python = process.env.SPIRE_PYTHON || 'python3';
  const deck = join(ROOT, 'scripts', 'deck.py');
  const run = (argv) => {
    const result = spawnSync(python, [deck, ...argv, '--path', dir]);
    if (result.status !== 0) {
      throw new Error(`demo setup failed: ${argv.join(' ')}\n${result.stderr}`);
    }
  };

  // Mirror what /spire actually deals, so the deck facet has something to show.
  run(['init', '--class', 'defect']);
  for (const card of ['orient', 'run-tests', 'add-endpoint']) {
    run(['add-card', '--name', card, '--type', 'skill']);
  }
  for (const relic of ['ruff-strict', 'typed-public-api', 'no-mocks-in-prod']) {
    run(['add-relic', '--id', relic]);
  }
  run(['add-power', '--event', 'PostToolUse', '--name', 'ruff-on-edit']);
  return dir;
}


const REPO = flag('repo', null) || scratchRepo();

/* ------------------------------------------------------- the MCP session -- */

class McpSession {
  constructor() {
    this.proc = spawn(BINARY, [], {
      stdio: ['pipe', 'pipe', 'inherit'],
      env: {
        ...process.env,
        SPIRE_PLUGIN_ROOT: ROOT,
        SPIRE_PROJECT_DIR: REPO,
      },
    });
    this.nextId = 1;
    this.pending = new Map();
    this.buffer = '';
    this.proc.stdout.on('data', (chunk) => this.#onData(chunk));
    this.proc.on('exit', (code) => {
      console.error(`spire-mcp exited (${code})`);
      process.exit(code ?? 1);
    });
  }

  #onData(chunk) {
    this.buffer += chunk.toString();
    let index = this.buffer.indexOf('\n');
    while (index >= 0) {
      const line = this.buffer.slice(0, index).trim();
      this.buffer = this.buffer.slice(index + 1);
      if (line) {
        let message;
        try { message = JSON.parse(line); } catch { message = null; }
        if (message && message.id !== undefined && this.pending.has(message.id)) {
          const { resolve } = this.pending.get(message.id);
          this.pending.delete(message.id);
          resolve(message);
        }
      }
      index = this.buffer.indexOf('\n');
    }
  }

  request(method, params) {
    const id = this.nextId++;
    const promise = new Promise((resolve) => this.pending.set(id, { resolve }));
    this.proc.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
    return promise;
  }

  notify(method, params) {
    this.proc.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', method, params })}\n`);
  }

  async initialize() {
    const reply = await this.request('initialize', {
      protocolVersion: '2026-07-28',
      capabilities: {
        extensions: {
          'io.modelcontextprotocol/ui': { mimeTypes: ['text/html;profile=mcp-app'] },
        },
      },
      clientInfo: { name: 'spire-demo-host', version: '0.3.0' },
    });
    this.notify('notifications/initialized');
    return reply.result;
  }
}

const mcp = new McpSession();
const info = await mcp.initialize();

/* The host fetches the view from the server, exactly as a real one would. */
const read = await mcp.request('resources/read', { uri: 'ui://spire/app.html' });
const APP_HTML = read.result.contents[0].text;

/* ------------------------------------------------------------ the harness -- */

const HOST_HTML = readFileSync(join(HERE, 'host.html'), 'utf8')
  .replace('__SERVER_NAME__', info.serverInfo.name)
  .replace('__SERVER_VERSION__', info.serverInfo.version)
  .replace('__REPO__', REPO);

/* Two policies, because the two documents have opposite jobs.
 *
 * The view gets the deny-by-default policy a real host applies — this is the
 * check that the bundle is genuinely self-contained. If a stylesheet, a font or
 * a fetch ever creeps back in, it fails here rather than in someone's Claude
 * Desktop. The host page has to be allowed to frame it and to reach /rpc. */
const VIEW_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
  + "img-src data:; font-src data:; connect-src 'none'; frame-src 'none'";

const HOST_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
  + "connect-src 'self'; frame-src 'self'";

const send = (res, status, type, body, csp) => {
  res.writeHead(status, {
    'content-type': type,
    'cache-control': 'no-store',
    'content-security-policy': csp,
  });
  res.end(body);
};

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (url.pathname === '/') {
    return send(res, 200, 'text/html; charset=utf-8', HOST_HTML, HOST_CSP);
  }
  if (url.pathname === '/app.html') {
    return send(res, 200, 'text/html; charset=utf-8', APP_HTML, VIEW_CSP);
  }
  if (url.pathname === '/rpc' && req.method === 'POST') {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString() || '{}');
    const reply = await mcp.request('tools/call', {
      name: body.name,
      arguments: body.arguments || {},
    });
    res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
    return res.end(JSON.stringify(reply.result ?? { error: reply.error }));
  }
  res.writeHead(404);
  res.end('not found');
});

server.listen(PORT, () => {
  console.error(`spire demo host  →  http://localhost:${PORT}`);
  console.error(`  server : ${info.serverInfo.name} ${info.serverInfo.version}`);
  console.error(`  repo   : ${REPO}`);
  console.error('  ctrl-c to stop');
});
