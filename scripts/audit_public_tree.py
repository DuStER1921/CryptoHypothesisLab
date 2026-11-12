"""Review public Git-tracked files before pushing. No network or write access.

This is a defense-in-depth check, NOT a substitute for least-privilege access,
reviewing every diff, or dedicated secret scanning.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 512 * 1024
ALLOWED_EXTENSIONS = {'.py', '.md', '.toml', '.yml', '.yaml', '.txt', '.ipynb'}
DANGEROUS_NAMES = {'.env', '.env.local', '.env.production', 'credentials.json',
                   'service-account.json', 'token.json'}
BLOCKED_SEGMENTS = {'data', 'datasets', 'outputs', 'results', 'artifacts',
                    'cache', 'downloads', 'secrets', '__pycache__', '.venv'}
SENSITIVE_PATTERNS = [
    re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}'),
    re.compile(r'github_pat_[A-Za-z0-9_]{20,}'),
    re.compile(r'AIza[0-9A-Za-z_-]{20,}'),
    re.compile(r'sk-(?:proj-)?[A-Za-z0-9_-]{20,}'),
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    re.compile('drive\\.google\\.com/drive/' + 'folders/'),
]


def inspect_file(relpath: str, contents: bytes) -> tuple[str, ...]:
    path = Path(relpath)
    flags = []
    if (path.is_absolute() or '..' in path.parts or
            any(p.lower() in BLOCKED_SEGMENTS for p in path.parts)):
        flags.append('disallowed directory')
    if (path.name.lower() in DANGEROUS_NAMES or
            (path.suffix.lower() not in ALLOWED_EXTENSIONS and path.name != '.gitignore')):
        flags.append('disallowed file type/name')
    if len(contents) > MAX_BYTES:
        flags.append('oversized source file')
    if b'\x00' in contents:
        flags.append('binary content')
    if path.suffix.lower() == '.ipynb':
        if len(path.parts) != 2 or path.parts[0] != 'colab':
            flags.append('notebook outside approved colab folder')
        else:
            try:
                import json
                nb = json.loads(contents)
                cells = nb['cells']
                if (nb.get('nbformat') != 4 or type(cells) is not list
                        or any(cell.get('cell_type') not in ('code', 'markdown')
                               or cell.get('attachments')
                               or cell.get('outputs', [])
                               or cell.get('execution_count') is not None
                               for cell in cells)):
                    flags.append('notebook outputs/attachments or unsupported cells')
                if path.name in ('02_discover_offline.ipynb', '03_premove_offline.ipynb',
                                  '05_micro_warning_offline.ipynb','06_premove_v3_offline.ipynb',
                                  '07_premove_v31_offline.ipynb','08_trade_candidate_offline.ipynb','09_failed_auction_v2_offline.ipynb','10_systematic_discovery_offline.ipynb'):
                    code = '\n'.join(''.join(cell.get('source', []))
                                     for cell in cells if cell.get('cell_type') == 'code')
                    if ('drive.mount(' in code or 'authenticate_user(' in code
                            or 'GoogleDrivePinnedSource(' in code):
                        flags.append('research notebook attempts source/Drive access')
            except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                flags.append('invalid notebook JSON')
    text = contents.decode('utf-8', errors='replace')
    if any(pattern.search(text) for pattern in SENSITIVE_PATTERNS):
        flags.append('potential secret or private Drive URL')
    return tuple(flags)


def tracked_files() -> list[str]:
    try:
        cp = subprocess.run(['git', 'ls-files', '--cached', '-z'], cwd=PROJECT_ROOT,
                            capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError('tracked Git file inventory is required') from exc
    return [p.decode('utf-8') for p in cp.stdout.split(b'\x00') if p]


def main() -> int:
    paths = tracked_files()
    failed = []
    for name in paths:
        try:
            content = (PROJECT_ROOT / name).read_bytes()
        except OSError:
            failed.append((name, ('unreadable tracked file',)))
            continue
        problem = inspect_file(name, content)
        if problem:
            failed.append((name, problem))
    if failed:
        for name, reasons in failed:
            # No content or secret ever printed.
            print(f'REJECT: {name}: {", ".join(reasons)}')
        return 1
    print(f'Public file audit PASS: {len(paths)} tracked files; no obvious prohibited artifacts')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
