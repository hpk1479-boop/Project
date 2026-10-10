"""Existing input layout: native arrow choices and manual editing only."""
from pathlib import Path
import importlib.util
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    source = Path(__file__).with_name('test_backtest_recovery70.py')
    spec = importlib.util.spec_from_file_location('symbol_fixture76', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._existing_dom_fixture()


SCRIPT = r'''
const symbols=scenario==='empty'?[]:['EURUSD','GOLDm','USTEC'];
let options={symbol:symbols[0]||'',symbols,start:'2026-09-01',end:'2026-10-01',mode:'BAR',
 specials:[],special_settings:{},watch_text:'',watch_chat_id:'BACKTEST',spread_points:{},warehouse_set:true};
const calls=[];
const context=vm.createContext({console,document,Option,window:{dispatchEvent(){},addEventListener(){}},
 setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},api:async(name,body)=>{
  calls.push({name,body});
  if(name==='mo/backtest/options')return JSON.parse(JSON.stringify(options));
  if(name.startsWith('mo/live/status'))return {modules:{},lines:[]};
  throw Error('Unexpected API '+name);
 }});
const run=value=>vm.runInContext(value,context),get=id=>document.querySelector('#'+id);
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));
 await run('moView("backtest")');
 const select=get('mo-bt-symbol-select'),input=get('mo-bt-symbol');
 assert.deepEqual(select.options.map(o=>o.value),symbols);
 assert.equal(select.disabled,!symbols.length);assert.equal(input.disabled,false);
 assert.equal(input.getAttribute('list'),'mo-bt-symbols');
 assert.equal(input.closest('label').parentElement,get('mo-bt-start').closest('label').parentElement);
 assert.equal(input.closest('label').parentElement.children.filter(x=>x.tagName==='LABEL').length,4);
 assert.equal(document.querySelectorAll('[data-bt-symbol-slot]').length,0);
 if(scenario==='selection'){
  select.value='USTEC';select.onchange();assert.equal(input.value,'USTEC');assert.equal(select.value,'USTEC');
  assert.equal(run('moBacktestRequest().symbol'),'USTEC');
 }else if(scenario==='manual'||scenario==='empty'){
  input.value='OTHER.actual+';assert.equal(run('moBacktestRequest().symbol'),'OTHER.actual+');
 }else if(scenario==='navigation'){
  select.value='GOLDm';select.onchange();input.value='user.edited';
  await run('moView("live")');await run('moView("backtest")');assert.equal(input.value,'user.edited');
 }else if(scenario==='refresh'){
  options.symbols=['NEW.symbol'];await run('moBacktestOptions()');
  assert.deepEqual(select.options.map(o=>o.value),['NEW.symbol']);assert.equal(input.value,'EURUSD');
 }else if(scenario==='text'){
  options.symbols=['<img/onerror=bad()>'];await run('moBacktestOptions()');
  assert.match(select.textContent,/<img/);assert.equal(select.querySelectorAll('img').length,0);
 }
 assert(calls.every(call=>!call.name.includes('mt5')));
 console.log('PASS '+scenario);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['selection', 'manual', 'empty', 'navigation', 'refresh', 'text'])
def test_arrow_selection_and_direct_input(scenario):
    node = shutil.which('node')
    assert node
    result = subprocess.run([node, '-e', fixture() + SCRIPT, str(ROOT / 'Part3/web'), scenario],
        capture_output=True, text=True, encoding='utf-8', timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
