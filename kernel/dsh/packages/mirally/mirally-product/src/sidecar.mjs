import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';

/** Owned JSONL request/reply process. Broken or timed-out transports reject pending calls. */
export class ProductSidecar {
  constructor({python, root, home, logger, bridgeUrl, bridgeToken, requestTimeoutMs = 30_000}) {
    Object.assign(this, {python, root, home, logger, bridgeUrl, bridgeToken, requestTimeoutMs});
    this.pending = new Map();
    this.sequence = 0;
    this.closed = false;
  }

  async start() {
    const args = ['-m', 'mirally.framework', '--root', this.root, '--home', this.home];
    if (this.bridgeUrl) args.push('--workers', '--bridge-url', this.bridgeUrl, '--bridge-token', this.bridgeToken);
    this.child = spawn(this.python, args, {
      cwd: this.root, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
      env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'},
    });
    this.lines = createInterface({input: this.child.stdout});
    this.lines.on('line', line => {
      if (line.length > 4 * 1024 * 1024) return this.fail(new Error('Product RPC frame exceeds its limit'));
      let frame;
      try { frame = JSON.parse(line); }
      catch { return this.fail(new Error('Product sidecar emitted invalid JSON')); }
      const pending = this.pending.get(String(frame.id));
      if (!pending) return;
      this.pending.delete(String(frame.id));
      clearTimeout(pending.timer);
      if (frame.error) {
        const error = new Error(frame.error.message ?? 'Product operation failed');
        error.code = frame.error.code ?? 'product_error';
        pending.reject(error);
      } else pending.resolve(frame.result);
    });
    this.child.stderr.setEncoding('utf8');
    this.child.stderr.on('data', data => this.logger.warn(`Product sidecar: ${data.slice(0, 4096).trim()}`));
    this.child.on('error', error => this.fail(error));
    this.child.on('exit', (code, signal) => this.fail(new Error(`Product sidecar exited (${code ?? signal})`)));
    return this.request('system.status', {});
  }

  request(method, params = {}) {
    if (this.closed || !this.child?.stdin.writable) return Promise.reject(new Error('Product sidecar is unavailable'));
    const id = String(++this.sequence);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Product RPC timed out: ${method}; outcome may be unknown`));
      }, this.requestTimeoutMs);
      this.pending.set(id, {resolve, reject, timer});
      this.child.stdin.write(JSON.stringify({id, method, params}) + '\n', 'utf8', error => {
        if (!error) return;
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      });
    });
  }

  fail(error) {
    this.closed = true;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
    this.onFailure?.(error);
  }

  async stop() {
    const child = this.child;
    if (!child) return;
    this.closed = true;
    child.stdin.end();
    await new Promise(resolve => {
      if (child.exitCode !== null || child.signalCode !== null) return resolve();
      const timer = setTimeout(() => { child.kill(); resolve(); }, 70_000);
      child.once('exit', () => { clearTimeout(timer); resolve(); });
    });
    this.fail(new Error('Product sidecar stopped'));
    this.lines?.close();
  }
}
