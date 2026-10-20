#!/usr/bin/env python3
"""Check release files or Git-tracked files; never print matched secret values."""
import json
import hashlib
from pathlib import Path
import re
import subprocess
import sys

PATTERNS = [
    r'github_pat_[A-Za-z0-9_]{20,}',
    r'gh[pousr]_[A-Za-z0-9]{20,}',
    r'sk-[A-Za-z0-9_-]{24,}',
    r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    r'AKIA[A-Z0-9]{16}',
    r'[?&]X-(?:Amz|Goog)-Signature=[a-fA-F0-9]{24,}',
    r'(?<!:)/Users/(?!you/|me/|<user>/)[A-Za-z0-9_.-]+/',
]
PRIVATE_DIRS = {'assets', 'episodes', 'reports', 'deliverables'}
SKIP = {'.git', 'node_modules', '__pycache__', '.venv'}


def reviewed_evidence(root, path):
    """Allow only explicitly listed, inspected documentation captures/results."""
    rel = path.relative_to(root).as_posix()
    if not rel.startswith('docs/evidence/') or path.suffix not in ('.png', '.mp4'):
        return False
    try:
        records = json.loads((root / 'docs/evidence/files.json').read_text())['files']
        record = records[rel]
        data = path.read_bytes()
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if record.get('privacy_review') != 'visually_inspected_public_sample':
        return False
    if hashlib.sha256(data).hexdigest() != record.get('sha256'):
        return False
    if path.suffix == '.png':
        return record.get('mime') == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n')
    return record.get('mime') == 'video/mp4' and len(data) >= 12 and data[4:8] == b'ftyp'


def check(root):
    root = Path(root).resolve()
    if (root / '.git').exists():
        result = subprocess.run(['git', 'ls-files', '-z'], cwd=root, check=True, capture_output=True)
        paths = [root / name for name in result.stdout.decode().split('\0') if name]
    else:
        paths = [p for p in root.rglob('*') if p.is_file() or p.is_symlink()]
    errors = []
    for path in paths:
        rel = path.relative_to(root)
        if any(part in SKIP for part in rel.parts):
            continue
        if rel.parts[0] in PRIVATE_DIRS or path.name in ('.env', 'jobs.json') or (path.name.startswith('.env.') and path.name != '.env.example'):
            errors.append({'path': str(rel), 'rule': 'private_runtime_file'})
            continue
        if path.is_symlink():
            if not path.resolve().is_relative_to(root):
                errors.append({'path': str(rel), 'rule': 'external_symlink'})
            continue
        if not path.is_file():
            continue
        try:
            data = path.read_text(encoding='utf-8-sig')
        except UnicodeDecodeError:
            if not reviewed_evidence(root, path):
                errors.append({'path': str(rel), 'rule': 'unreviewed_or_changed_binary'})
            continue
        for index, pattern in enumerate(PATTERNS):
            if re.search(pattern, data):
                errors.append({'path': str(rel), 'rule': 'content_' + str(index)})
    return {'passed': not errors, 'flagged_files': errors,
            'scope': 'deterministic checks of tracked or exported files; not comprehensive secret detection'}


if __name__ == '__main__':
    result = check(Path(__file__).resolve().parents[1])
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(not result['passed'])
