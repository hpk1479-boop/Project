"""175: the settings screen draws a 1-point price field for each symbol, in the real unified.js.

A symbol without a price shows an empty number field with an example; an untouched empty field is not
saved, and a typed price is saved with the group's save button.
"""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
from test_settings_layout68 import SCRIPT as SETTINGS_DOM

SCENARIO = r'''
const config={live:[{key:'SYMBOLS',value:'XAUUSD+,NAS100',secret:false,configured:null},
  {key:'POINT_XAUUSD+',value:'0.01',secret:false,configured:null},
  {key:'POINT_NAS100',value:'',secret:false,configured:null}],
 part2:{cores:null,overlap_trading_days:3},part2_cores:4,part2_tuned:null,
 connections:{warehouse:'synthetic warehouse',python_executable:''},ai:{provider:'disabled',timeout:90}};
const writes=[];
const context=vm.createContext({console,document,
 Option:class {constructor(text,value){const item=new Element('option');item.textContent=text;item.value=value;return item;}},
 window:{dispatchEvent(){},addEventListener(){},confirm:()=>true},Event:class{},
 setInterval:()=>1,setTimeout:fn=>{fn();return 1;},clearTimeout(){},
 api:async(route,body)=>{
  if(route==='mo/settings'&&body){writes.push(JSON.parse(JSON.stringify(body)));
   for(const [key,value] of Object.entries(body.changes))config.live.find(row=>row.key===key).value=value;
   return {ok:true,message:'설정이 저장되었습니다'};}
  if(route==='mo/settings')return JSON.parse(JSON.stringify(config));
  if(route.startsWith('mo/live/status'))return {modules:{},lines:[]};
  if(route.startsWith('ai/'))return {available:false,models:[]};
  throw Error('Unexpected API '+route);
 }});
const run=code=>vm.runInContext(code,context),get=id=>document.getElementById(id);
const field=key=>get('settings-live-fields').querySelector('[data-setting="'+key+'"]');
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));
 await run('moView("settings")');
 const nasdaq=field('POINT_NAS100'),gold=field('POINT_XAUUSD+');
 assert.equal(nasdaq.closest('fieldset').querySelector('legend').textContent,'지표');
 assert.equal(nasdaq.closest('details').id,'settings-live-details');
 assert.equal(nasdaq.closest('label').childNodes[0].textContent,'NAS100 1포인트 가격');
 assert.equal(nasdaq.type,'number');assert.equal(nasdaq.value,'');assert.equal(nasdaq.placeholder,'예: 0.01');
 assert.equal(gold.value,'0.01');
 await run('moSettingsSave("live")');
 assert.equal(writes.length,0);                                   // an untouched empty field is not saved
 nasdaq.value='0.01';await run('moSettingsSave("live")');
 assert.deepEqual(writes,[{group:'live',changes:{'POINT_NAS100':'0.01'}}]);
 await run('moView("strategy")');await run('moView("settings")');
 assert.equal(field('POINT_NAS100').value,'0.01');
 console.log('PASS');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


def test_each_symbol_has_a_point_field_that_saves_once_filled(tmp_path):
    script = SETTINGS_DOM.split('const liveKeys=')[0] + SCENARIO
    result = subprocess.run([shutil.which('node'), '-e', script, str(ROOT / 'Part3/web'), 'points'],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS' in result.stdout
