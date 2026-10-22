/** Execute only a preconfigured Skill script, with bounded output and process-tree cancellation. */
import {spawn} from 'node:child_process';
import {realpath, readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

/** Script code is trusted local code; authorization is not an operating-system sandbox. */
export async function runConfiguredScript(script, args, options) {
  if (!Array.isArray(args) || args.length > 100 || args.some(arg => typeof arg !== 'string' || arg.includes('\0'))
    || Buffer.byteLength(JSON.stringify(args), 'utf8') > 65_536) throw new Error('Script arguments must be a bounded string array');
  options.signal?.throwIfAborted();
  await options.authorize();
  options.signal?.throwIfAborted();
  options.assertActive();
  const canonical = await realpath(script.path);
  const hash = createHash('sha256').update(await readFile(canonical)).digest('hex');
  if (canonical !== script.path || hash !== script.sha256) throw new Error('The allowed script changed; review and configure the extension again');
  options.signal?.throwIfAborted();
  options.assertActive();
  const command = script.interpreter === 'node' ? process.execPath : options.python;
  if (typeof command !== 'string' || !command) throw new Error('Python interpreter is not configured');
  const env = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (/^(PATH|SYSTEMROOT|WINDIR|TEMP|TMP|HOME|USERPROFILE|COMSPEC|PATHEXT|LANG|LC_.*)$/i.test(key) && value !== undefined) env[key] = value;
  }
  Object.assign(env, {PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'}, script.env);
  return new Promise((resolve, reject) => {
    const child = spawn(command, [script.path, ...args], {
      cwd: script.workingDirectory, env, shell: false, windowsHide: true,
      detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'],
    });
    const stdout = [];
    const stderr = [];
    let bytes = 0;
    let status = 'finished';
    let killTask;
    let settled = false;
    const terminate = reason => {
      if (status !== 'finished' || settled) return;
      status = reason;
      if (!child.pid) return;
      if (process.platform === 'win32') {
        killTask = new Promise(done => {
          const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {windowsHide: true, stdio: 'ignore', shell: false});
          killer.once('error', () => { child.kill(); done(); });
          killer.once('close', done);
        });
      } else {
        try { process.kill(-child.pid, 'SIGKILL'); }
        catch (error) { if (error.code !== 'ESRCH') child.kill('SIGKILL'); }
      }
    };
    const collect = target => chunk => {
      const remaining = Math.max(0, script.maxOutputBytes - bytes);
      bytes += chunk.length;
      if (remaining > 0) target.push(chunk.subarray(0, remaining));
      if (bytes > script.maxOutputBytes) terminate('output_limit');
    };
    child.stdout.on('data', collect(stdout));
    child.stderr.on('data', collect(stderr));
    const abort = () => terminate('cancelled');
    options.signal?.addEventListener('abort', abort, {once: true});
    if (options.signal?.aborted) abort();
    const timeout = setTimeout(() => terminate('timeout'), script.timeoutMs);
    child.once('error', error => {
      settled = true;
      clearTimeout(timeout);
      options.signal?.removeEventListener('abort', abort);
      reject(new Error(`Could not start the allowed script: ${error.code ?? error.message}`));
    });
    child.once('close', async (code, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      options.signal?.removeEventListener('abort', abort);
      await killTask;
      resolve({
        status, exit_code: code, signal, stdout: Buffer.concat(stdout).toString('utf8'), stderr: Buffer.concat(stderr).toString('utf8'),
        truncated: bytes > script.maxOutputBytes,
        outcome: status === 'finished' ? code === 0 ? 'completed' : 'failed' : 'unknown',
        script_id: script.id, script_sha256: script.sha256,
      });
    });
  });
}
