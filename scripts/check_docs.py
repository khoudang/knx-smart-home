"""Check tracked Markdown links without contacting external services."""
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def main():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    issues = []
    count = 0
    for name in names:
        if not name.endswith('.md'):
            continue
        source = ROOT / name
        if not source.exists():
            continue
        count += 1
        text = source.read_text(encoding='utf-8')
        # Ignore fenced examples; only documentation links are checked here.
        text = re.sub(r'```.*?```', '', text, flags=re.S)
        for match in re.finditer(r'\]\(([^)\s]+)(?:\s+[^)]*)?\)', text):
            link = match.group(1).strip('<>')
            if re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*:', link):
                continue
            path = unquote(link.split('#', 1)[0])
            if path and not (source.parent / path).exists():
                line = text[:match.start()].count('\n') + 1
                issues.append(f'{name}:{line}: missing local target {path}')
    for issue in issues:
        print(issue)
    print(f'Checked links in {count} Markdown files; {len(issues)} broken local links.')
    return bool(issues)


if __name__ == '__main__':
    sys.exit(main())
