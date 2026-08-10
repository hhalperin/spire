/* The MCP Apps bridge.
 *
 * Hand-written against the postMessage protocol in the MCP Apps specification
 * (2026-01-26) rather than taking @modelcontextprotocol/ext-apps. The spec is
 * explicit that the SDK is a convenience and not a requirement, and a hand-rolled
 * bridge is what lets the whole client stay a single dependency-free HTML file —
 * which is also what lets the CSP declare no external origins at all.
 *
 * Sequence:
 *   app  -> host   ui/initialize                  (request)
 *   host -> app    result { hostContext, ... }
 *   app  -> host   ui/notifications/initialized   (notification)
 *   app  -> host   tools/call                     (request, any time after)
 *   host -> app    ui/notifications/tool-result   (pushed, unprompted)
 *
 * Standalone mode matters as much as hosted mode. Opened directly in a browser
 * there is no parent to talk to, so the bridge resolves `connect()` against a
 * local adapter instead. That is how tools/host and the screenshot harness drive
 * the real client rather than a mock of it.
 */

export const PROTOCOL_VERSION = '2026-01-26';

const RPC = '2.0';

export class Bridge {
  constructor(options = {}) {
    this.name = options.name || 'Spire';
    this.version = options.version || '0.3.0';
    this.pending = new Map();
    this.nextId = 1;
    this.hostContext = null;
    this.hostCapabilities = null;
    this.connected = false;
    this.hosted = typeof window !== 'undefined' && window.parent && window.parent !== window;

    /* Consumers assign these. */
    this.ontoolresult = null;
    this.ontoolinput = null;
    this.oncontextchange = null;
    this.onteardown = null;

    /* Standalone escape hatch: a function (name, args) -> Promise<CallToolResult>. */
    this.localAdapter = options.localAdapter || null;

    this._onMessage = this._onMessage.bind(this);
    if (typeof window !== 'undefined') window.addEventListener('message', this._onMessage);
  }

  /* ------------------------------------------------------------ outbound -- */

  _post(payload) {
    if (!this.hosted) return;
    window.parent.postMessage(payload, '*');
  }

  _request(method, params) {
    if (!this.hosted) return Promise.reject(new Error('not hosted'));
    const id = this.nextId++;
    const promise = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      // A host that never answers must not hang the UI forever.
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`${method} timed out`));
        }
      }, 30000);
    });
    this._post({ jsonrpc: RPC, id, method, params });
    return promise;
  }

  _notify(method, params) {
    this._post({ jsonrpc: RPC, method, params });
  }

  /* ------------------------------------------------------------- inbound -- */

  _onMessage(event) {
    const msg = event && event.data;
    if (!msg || msg.jsonrpc !== RPC) return;

    if (msg.id !== undefined && (msg.result !== undefined || msg.error !== undefined)) {
      const waiting = this.pending.get(msg.id);
      if (!waiting) return;
      this.pending.delete(msg.id);
      if (msg.error) waiting.reject(new Error(msg.error.message || 'host error'));
      else waiting.resolve(msg.result);
      return;
    }

    switch (msg.method) {
      case 'ui/notifications/tool-result':
        if (this.ontoolresult) this.ontoolresult(msg.params);
        break;
      case 'ui/notifications/tool-input':
        if (this.ontoolinput) this.ontoolinput(msg.params);
        break;
      case 'ui/notifications/host-context-changed':
        this.hostContext = Object.assign({}, this.hostContext, msg.params);
        if (this.oncontextchange) this.oncontextchange(this.hostContext);
        break;
      case 'ui/resource-teardown':
        if (this.onteardown) this.onteardown(msg.params);
        break;
      default:
        break;
    }
  }

  /* ---------------------------------------------------------- public API -- */

  async connect() {
    if (!this.hosted) {
      this.connected = true;
      this.hostContext = { theme: null, displayMode: 'standalone' };
      return this.hostContext;
    }
    try {
      const result = await this._request('ui/initialize', {
        protocolVersion: PROTOCOL_VERSION,
        capabilities: {},
        clientInfo: { name: this.name, version: this.version },
        appCapabilities: { availableDisplayModes: ['inline', 'fullscreen'] },
      });
      this.hostCapabilities = (result && result.hostCapabilities) || {};
      this.hostContext = (result && result.hostContext) || {};
      this.connected = true;
      this._notify('ui/notifications/initialized', {});
      return this.hostContext;
    } catch (err) {
      // A host that does not speak the extension is not a crash: fall back to
      // standalone and keep rendering. Failing open is the house rule.
      this.hosted = false;
      this.connected = true;
      this.hostContext = { theme: null, displayMode: 'standalone', error: String(err) };
      return this.hostContext;
    }
  }

  async callServerTool(name, args) {
    if (!this.hosted) {
      if (!this.localAdapter) throw new Error('no host and no local adapter');
      return this.localAdapter(name, args || {});
    }
    return this._request('tools/call', { name, arguments: args || {} });
  }

  requestDisplayMode(mode) {
    if (this.hosted) this._notify('ui/request-display-mode', { mode });
  }

  /* Tell the host how tall we actually are, so the iframe stops guessing. */
  reportSize() {
    if (!this.hosted || typeof document === 'undefined') return;
    const el = document.documentElement;
    this._notify('ui/notifications/size-changed', {
      width: el.scrollWidth,
      height: Math.max(el.scrollHeight, document.body ? document.body.scrollHeight : 0),
    });
  }

  observeSize() {
    if (!this.hosted || typeof ResizeObserver === 'undefined') return;
    let queued = false;
    const observer = new ResizeObserver(() => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        this.reportSize();
      });
    });
    observer.observe(document.body);
  }

  /* Hand the model a one-line summary of what the player just did, so the
     conversation stays in sync with a UI it cannot see. */
  updateModelContext(text, structured) {
    if (!this.hosted) return;
    this._notify('ui/update-model-context', {
      content: [{ type: 'text', text }],
      structuredContent: structured || undefined,
    });
  }

  sendMessage(text) {
    if (this.hosted) this._notify('ui/message', { role: 'user', content: { type: 'text', text } });
  }
}

/* Pull the payload out of a CallToolResult. structuredContent is what the client
   renders; the text block is for hosts with no view. */
export function unwrap(result) {
  if (!result) return null;
  if (result.structuredContent !== undefined) return result.structuredContent;
  const block = (result.content || []).find((c) => c.type === 'text');
  if (!block) return null;
  try {
    return JSON.parse(block.text);
  } catch (_) {
    return null;
  }
}
