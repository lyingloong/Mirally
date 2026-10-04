"""Run a real native agent file/command task and verify its generated artifacts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    runtime = json.loads((ROOT/'.artifacts/openclaw-runtime.json').read_text())
    state = ROOT/'config/openassist/openclaw'
    config = json.loads((state/'openclaw.json').read_text(encoding='utf-8'))
    workspace = Path(config['agents']['defaults']['workspace'])
    output = ROOT/'output/openclaw-native-verification'
    output.mkdir(parents=True, exist_ok=True)
    prompt = output/'request.txt'
    run_label = uuid4().hex[:12]
    marker = 'MIRALLY-NATIVE-'+run_label.upper()
    session_key = 'agent:main:mirally-native-file-check-'+run_label
    source = workspace/f'验证资料/{run_label}/研究会议纪要.md'
    artifact_dir = workspace/f'验证产物/{run_label}'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        f'# 研究会议纪要\n\n验证标记：{marker}\n会议日期：2026年10月3日。\n'
        '行动项：林晓于10月8日前完成中文文档摘要样例；周宁于10月10日前完成后台命令的产物核对；'
        '陈悦于10月12日前完成独立浏览器的下载测试。\n下次讨论：2026年10月13日14:30。\n', encoding='utf-8')
    prompt.write_text(
        f'请读取工作目录中的“验证资料/{run_label}/研究会议纪要.md”，完成一次后台文件处理测试。'
        f'不要打开桌面应用或操作鼠标。请在“验证产物/{run_label}/会议摘要.md”生成中文摘要，'
        '包括原文的验证标记、会议日期、三项行动项及负责人和截止日期、下次讨论时间。'
        '然后使用后台命令计算这个摘要文件的 SHA256，将实际哈希保存到'
        f'“验证产物/{run_label}/会议摘要.sha256”。请读取产物核对后返回文件路径。'
        '如果工具失败，请报告实际错误。此轮是临时验收，不进行身份出生流程。', encoding='utf-8')
    env = dict(os.environ)
    node = ROOT/runtime['node']
    env.update(OPENCLAW_STATE_DIR=str(state), OPENCLAW_CONFIG_PATH=str(state/'openclaw.json'))
    env['PATH'] = str(node.parent)+os.pathsep+env.get('PATH', '')
    result = subprocess.run([
        str(node), str(ROOT/runtime['entry']), 'agent', '--agent', 'main',
        '--session-key', session_key,
        '--message-file', str(prompt), '--json', '--timeout', '240',
    ], cwd=ROOT, env=env, text=True, encoding='utf-8', errors='replace',
       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300,
       creationflags=subprocess.CREATE_NO_WINDOW)
    secrets = [config['gateway']['auth']['token'],
               config['models']['providers']['mirally']['apiKey']]
    def redact(text):
        for secret in secrets:
            text = text.replace(secret, '[REDACTED]')
        return text
    (output/'response.json').write_text(redact(result.stdout), encoding='utf-8')
    (output/'diagnostics.txt').write_text(redact(result.stderr), encoding='utf-8')
    print('CLI exit code:', result.returncode)
    try:
        value = json.loads(result.stdout)
        print('Native result:', value.get('status', value.get('summary', 'received')))
        payload = value.get('result', value)
        for message in payload.get('payloads', []):
            if message.get('text'):
                print(redact(message['text'])[:1200])
    except ValueError:
        print('Native output saved for diagnosis.')
    summary = artifact_dir/'会议摘要.md'
    digest = artifact_dir/'会议摘要.sha256'
    checks = {'summary_exists': summary.is_file(), 'digest_exists': digest.is_file()}
    if summary.is_file():
        contents = summary.read_text(encoding='utf-8-sig')
        for term in [marker, '林晓', '周宁', '陈悦', '14:30']:
            checks['contains_'+term] = term in contents
    if summary.is_file() and digest.is_file():
        actual = hashlib.sha256(summary.read_bytes()).hexdigest()
        checks['sha256_matches'] = actual in digest.read_text(encoding='utf-8-sig').lower()
    report = {'sessionKey': session_key, 'marker': marker,
              'summary': str(summary), 'digest': str(digest), 'checks': checks}
    (output/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    if result.returncode != 0 or not all(checks.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
