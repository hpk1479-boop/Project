"""163: the first opening of the settings and backtest views shows one loading line instead of empty
fields until the view's data is drawn (settings: not waiting for a model list), and reading the settings
asks the system for its cores once."""
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from lab import server, storage, unified_backtest, unified_settings
from test_settings_layout68 import SCRIPT as SETTINGS_DOM
from test_symbol_input76 import fixture

HELD = r'''
const held=()=>{let release,fail;const promise=new Promise((ok,no)=>{release=ok;fail=no;});return {promise,release,fail};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
'''

SETTINGS = HELD + r'''
let load=held();const models=held();
const config={live:[],part2:{cores:4},connections:{warehouse:'synthetic warehouse',python_executable:''},
 ai:{provider:'gemini',gemini_model:'saved-gemini',gemini_api_key_configured:true,timeout:90}};
const context=vm.createContext({console,document,
 Option:class {constructor(text,value){const item=new Element('option');item.textContent=text;item.value=value;return item;}},
 window:{dispatchEvent(){},addEventListener(){}},Event:class{},setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},
 api:async(route,body)=>{
  if(route.startsWith('mo/live/status'))return {modules:{},lines:[]};
  if(route==='ai/gemini-models')return models.promise;
  assert.equal(route,'mo/settings');assert.equal(body,undefined);
  return load.promise;
 }});
const run=code=>vm.runInContext(code,context),view=()=>document.getElementById('view-settings');
const loading=()=>view().classList.contains('moses-view-loading');
const field=key=>document.getElementById('settings-ai-fields').querySelector('[data-setting="'+key+'"]');
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));
 const opening=run('moView("settings")');
 assert(!view().classList.contains('hidden'));assert(loading());
 if(scenario==='error'){
  load.fail(Error('설정 읽기 실패'));await opening;
  assert(!loading());assert.equal(document.getElementById('settings-message').textContent,'설정 읽기 실패');
  load=held();const again=run('moView("settings")');assert(loading());   // still never drawn
  load.release(JSON.parse(JSON.stringify(config)));await tick();assert(!loading());
  models.release({available:true,models:['saved-gemini']});await again;
 }else{
  load.release(JSON.parse(JSON.stringify(config)));await tick();
  assert(!loading());assert(field('gemini_model'));                     // drawn before the model list arrives
  models.release({available:true,models:['saved-gemini']});await opening;
  await run('moView("live")');load=held();
  const again=run('moView("settings")');assert(!loading());assert(field('gemini_model'));
  load.release(JSON.parse(JSON.stringify(config)));await again;assert(!loading());
 }
 console.log('PASS '+scenario);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''

BACKTEST = HELD + r'''
let options=held();
const value={symbol:'XAUUSD+',symbols:['XAUUSD+'],start:'2026-09-01',end:'2026-10-01',mode:'BAR',
 specials:[],special_settings:{},watch_text:'',watch_chat_id:'BACKTEST',spread_points:{},warehouse_set:true};
const context=vm.createContext({console,document,Option,window:{dispatchEvent(){},addEventListener(){}},
 setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},api:async name=>{
  if(name==='mo/backtest/options')return options.promise;
  if(name.startsWith('mo/live/status'))return {modules:{},lines:[]};
  throw Error('Unexpected API '+name);
 }});
const run=code=>vm.runInContext(code,context),view=()=>document.getElementById('view-backtest');
const loading=()=>view().classList.contains('moses-view-loading');
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));
 const opening=run('moView("backtest")');
 assert(!view().classList.contains('hidden'));assert(loading());
 if(scenario==='error'){
  options.fail(Error('창고 연결 실패'));await opening;
  assert(!loading());const error=document.getElementById('mo-bt-setup-error');
  assert.equal(error.textContent,'창고 연결 실패');assert(!error.classList.contains('hidden'));
 }else{
  options.release(JSON.parse(JSON.stringify(value)));await opening;
  assert(!loading());assert.equal(document.getElementById('mo-bt-symbol').value,'XAUUSD+');
  await run('moView("live")');options=held();
  const again=run('moView("backtest")');assert(!loading());
  options.release(JSON.parse(JSON.stringify(value)));await again;assert(!loading());
 }
 console.log('PASS '+scenario);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['drawn', 'error'])
def test_first_settings_opening_shows_one_loading_line_until_drawn(scenario, tmp_path):
    script = SETTINGS_DOM.split('const liveKeys=')[0] + SETTINGS
    result = subprocess.run([shutil.which('node'), '-e', script, str(ROOT / 'Part3/web'), scenario],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS ' + scenario in result.stdout


@pytest.mark.parametrize('scenario', ['drawn', 'error'])
def test_first_backtest_opening_shows_one_loading_line_until_drawn(scenario):
    result = subprocess.run([shutil.which('node'), '-e', fixture() + BACKTEST, str(ROOT / 'Part3/web'), scenario],
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS ' + scenario in result.stdout


def test_reading_the_settings_asks_for_the_cores_once(tmp_path, monkeypatch):
    (tmp_path / 'config.txt').write_text('SYMBOLS=XAUUSD+\n', encoding='utf-8')
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', tmp_path / 'config.txt')
    monkeypatch.setattr(unified_settings, 'ROOT', tmp_path)
    monkeypatch.setattr(unified_backtest, '_part2', lambda: (SimpleNamespace(settings=lambda: {}), None))
    monkeypatch.setattr(storage, 'connections', lambda: {})
    monkeypatch.setattr(server, 'ai_settings', lambda: {'provider': 'disabled'})
    monkeypatch.setattr(unified_settings, '_CORES', [])
    from event_backtest import system, worker_tuning
    # Without a measurement for this PC (수정본167) the field's default stays the physical cores.
    monkeypatch.setattr(worker_tuning, 'saved_profile', lambda config=None: None)
    asked = []
    monkeypatch.setattr(system, 'physical_cores', lambda: asked.append(1) or 6)
    assert [unified_settings.read()['part2_cores'] for _ in range(3)] == [6, 6, 6]
    assert len(asked) == 1
