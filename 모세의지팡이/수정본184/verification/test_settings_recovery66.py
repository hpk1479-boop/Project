"""Current web recovery crosses the real Part1 guard, using temporary files only."""
import io
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import server, unified_live


@pytest.fixture
def controller(tmp_path, monkeypatch):
    owner = runpy.run_path(str(ROOT / 'Part1/live_control.py'))
    namespace = owner['load_special_settings'].__globals__
    path = tmp_path / 'special_settings.json'
    path.write_bytes(b'{broken')
    launch = Mock(return_value=(True, 'started'))
    monkeypatch.setitem(namespace, 'SPECIAL_SETTINGS_PATH', path)
    monkeypatch.setitem(namespace, 'start_program', launch)
    discover = lambda part=None: ['SPECIAL1', 'SPECIAL2']  # registry.list_presets(part) takes the Part name
    from strategy_recipe import registry
    # The controller and web UI must see the same synthetic registry. Keep the
    # corruption/recovery assertions independent of installed preset count.
    monkeypatch.setattr(registry, 'list_presets', discover)
    monkeypatch.setitem(namespace, 'discover_live_specials', discover)
    owner['discover_live_specials'] = discover
    owner['special_code_default_trigger'] = lambda name: '올존'
    monkeypatch.setattr(unified_live, 'control', lambda: owner)
    monkeypatch.setattr(unified_live, '_closing', False)
    return owner, path, launch


def http_post(path, data):
    handler = object.__new__(server.Handler)
    handler.server = SimpleNamespace(token='local-test', server_port=8763, last_seen=None)
    handler.path = path
    payload = json.dumps(data).encode('utf-8')
    handler.headers = {'X-Lab-Token': 'local-test', 'Content-Length': str(len(payload))}
    handler.rfile = io.BytesIO(payload)
    replies = []
    handler.send = lambda status, body: replies.append((status, body))
    handler.do_POST()
    return replies[-1]


@pytest.mark.parametrize('payload', [b'{broken', b'[]', b'{"specials":null}', b'\xff'])
def test_corrupt_file_preview_never_enables_or_writes_and_start_is_rejected(controller, payload):
    _, path, launch = controller
    path.write_bytes(payload)
    data = unified_live.specials()
    assert data['needs_recovery'] is True and data['settings_error']
    assert data['uses_code_defaults'] is False
    assert set(data['items']) == {'SPECIAL1', 'SPECIAL2'}
    assert all(row['enabled'] is False for row in data['items'].values())
    assert path.read_bytes() == payload
    code, body = http_post('/api/mo/live/start', {})
    assert code == 400 and '전략 설정 파일' in body['error']
    launch.assert_not_called()
    assert path.read_bytes() == payload


def selected_items():
    return {'SPECIAL1': {'enabled': False, 'trigger': None, 'time_filters': None},
            'SPECIAL2': {'enabled': True, 'trigger': '브레이커 올존', 'time_filters': None}}


@pytest.mark.parametrize('recover', [None, False, 1, 'true'])
def test_repair_requires_explicit_boolean_confirmation(controller, recover):
    _, path, launch = controller
    before = path.read_bytes()
    request = {'items': selected_items()}
    if recover is not None:
        request['recover'] = recover
    code, body = http_post('/api/mo/live/specials', request)
    assert code == 400 and '복구 저장' in body['error']
    assert path.read_bytes() == before
    launch.assert_not_called()


def test_confirmed_repair_saves_only_selected_strategies_then_start_uses_them(controller):
    owner, path, launch = controller
    code, body = http_post('/api/mo/live/specials', {'items': selected_items(), 'recover': True})
    assert code == 200 and body['ok']
    launch.assert_not_called()  # Saving is not starting.
    saved = owner['load_special_settings']()
    assert saved == selected_items()
    data = unified_live.specials()
    assert data['needs_recovery'] is False and data['settings_error'] is None
    assert data['items']['SPECIAL1']['enabled'] is False
    assert data['items']['SPECIAL2']['enabled'] is True
    code, body = http_post('/api/mo/live/start', {})
    assert code == 200 and body['ok']
    assert launch.call_args.kwargs['enabled_specials'] == {'SPECIAL2'}
    assert json.loads(launch.call_args.kwargs['special_triggers']) == {'SPECIAL2': '브레이커 올존'}
    assert json.loads(path.read_text('utf-8'))['specials'] == selected_items()


def test_first_run_without_file_keeps_existing_defaults(controller):
    _, path, _ = controller
    path.unlink()
    data = unified_live.specials()
    assert data['uses_code_defaults'] is True and data['needs_recovery'] is False
    assert all(row['enabled'] for row in data['items'].values())
    assert not path.exists()


def test_recovery_does_not_hide_unrelated_controller_errors(controller):
    owner, _, _ = controller
    def broken():
        raise RuntimeError('unrelated controller error')
    owner['load_special_settings'] = broken
    with pytest.raises(RuntimeError, match='unrelated'):
        unified_live.specials()


def test_actual_js_never_repairs_without_user_confirmation(tmp_path):
    node = shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
class Element {
    constructor() {
        this.value = ''; this.checked = false; this.textContent = ''; this.dataset = {};
        this.options = [{}, {}]; this.classes = new Set(); this.children = [];
        this.classList = {toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name),
            add:name=>this.classes.add(name), remove:name=>this.classes.delete(name)};
    }
    append(...children) {this.children.push(...children);}
    replaceChildren() {this.children = [];}
    setAttribute() {} addEventListener() {} querySelectorAll() {return [];}
}
const root=process.argv[1], html=fs.readFileSync(root+'/index.html','utf8');
const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const get=id=>elements.get(id), writes=[], rendered=[];
let allow=false, confirms=0, normal=false;
const selected={SPECIAL1:{enabled:false,trigger:null,time_filters:null},
                SPECIAL2:{enabled:true,trigger:'브레이커 올존',time_filters:null}};
const preview=()=>({items:normal?selected:{SPECIAL1:{enabled:false},SPECIAL2:{enabled:false}},
    trigger_choices:[], needs_recovery:!normal, settings_error:normal?null:'전략 설정 파일 손상'});
const context=vm.createContext({console,
    document:{body:{dataset:{}},querySelector:s=>get(s.slice(1))||null,querySelectorAll:()=>[],
        createElement:()=>new Element(),createTextNode:text=>({textContent:text})},
    window:{dispatchEvent(){},addEventListener(){},confirm:()=>{confirms++;return allow;}}, Option:Element,
    setInterval(){},setTimeout(){return 1;},clearTimeout(){},
    api:(route,body)=>{
        if(route.startsWith('mo/live/status'))return Promise.resolve({modules:{STAFF:{state:'대기'},ENGINE:{state:'대기'}},lines:[]});
        assert.equal(route,'mo/live/specials');
        if(body){writes.push(body);normal=true;return Promise.resolve({ok:true,message:'저장 완료'});}
        return Promise.resolve(preview());
    }
});
const run=code=>vm.runInContext(code,context);
context.rendered=rendered;context.selected=selected;
(async()=>{
    run(fs.readFileSync(root+'/unified.js','utf8'));
    await new Promise(resolve=>setImmediate(resolve));
    run('moSpecialCard=(name,row)=>{rendered.push({name,enabled:row.enabled});return {};}; moReadSpecialCards=()=>selected;');
    await run('moLivePage("settings"); moLiveSpecials()');
    assert(rendered.every(row=>row.enabled===false));assert.equal(writes.length,0);
    assert(get('live-settings-message').textContent.includes('시작을 차단'));
    assert.equal(get('live-specials-save').textContent,'선택한 전략으로 복구 저장');
    await run('moSaveSpecials()');
    assert.equal(confirms,1);assert.equal(writes.length,0);
    allow=true;await run('moSaveSpecials()');
    assert.equal(confirms,2);assert.equal(writes.length,1);
    assert.equal(writes[0].recover,true);assert.deepEqual(writes[0].items,selected);
    assert.equal(get('live-settings-message').textContent,'저장 완료');
    assert.equal(get('live-specials-save').textContent,'전략 설정 저장');
    await run('moSaveSpecials()');
    assert.equal(confirms,2);assert.equal(writes.length,2);assert(!('recover' in writes[1]));
    normal=false;get('live-specials-refresh').onclick();
    await new Promise(resolve=>setImmediate(resolve));
    assert(get('live-settings-message').textContent.includes('시작을 차단'));
    assert.equal(writes.length,2);
    console.log('recovery behavior PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run([node, '-e', script, str(ROOT / 'Part3/web')], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'recovery behavior PASS' in result.stdout
