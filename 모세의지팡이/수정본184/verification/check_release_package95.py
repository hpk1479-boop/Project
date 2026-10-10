"""Verify this release's actual fresh-install artifact, including relocation."""
from pathlib import Path
import ast
import hashlib
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verify_current import portable_text

SMOKE = r'''
from pathlib import Path
import json, socket, sys
root=Path.cwd().resolve()
sys.path[:0]=[str(root),str(root/'Part1/program'),str(root/'Part2'),str(root/'Part3')]
def forbidden(*args,**kwargs):raise AssertionError('Release smoke must stay offline')
socket.socket.connect=forbidden
socket.socket.connect_ex=forbidden
socket.socket.sendto=forbidden
from strategy_recipe.registry import list_presets,entries
assert list_presets()
from event_application import create_event_engine
engine=create_event_engine({'SYMBOLS':'TEST','TELEGRAM_CHAT_ID':''},symbols=['TEST'],selection=['SPECIAL1'])
from event_backtest import runner,settings
assert not any(n=='lab' or n.startswith('lab.') or n=='Part3' for n in sys.modules)
assert settings.ROOT==root
from lab import server
assert server.ROOT==root/'Part3'
for doc in server.DOCS:assert (server.ROOT/doc).is_file(),doc
manual=server.ai_post('/api/ai/presets',{'research':True,'session':'release95'})
assert manual and not server.ai_settings().get('gemini_api_key')
server.close_ai_client()
assert (root/'runtime/llama.cpp/llama-server.exe').is_file()
assert (root/'Part1/program/MT5/THE_STAFF_OF_MOSES.ex5').is_file()
print(json.dumps({'part1_part2_independent':True,'manual_presets_unconfigured':True,
                  'ui_documents':len(server.DOCS),'presets':len(entries())}))
'''


def main():
    target = (ROOT / '배포/MOSES95').resolve()
    moved = (ROOT / '배포/relocation95/MOSES95').resolve()
    for path in (target, moved):
        if not path.is_relative_to(ROOT / '배포'):
            raise ValueError('Package relocation must stay within the distribution folder')
    if moved.exists():
        raise FileExistsError('Relocation destination already exists')
    manifest = json.loads((target / 'release_manifest.json').read_text('utf-8'))
    if manifest['version'] != 95 or manifest['purpose'] != 'fresh_install':
        raise ValueError('Unexpected package')
    details = {'files': len(manifest['files']), 'syntax_files': 0, 'hashes_match': True,
               'private_values_absent': True, 'personal_path_files': []}
    private_values = []
    for name, keys in [('settings/ai_settings.json', ('gemini_api_key',)),
                       ('Part2/backtest_ui.json', ('watch', 'chat_id'))]:
        value = json.loads((ROOT / name).read_text('utf-8'))
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        # BACKTEST is the synthetic replay recipient, also a public enum and
        # UI label. It is not a Telegram account identifier or credential.
        if name == 'Part2/backtest_ui.json' and value == 'BACKTEST':
            continue
        if isinstance(value, str) and len(value) >= 5:
            private_values.append(value.encode('utf-8'))
    text_suffixes = {'.py', '.pyw', '.json', '.md', '.txt', '.ps1', '.bat', '.cmd', '.js', '.html', '.mq5', '.mqh'}
    for name, metadata in manifest['files'].items():
        path = target / name
        if not path.resolve().is_relative_to(target):raise ValueError('Manifest path escape')
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == metadata['sha256'], name
        assert len(data) == metadata['bytes'], name
        if path.suffix in ('.py', '.pyw'):
            ast.parse(data.decode('utf-8-sig'), filename=name)
            details['syntax_files'] += 1
        if path.suffix.lower() in text_suffixes:
            assert not any(value in data for value in private_values), 'Private value in ' + name
            account = os.environ.get('USERNAME', '')
            if account and ('Users/' + account).encode() in data.replace(b'\\', b'/'):
                details['personal_path_files'].append(name)
    assert not details['personal_path_files'], 'Personal path in distribution'
    moved.parent.mkdir(parents=True)
    target.rename(moved)
    try:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        result = subprocess.run([sys.executable, '-B', '-c', SMOKE], cwd=moved, env=env,
                                capture_output=True, text=True, encoding='utf-8', timeout=90,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        details['relocation_smoke_returncode'] = result.returncode
        details['smoke_output'] = portable_text(result.stdout + result.stderr)
    finally:
        moved.rename(target)
        moved.parent.rmdir()
    destination = ROOT / '검증결과/fixes95/package_actual.json'
    destination.write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(details, ensure_ascii=False))
    return int(result.returncode != 0)


if __name__ == '__main__':
    raise SystemExit(main())
