"""Materialize a local Codex marketplace for this installation; does not install it."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'integrations/codex-plugin'


def main():
    # Windows command hooks embed a literal PowerShell string. Restrict metacharacters
    # instead of producing a potentially ambiguous shell command for these paths.
    path = ROOT.as_posix()
    if any(char in path for char in "'\"`$\n\r"):
        raise SystemExit('Choose a project folder without quote, backtick, dollar or newline characters.')
    python = ROOT / '.venv/Scripts/python.exe'
    if not python.is_file():
        raise SystemExit('Create the project .venv before preparing the plugin.')
    template = PLUGIN / 'template'
    output = PLUGIN / 'generated'
    if output.exists():
        raise SystemExit('Generated marketplace already exists; remove it explicitly before regenerating.')
    for source in template.rglob('*'):
        if not source.is_file():
            continue
        target = output / source.relative_to(template)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding='utf-8').replace('__PROJECT_ROOT__', path)
        if target.suffix == '.json':
            data = json.loads(text)
            if target.name == 'hooks.json':
                for groups in data['hooks'].values():
                    for group in groups:
                        for hook in group['hooks']:
                            # Use the canonical Windows hook command for both keys;
                            # generic shell interpolation of PLUGIN_ROOT is undefined.
                            hook['command'] = hook['commandWindows']
            text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
        target.write_text(text, encoding='utf-8')
    print('Local marketplace prepared at integrations/codex-plugin/generated; no plugin installed.')


if __name__ == '__main__':
    main()
