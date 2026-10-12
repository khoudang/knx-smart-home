"""Check tracked publication files without printing matched credentials."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RULES = {
    'private-key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'provider-token': r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9]{20,})\b',
    'telegram-token': r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b',
    'google-document-link': r'docs\.google\.com/(?:spreadsheets|document|presentation)/d/[A-Za-z0-9_-]+',
    'google-drive-link': r'drive\.google\.com/(?:file/d/|drive/folders/)[A-Za-z0-9_-]+',
    'installation-ip': r'\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b',
    'service-account-email': r'[\w.+-]+@[\w.-]+\.iam\.gserviceaccount\.com',
}

def tracked_files():
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True)
    return [Path(p) for p in result.stdout.decode('utf-8').split('\0') if p]

def violations(relative, text):
    result = []
    name = relative.name
    if name.startswith('.env') and name != '.env.example':
        result.append('tracked-environment')
    if name in {'credentials.json', 'openclaw.json', 'agent.json', 'devices_cache.json',
                'permissions.json', 'user_quota.json', 'sheet_id.txt', 'knx_ip.txt',
                'homebridge_accessories.json', 'knx_config.json'}:
        result.append('tracked-runtime-config')
    if any(part in {'node_modules', '__pycache__', '.venv', 'logs', 'backups', 'secrets'} for part in relative.parts):
        result.append('tracked-runtime-directory')
    if name.endswith(('.db', '.sqlite', '.sqlite3', '.gz', '.zip', '.pem', '.key', '.jsonl')) or '.bak' in name or name.endswith('.bad'):
        result.append('tracked-backup-or-runtime')
    for label, pattern in RULES.items():
        if re.search(pattern, text):
            result.append(label)
    return result

def main():
    issues = []
    files = tracked_files()
    for relative in files:
        p = ROOT/relative
        if not p.is_file():
            issues.append((relative, 'missing-tracked-file'))
            continue
        try:
            text = p.read_text(encoding='utf-8')
        except UnicodeError:
            issues.append((relative, 'binary-file-requires-review'))
            continue
        issues.extend((relative, rule) for rule in violations(relative, text))
    for relative, rule in issues:
        print(f'{relative.as_posix()}: {rule}')
    print(f'Checked {len(files)} tracked files; {len(issues)} publication findings.')
    return 1 if issues else 0

if __name__ == '__main__':
    sys.exit(main())
