"""Install the official OpenClaw packages with a project-local portable Node."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NODE_VERSION = 'v24.21.0'
NODE_ARCHIVE = f'node-{NODE_VERSION}-win-x64.zip'
NODE_ROOT = ROOT/'.artifacts/node'/f'node-{NODE_VERSION}-win-x64'


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mirally-setup'})
            with urllib.request.urlopen(request, timeout=45) as response, target.open('wb') as output:
                while chunk := response.read(1024*1024):
                    output.write(chunk)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


def main() -> None:
    if sys.platform != 'win32':
        raise SystemExit('This portable bootstrap targets Windows x64.')
    node = NODE_ROOT/'node.exe'
    if not node.is_file():
        print(f'Downloading official Node {NODE_VERSION}...', flush=True)
        cache = ROOT/'.artifacts/downloads'
        archive = cache/NODE_ARCHIVE
        checksums = cache/f'node-{NODE_VERSION}-SHASUMS256.txt'
        base = f'https://nodejs.org/dist/{NODE_VERSION}'
        download(base+'/SHASUMS256.txt', checksums)
        expected = next(line.split()[0] for line in checksums.read_text().splitlines()
                        if line.split()[-1] == NODE_ARCHIVE)
        if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            download(base+'/'+NODE_ARCHIVE, archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Official Node archive checksum mismatch.')
        extraction = NODE_ROOT.parent.resolve()
        extraction.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as package:
            for name in package.namelist():
                (extraction/name).resolve().relative_to(extraction)
            package.extractall(extraction)
    env = dict(os.environ)
    env['PATH'] = str(NODE_ROOT)+os.pathsep+env.get('PATH', '')
    env['npm_config_cache'] = str(ROOT/'.artifacts/npm-cache')
    env['npm_config_update_notifier'] = 'false'
    env.setdefault('npm_config_fetch_timeout', '60000')
    env.setdefault('npm_config_fetch_retries', '2')
    npm = NODE_ROOT/'node_modules/npm/bin/npm-cli.js'
    package = json.loads((ROOT/'integrations/openclaw/package.json').read_text(encoding='utf-8'))
    print('Installing pinned OpenClaw runtime and official Gateway client...', flush=True)
    lockfile = ROOT/'integrations/openclaw/package-lock.json'
    lock = json.loads(lockfile.read_text(encoding='utf-8')) if lockfile.exists() else {}
    locked_dependencies = lock.get('packages', {}).get('', {}).get('dependencies')
    install_action = 'ci' if locked_dependencies == package['dependencies'] else 'install'
    subprocess.run([str(node), str(npm), install_action, '--no-audit', '--no-fund'],
                   cwd=ROOT/'integrations/openclaw', env=env, check=True)
    # Keep version-specific install-script approvals in the public manifest.
    # An upgrade must review the new versions rather than approve them by name.
    scripts = []
    for spec, approved in package.get('allowScripts', {}).items():
        if not approved:
            continue
        name, expected_version = spec.rsplit('@', 1)
        installed = ROOT/'integrations/openclaw/node_modules'/name/'package.json'
        if not installed.is_file():
            continue
        actual = json.loads(installed.read_text(encoding='utf-8'))
        if actual['version'] != expected_version:
            raise SystemExit(f"Install-script policy needs review for {name}@{actual['version']}.")
        scripts.append(name)
    subprocess.run([str(node), str(npm), 'rebuild', *scripts],
                   cwd=ROOT/'integrations/openclaw', env=env, check=True)
    runtime = {'node': str(node.relative_to(ROOT)),
               'entry': 'integrations/openclaw/node_modules/openclaw/openclaw.mjs',
               'version': package['dependencies']['openclaw']}
    (ROOT/'.artifacts/openclaw-runtime.json').write_text(
        json.dumps(runtime, indent=2), encoding='utf-8')
    subprocess.run([str(node), str(ROOT/runtime['entry']), '--version'],
                   cwd=ROOT, env=env, check=True)
    print('Runtime ready. Start with .\\run-openclaw.ps1 --help', flush=True)


if __name__ == '__main__':
    main()
