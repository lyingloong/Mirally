"""Initialize private OpenClaw config, or seed a native Mirally identity."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import shutil
import sys
from urllib.parse import urlsplit, urlunsplit

import yaml

try:
    from tools.configure_activity_agent import activity_patch
    from tools.configure_memory_agent import memory_patch
except ModuleNotFoundError:  # script execution sets sys.path to tools/
    from configure_activity_agent import activity_patch
    from configure_memory_agent import memory_patch

ROOT = Path(__file__).resolve().parents[1]

# Direct script execution must work even when the caller is outside the repo.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from mirally.runtime.workspace import resolve_workspace


def template_body(text: str) -> str:
    """Match the shipped starter after OpenClaw strips its doc frontmatter."""
    text = text.replace('\r\n', '\n').lstrip('\ufeff').strip()
    if text.startswith('---\n'):
        _, separator, body = text[4:].partition('\n---\n')
        if separator:
            return body.strip()
    return text


def seed_identity(workspace: Path) -> bool:
    """Fill an absent/blank/starter identity; preserve any authored identity."""
    workspace = workspace.expanduser().resolve()
    identity = workspace/'IDENTITY.md'
    starter = ROOT/'integrations/openclaw/node_modules/openclaw/docs/reference/templates/IDENTITY.md'
    if identity.exists():
        current = template_body(identity.read_text(encoding='utf-8'))
        if current and (not starter.is_file() or
                        current != template_body(starter.read_text(encoding='utf-8'))):
            print('Existing authored identity retained:', identity)
            return False
    content = (ROOT/'config/mirally/openclaw-workspace/IDENTITY.md').read_text(encoding='utf-8')
    workspace.mkdir(parents=True, exist_ok=True)
    avatar = workspace/'avatars/mirally.png'
    if not avatar.exists():
        avatar.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/'mirally/assets/mirally-128.png', avatar)
    identity.write_text(content, encoding='utf-8')
    print('Native Mirally identity initialized:', identity)
    # OpenClaw detects the authored identity and completes its bootstrap itself.
    # Do not rewrite SOUL/USER/AGENTS, remove bootstrap files, or touch memory.
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identity-workspace', type=Path,
                        help='Seed only the native identity in this workspace; leave config unchanged.')
    parser.add_argument('--workspace', type=Path,
                        help='Workspace for a new configuration; defaults to the Mirally user data directory. '
                             'Relative paths resolve against the product root; existing config is retained.')
    args = parser.parse_args()
    if args.identity_workspace is not None:
        seed_identity(resolve_workspace(ROOT, args.identity_workspace))
        return
    state = ROOT/'config/mirally/openclaw'
    destination = state/'openclaw.json'
    if destination.exists():
        print('Existing OpenClaw configuration retained; use the native config commands to change it.')
        return
    source = ROOT/'config/mirally/ufo/agents.yaml'
    if not source.is_file():
        raise SystemExit('Existing provider config not found. Use .\\run-openclaw.ps1 onboard for native setup.')
    provider = yaml.safe_load(source.read_text(encoding='utf-8'))['APP_AGENT']
    if provider.get('API_TYPE', '').lower() != 'openai':
        raise SystemExit('Current provider is not OpenAI-compatible; configure it through native onboarding.')
    parts = urlsplit(provider['API_BASE'])
    if parts.username or parts.password or parts.query or parts.fragment:
        raise SystemExit('Provider URL requires manual review before configuration.')
    path = parts.path.rstrip('/')
    if path.endswith('/chat/completions'):
        path = path[:-len('/chat/completions')]
    base_url = urlunsplit((parts.scheme, parts.netloc, path, '', ''))
    provider_key = provider.get('API_KEY', '')
    model = provider.get('API_MODEL', '')
    if not provider_key or not model:
        raise SystemExit('The existing provider is missing credentials or model ID.')
    config = json.loads((ROOT/'config/mirally/openclaw.template.json').read_text(encoding='utf-8'))
    # Activity knowledge is a shared OpenClaw capability.  Register the
    # product-owned local stdio MCP server. OpenClaw owns discovery and process
    # lifetime; the child opens the activity database only on a tool call.
    activity = activity_patch(config, ROOT)
    config.setdefault('mcp', {}).setdefault('servers', {}).update(activity['mcp']['servers'])
    config.setdefault('tools', {}).update(activity['tools'])
    config.setdefault('skills', {}).setdefault('load', {}).update(activity['skills']['load'])
    # Register additive memory support without replacing OpenClaw's memory
    # slot. Local memory maintenance defaults on; model synthesis is opt-in.
    memory = memory_patch(config, ROOT)
    config.setdefault('mcp', {}).setdefault('servers', {}).update(memory['mcp']['servers'])
    config.setdefault('tools', {}).update(memory['tools'])
    config.setdefault('skills', {}).setdefault('load', {}).update(memory['skills']['load'])
    for plugin_key, value in memory['plugins'].items():
        if isinstance(value, dict):
            config.setdefault('plugins', {}).setdefault(plugin_key, {}).update(value)
        else:
            config.setdefault('plugins', {})[plugin_key] = value
    try:
        workspace = resolve_workspace(ROOT, args.workspace)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    seed_identity(workspace)
    config['gateway']['auth']['token'] = secrets.token_urlsafe(32)
    config['agents']['defaults']['workspace'] = str(workspace)
    config['agents']['defaults']['model']['primary'] = 'mirally/'+model
    route = config['models']['providers']['mirally']
    route.update(baseUrl=base_url, apiKey=provider_key)
    # Keep the tested transport metadata when this is the template's model.
    # Unknown models retain the original generic onboarding behavior.
    route['models'] = [next((entry for entry in route['models'] if entry['id'] == model),
                            {'id': model, 'name': 'Configured Mirally provider',
                             'input': ['text', 'image'], 'reasoning': False})]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Private native OpenClaw configuration initialized. Credentials were not printed.')
    print('Workspace:', workspace)


if __name__ == '__main__':
    main()
