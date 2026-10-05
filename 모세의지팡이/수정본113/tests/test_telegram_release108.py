"""Telegram field confirmation also works in the real source-free payload.

The real release preparation and HTTP handler run with isolated settings and
fake Telegram API replies. No installer or outbound connection is created.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / '통합설치'
sys.path.insert(0, str(TOOLS))
from releasekit import builder, resources
from releasekit.manuals import MANUAL_RELATIVE, manual_record, verify_manual
from releasekit.runtime_bundle import build_code_bundle

KEYS = ('TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_COMMAND_CHAT_IDS')
CODE = {'Part3/lab/telegram_connection.py', 'Part3/lab/unified_settings.py',
        'Part3/lab/server.py'}
ASSETS = ('Part3/web/unified.js', 'Part3/web/style.css', MANUAL_RELATIVE)

PROBE = r'''
import io, json, os, socket, sys, urllib.parse, urllib.request
from pathlib import Path
from types import SimpleNamespace
root, tooling, fixture, mode = map(str,sys.argv[1:5])
root, fixture = Path(root), Path(fixture)
def no_network(*args,**kwargs):
    raise AssertionError('Telegram release probe must never access a real network')
socket.create_connection = no_network
socket.socket.connect = socket.socket.connect_ex = socket.socket.sendto = no_network
sys.path[:0] = [tooling,str(root),str(root/'Part3'),str(root/'Part2'),str(root/'Part1/program')]
bundle = None
if mode=='bundle':
    from releasekit.runtime_bundle import RuntimeBundle
    bundle = RuntimeBundle(root).install()
try:
    from lab import telegram_connection, unified_settings as settings, server, unified_backtest
    from lab import storage
    from common_ai.settings import masked_settings
    fixture.mkdir(parents=True,exist_ok=True)
    config = fixture/'config.txt'
    config.write_text('TELEGRAM_TOKEN=\nTELEGRAM_CHAT_ID=\nTELEGRAM_COMMAND_CHAT_IDS=\nSYMBOLS=GOLD\n',encoding='utf-8')
    settings.LIVE_CONFIG = config
    calls, responses = [], []
    behavior = {'denied_channel':False}
    token = '987654321:'+'A'*35
    bad_token = '987654321:'+'B'*35

    class Reply:
        status = 200
        headers = {}
        def __init__(self,data):self.raw=json.dumps(data).encode('utf-8')
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self,*args):return self.raw
        def close(self):pass
        def getcode(self):return 200

    def fake_urlopen(request,*args,**kwargs):
        url = request.full_url if hasattr(request,'full_url') else str(request)
        parsed = urllib.parse.urlsplit(url)
        method = parsed.path.rsplit('/',1)[-1]
        arguments = dict(urllib.parse.parse_qsl(parsed.query))
        raw = getattr(request,'data',None)
        if raw:
            text = raw.decode('utf-8')
            try:arguments.update(json.loads(text))
            except ValueError:arguments.update(dict(urllib.parse.parse_qsl(text)))
        calls.append(method)
        if method=='getMe':
            if bad_token in url:
                return Reply({'ok':False,'error_code':401,'description':'Unauthorized'})
            result = {'id':987654321,'is_bot':True,'username':'offline_release_bot'}
        elif method=='getChat':
            chat = str(arguments['chat_id'])
            result = {'id':int(chat),'type':'channel' if chat.startswith('-100') else 'private',
                      'title':'offline release fixture'}
        elif method=='getChatMember':
            result = {'status':'member' if behavior['denied_channel'] else 'administrator',
                      'user':{'id':987654321,'is_bot':True},
                      'can_post_messages':not behavior['denied_channel']}
        else:raise AssertionError('Unexpected Telegram method: '+method)
        return Reply({'ok':True,'result':result})
    urllib.request.urlopen = fake_urlopen
    if hasattr(telegram_connection,'urlopen'):
        telegram_connection.urlopen = fake_urlopen

    def post(payload):
        raw = json.dumps(payload).encode('utf-8')
        handler = server.Handler.__new__(server.Handler)
        handler.path = '/api/mo/settings/telegram'
        handler.headers = {'Content-Length':str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.authorized = lambda:True
        captured = []
        handler.send = lambda status,body,*args:captured.append({'status':status,'body':body})
        handler.do_POST()
        assert len(captured)==1
        responses.append(captured[0])
        return captured[0]

    for key,value in (('TELEGRAM_TOKEN',token),('TELEGRAM_CHAT_ID','-10010801'),
                      ('TELEGRAM_COMMAND_CHAT_IDS','10802')):
        answer = post({'key':key,'value':value})
        assert answer['status']==200,answer
        assert answer['body']['key']==key and answer['body']['configured'] is True,answer
        assert value in config.read_text('utf-8'),key
    before = config.read_bytes()
    behavior['denied_channel'] = True
    denied_chat = post({'key':'TELEGRAM_CHAT_ID','value':'-10010899','token':token})
    assert denied_chat['status']==400 and config.read_bytes()==before,denied_chat
    behavior['denied_channel'] = False
    denied_token = post({'key':'TELEGRAM_TOKEN','value':bad_token})
    assert denied_token['status']==400 and config.read_bytes()==before,denied_token

    # Exercise the real secret masking without reading other personal settings.
    unified_backtest._part2 = lambda:(SimpleNamespace(settings=lambda:{}),None)
    storage.connections = lambda:{}
    settings._machine_roots = lambda:SimpleNamespace(load_live_root=lambda:None)
    server.ai_settings = lambda:{}
    visible = [row for row in settings.read()['live'] if row['key'].startswith('TELEGRAM_')]
    assert len(visible)==3 and all(row['value']=='' and row['configured'] for row in visible),visible
    rendered = json.dumps(responses+visible,ensure_ascii=False)
    assert all(secret not in rendered for secret in (token,bad_token,'-10010801','10802','-10010899'))
    print(json.dumps({'responses':responses,'masked':visible,'calls':calls,
        'failed_confirmation_preserved_config':config.read_bytes()==before,
        'physical_source_exists':os.path.isfile(root/'Part3/lab/telegram_connection.py')},ensure_ascii=False))
finally:
    if bundle is not None:bundle.uninstall()
'''


def probe(root,work,mode):
    answer = subprocess.run([sys.executable,'-I','-B','-X','utf8','-c',PROBE,
        str(root),str(TOOLS),str(work/mode),mode],cwd=work,capture_output=True,text=True,
        encoding='utf-8',errors='replace',timeout=90)
    assert answer.returncode==0,answer.stderr[-12000:]+answer.stdout[-2000:]
    return json.loads(answer.stdout.strip())


@pytest.fixture(scope='module')
def release(tmp_path_factory):
    work = tmp_path_factory.mktemp('telegram_release108')
    payload = work/'installed'
    config = ROOT/'Part1/program/config.txt'
    before = hashlib.sha256(config.read_bytes()).hexdigest()
    manual = manual_record(ROOT)
    selected = list(builder.source_paths(ROOT))
    assets = resources.discover_resources(ROOT,selected)
    code = builder.prepare_data(ROOT,payload,selected)
    verify_manual(payload,manual)
    compiled = build_code_bundle(ROOT,payload/'runtime/code.bundle',paths=code)
    resources.validate_payload(payload,assets,code)
    developer = probe(ROOT,work,'developer')
    installed = probe(payload,work,'bundle')
    assert hashlib.sha256(config.read_bytes()).hexdigest()==before
    return {'payload':payload,'code':code,'compiled':compiled,
        'developer':developer,'installed':installed}


def test_new_telegram_code_latest_ui_and_manual_are_in_current_source_free_payload(release):
    assert CODE<=set(release['code'])
    assert CODE<=set(release['compiled']['entries'])
    for relative in ASSETS:
        assert (release['payload']/relative).read_bytes()==(ROOT/relative).read_bytes()
    assert 'mo/settings/telegram' in (release['payload']/ASSETS[0]).read_text('utf-8')
    assert release['developer']['physical_source_exists'] is True
    assert release['installed']['physical_source_exists'] is False


def test_three_confirmations_and_failure_preservation_match_developer_without_network(release):
    installed,developer = release['installed'],release['developer']
    for key in ('responses','masked','calls','failed_confirmation_preserved_config'):
        assert installed[key]==developer[key],key
    assert [row['status'] for row in installed['responses']]==[200,200,200,400,400]
    assert set(installed['calls'])=={'getMe','getChat','getChatMember'}
    assert installed['failed_confirmation_preserved_config']


def test_release_initially_contains_no_developer_telegram_credentials_or_source(release):
    data = (release['payload']/'Part1/program/config.txt').read_text('utf-8-sig')
    for key in KEYS:
        values = re.findall(r'^\s*'+key+r'\s*=([^\r\n]*)$',data,re.M)
        assert values and all(not value.strip() for value in values),key
    assert not [path for path in release['payload'].rglob('*') if path.is_file() and path.suffix.lower() in {'.py','.pyw','.mq5','.mqh'}]
    assert not (release['payload']/'settings/strategy_visibility.json').exists()
