"""Protect one speech credential using the same owner policy as channel secrets."""
from __future__ import annotations

import json
import sys
from mirally.framework.channels import CredentialProtector


def main():
    request = json.loads(sys.stdin.read(32000))
    value = request.get('value')
    if not isinstance(value, str) or not value or len(value) > 16000:
        raise ValueError('invalid credential')
    protector = CredentialProtector()
    if request.get('operation') == 'protect':
        result = protector.protect(value)
    elif request.get('operation') == 'unprotect':
        result = protector.unprotect(value)
    else:
        raise ValueError('invalid operation')
    return {'ok': True, 'value': result}


if __name__ == '__main__':
    try:
        output = main()
    except Exception:
        output = {'ok': False}
    sys.stdout.write(json.dumps(output, ensure_ascii=False))
