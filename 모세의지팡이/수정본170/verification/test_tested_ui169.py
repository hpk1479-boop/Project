"""169: the shipped backtest list opens what a run tested under its row.

The page scripts (ai_display.js, unified.js, backtest_jobs.js) run on the shipped index.html with the
real Part3 answers for kept runs, a run from before 169, an older virtual entry form and a data build.
"""
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
from test_symbol_input76 import fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / name) for name in ('Part3', 'Part2', 'Part1/program')]
from lab import unified_backtest  # noqa: E402

unified_backtest._part2()
from event_backtest import runner  # noqa: E402
from event_backtest.settings import scenario  # noqa: E402
from event_backtest.tested_settings import FILE, tested_settings as keep  # noqa: E402
from strategy_recipe import registry  # noqa: E402

PERIOD = dict(symbol='XAUUSD+', start='2026-09-01', end='2026-10-01')
KEPT, OLD, FORM, BUILD = '1' * 32, '2' * 32, '3' * 32, '4' * 32


def answers(tmp_path):
    runs = {
        KEPT: (scenario(strategies=['SPECIAL2'], result_mode='VIRTUAL_ENTRY', triggers={'SPECIAL2': '무지성 브레이커 올존'},
                        special_time_filters={'SPECIAL2': {'MAIN_ASIA': {'enabled': True},
                                                           'MAIN_NEWYORK': {'enabled': True, 'start': '2200', 'end': '0100'}}},
                        spread_points={'XAUUSD+': 12}, **PERIOD), True),
        OLD: (scenario(strategies=['SPECIAL1'], **PERIOD), False),
        FORM: (scenario(strategies=['SPECIAL8'], result_mode='VIRTUAL_ENTRY', **PERIOD), False),
    }
    items, views = [], {}
    for identifier, (s, snapshot) in runs.items():
        config = runner.runtime_config(s)
        applied = runner.applied_strategy_settings(s, config)
        data = {'scenario': s, 'applied_special_settings': applied, 'wonbi_sigma': float(config['WONBI_SIGMA'])}
        if snapshot:
            data['tested_settings'] = keep(s, config, applied)
        if identifier == FORM:
            data['scenario'] = {**s, 'virtual_entry': {'schema': 1, 'mode': 'CONFIRM', 'tf': '1m', 'stop': {'kind': 'AUTO'}}}
        folder = tmp_path / 'runs' / identifier
        folder.mkdir(parents=True)
        (folder / 'result.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        items.append({'job_id': identifier, 'phase': 'complete', 'active': False, 'symbol': s['symbol'],
                      'start': s['start'], 'end': s['end'], 'strategies': s['strategies'], 'scenario': {'build_only': False}})
    # The recipe of the kept run was changed after it ran (the registry's read strategy files, 수정본170).
    changed = registry.builtin_entries()
    changed['SPECIAL2']['recipe']['strategy_intent']['final']['tfs'] = ['1m']
    with patch.object(registry, '_cached_builtin_entries', lambda: deepcopy(changed)):
        for identifier in runs:
            with patch.dict(unified_backtest.JOBS, {identifier: {'folder': tmp_path / 'runs' / identifier, 'scenario': {}}}):
                views[identifier] = unified_backtest.tested(identifier)
    assert views[KEPT]['strategies'][0]['now'] == 'changed'
    items.append({'job_id': BUILD, 'phase': 'complete', 'active': False, 'symbol': 'XAUUSD+', 'start': '2026-09-01',
                  'end': '2026-10-01', 'scenario': {'build_only': True}})
    return {'items': items, 'views': views}


SCRIPT = r'''
const answers=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const calls=[];let fail=scenario==='error';
const context=vm.createContext({console,document,Option,Event:class {constructor(type){this.type=type;}},
 window:{dispatchEvent(){},addEventListener(){},confirm:()=>true},sessionStorage:{getItem:()=>null,setItem(){}},
 setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},
 api:async route=>{
  calls.push(route);
  const name=route.split('?')[0];
  if(name==='mo/live/status')return {modules:{},lines:[]};
  if(name==='mo/backtest/options')return {symbol:'XAUUSD+',symbols:['XAUUSD+'],start:'2026-09-01',end:'2026-10-01',mode:'BAR',
   specials:[],special_settings:{},watch_text:'',watch_chat_id:'BACKTEST',spread_points:{},warehouse_set:true};
  if(name==='mo/backtest/recent')return {items:JSON.parse(JSON.stringify(answers.items)),warnings:[],leftovers:[]};
  if(name==='mo/backtest/tested'){
   if(fail){fail=false;throw Error('isolated failure');}
   return JSON.parse(JSON.stringify(answers.views[new URLSearchParams(route.split('?')[1]).get('id')]));
  }
  throw Error('Unmocked backend call: '+route);
 }});
const run=code=>vm.runInContext(code,context),get=id=>document.querySelector('#'+id);
for(const file of ['ai_display.js','unified.js','backtest_jobs.js'])run(fs.readFileSync(root+'/'+file,'utf8'));
const row=id=>get('bt-jobs-list').children.find(item=>item.textContent.includes(id));
const reads=id=>calls.filter(route=>route==='mo/backtest/tested?id='+id).length;
async function open(id,on=true){const details=row(id).querySelector('details');details.open=on;await details.ontoggle();return details;}
function lines(details){
 return Object.fromEntries(details.querySelectorAll('.moses-tested-line').map(line=>[line.children[0].textContent,line.children[1]]));
}
(async()=>{
 await run('window.MosesBacktestJobs.refresh()');
 const [kept,old,form,build]=[answers.items[0].job_id,answers.items[1].job_id,answers.items[2].job_id,answers.items[3].job_id];
 // The open button stays the only button of a row; a data build has nothing tested.
 for(const id of [kept,old,form,build])assert.equal(row(id).querySelectorAll('button').length,1);
 assert.equal(row(build).querySelector('details'),null);
 assert.equal(row(kept).querySelector('details summary').textContent,'당시 설정');
 assert.equal(reads(kept),0);
 if(scenario==='open'){
  const details=await open(kept),shown=lines(details);
  const head=details.querySelector('.moses-tested-head');
  assert.match(head.textContent,/^SPECIAL2 · 외부유동성 스윕 · Part1\/program\/SPECIAL/);
  assert.deepEqual(head.querySelectorAll('.moses-tested-note').map(note=>note.textContent),['지금 레시피와 다름']);
  assert.equal(shown['OZ 트리거'].textContent,'무지성 브레이커 올존');
  assert.equal(shown['거래 시간'].textContent,'아시아 08:00~12:00, 뉴욕 22:00~01:00');
  assert.match(shown['조건'].textContent,/무지성 브레이커/);
  assert.match(shown['가상진입'].textContent,/진입: .*손절: /);
  assert.equal(shown['스프레드'].textContent,'12포인트');
  assert.equal(shown['재생 모드'].textContent,'BAR');
  assert.equal(shown['올존 확인 시간봉'].textContent,'선택한 시간봉만');
  assert.equal(shown['원비 밴드 배수'].textContent,answers.views[kept].config.WONBI_SIGMA);
  assert.equal(shown['XAUUSD+ 1포인트 가격'].textContent,answers.views[kept].config['POINT_XAUUSD+']);
  assert.equal(shown['아시아 주요 거래시간'].textContent,answers.views[kept].config.MAIN_ASIA);
  // Read once: closing and opening again shows the same without asking again.
  await open(kept,false);await open(kept);assert.equal(reads(kept),1);
 }else if(scenario==='old'){
  const shown=lines(await open(old));
  assert.equal(shown['조건'].textContent,'기록 없음');assert.equal(shown['OZ 트리거'].textContent,answers.views[old].strategies[0].trigger);
  assert.equal(row(old).querySelector('.moses-tested-head').textContent,'SPECIAL1');
  assert.match(lines(await open(form))['가상진입'].textContent,/"schema":1/);
 }else if(scenario==='unreadable'){
  // Conditions the explanation cannot read: that line shows them as saved, the rest as usual.
  answers.views[kept].strategies[0].conditions={steps:5};
  const shown=lines(await open(kept));
  assert.equal(shown['조건'].textContent,'{"steps":5}');assert.equal(shown['OZ 트리거'].textContent,'무지성 브레이커 올존');
 }else if(scenario==='error'){
  const details=await open(kept);
  assert.equal(details.querySelector('.moses-tested').textContent,'당시 설정: isolated failure');
  await open(kept,false);await open(kept);
  assert.equal(reads(kept),2);assert.equal(lines(details)['OZ 트리거'].textContent,'무지성 브레이커 올존');
 }
 console.log('PASS '+scenario);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['open', 'old', 'unreadable', 'error'])
def test_tested_settings_open_under_the_row(scenario, tmp_path):
    path = tmp_path / 'answers.json'
    path.write_text(json.dumps(answers(tmp_path), ensure_ascii=False), encoding='utf-8')
    result = subprocess.run([shutil.which('node'), '-e', fixture() + SCRIPT, str(ROOT / 'Part3/web'), scenario, str(path)],
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS ' + scenario in result.stdout
